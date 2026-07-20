"""ComfyUI Server execution hook for 1986 Studios GCP Compute Broker.
Monitors all executed prompts globally and async-reports renders to the ledger.

Enabled only when both DIGIT_BROKER_URL and RENDER_HOOK_SECRET are set.
Misconfiguration or a dead broker used to POST (and 401/timeout) on every
render; under a busy queue that looks like constant connecting/reconnecting.

On each execution_success the hook:
1. Fetches local prompt history (with short retries — history lags the event)
2. Scores billable nodes via render_pricing (cost + provider + model)
3. POSTs history + priced_nodes to /api/renders/log-execution
   The broker stamps user + project from the active lease on vm_name.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("DigitBrokerHook")

try:
    from . import render_pricing
except ImportError:  # pragma: no cover
    import render_pricing  # type: ignore

# Stop hammering a dead/misconfigured broker after this many consecutive failures.
_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN_SEC = 300
_HISTORY_RETRIES = 4
_HISTORY_RETRY_DELAY_SEC = 0.35

_hook_installed = False
_install_lock = threading.Lock()
_circuit_lock = threading.Lock()
_consecutive_failures = 0
_circuit_open_until = 0.0
_circuit_logged_open = False


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _broker_config() -> tuple[str, str] | None:
    """Return (url, secret) when the hook is fully configured; else None."""
    url = _env("DIGIT_BROKER_URL")
    secret = _env("RENDER_HOOK_SECRET")
    if not url or not secret:
        return None
    return url, secret


def _vm_name() -> str:
    """Prefer DIGIT_VM_NAME (ansible inventory hostname) over socket hostname.

    GCP hostnames sometimes include a domain suffix; leases use short names
    like comfyui-04. Override keeps attribution matching deterministic.
    """
    return _env("DIGIT_VM_NAME") or socket.gethostname()


def _circuit_allows() -> bool:
    with _circuit_lock:
        if time.monotonic() < _circuit_open_until:
            return False
        return True


def _record_success() -> None:
    global _consecutive_failures, _circuit_open_until, _circuit_logged_open
    with _circuit_lock:
        _consecutive_failures = 0
        _circuit_open_until = 0.0
        _circuit_logged_open = False


def _record_failure(reason: str) -> None:
    global _consecutive_failures, _circuit_open_until, _circuit_logged_open
    with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures < _FAILURE_THRESHOLD:
            logger.error(
                "[DigitBrokerHook] Broker report failed (%d/%d): %s",
                _consecutive_failures,
                _FAILURE_THRESHOLD,
                reason,
            )
            return

        _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SEC
        if not _circuit_logged_open:
            logger.error(
                "[DigitBrokerHook] Opening circuit for %ds after %d consecutive "
                "failures (last: %s). Cost logging paused until then.",
                _CIRCUIT_COOLDOWN_SEC,
                _consecutive_failures,
                reason,
            )
            _circuit_logged_open = True
        _consecutive_failures = 0


def _history_base_url() -> str:
    """Prefer the live PromptServer listen address; fall back to localhost:8188."""
    try:
        from server import PromptServer

        inst = getattr(PromptServer, "instance", None)
        if inst is not None:
            port = getattr(inst, "port", None) or 8188
            address = getattr(inst, "address", None) or "127.0.0.1"
            if address in ("0.0.0.0", "::", "[::]"):
                address = "127.0.0.1"
            return f"http://{address}:{port}"
    except Exception:
        pass
    return "http://127.0.0.1:8188"


def init_broker_hook():
    """Install the global execution monitor once, only when fully configured."""
    global _hook_installed

    config = _broker_config()
    if config is None:
        missing = []
        if not _env("DIGIT_BROKER_URL"):
            missing.append("DIGIT_BROKER_URL")
        if not _env("RENDER_HOOK_SECRET"):
            missing.append("RENDER_HOOK_SECRET")
        logger.info(
            "[DigitBrokerHook] %s unset — render reporting disabled, skipping hook.",
            " + ".join(missing),
        )
        return

    with _install_lock:
        if _hook_installed:
            return

        try:
            from server import PromptServer
        except ImportError:
            logger.warning(
                "[DigitBrokerHook] server.PromptServer not found, skipping hook initialization."
            )
            return

        existing = PromptServer.send_sync
        if getattr(existing, "_digit_broker_hook", False):
            _hook_installed = True
            return

        _original_send_sync = existing

        def _patched_send_sync(self, event, data, sid=None):
            # Always run the original first to avoid delaying ComfyUI execution.
            res = _original_send_sync(self, event, data, sid)

            try:
                if event == "execution_success":
                    prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
                    if prompt_id and _circuit_allows():
                        threading.Thread(
                            target=fetch_and_post_history,
                            args=(prompt_id,),
                            daemon=True,
                        ).start()
            except Exception as e:
                logger.error("[DigitBrokerHook] Error in patched send_sync: %s", e)

            return res

        _patched_send_sync._digit_broker_hook = True  # type: ignore[attr-defined]
        PromptServer.send_sync = _patched_send_sync
        _hook_installed = True
        logger.info(
            "[DigitBrokerHook] Successfully initialized global execution monitor "
            "(vm_name=%s).",
            _vm_name(),
        )


def _fetch_history(prompt_id: str) -> dict | None:
    """Fetch /history/{id}, retrying briefly if the entry is not ready yet."""
    history_url = f"{_history_base_url()}/history/{prompt_id}"
    last_err = None
    for attempt in range(_HISTORY_RETRIES):
        try:
            req = urllib.request.Request(history_url)
            with urllib.request.urlopen(req, timeout=5) as response:
                history_data = json.loads(response.read().decode("utf-8"))
            if history_data and prompt_id in history_data:
                return history_data[prompt_id]
            last_err = "missing entry"
        except Exception as e:
            last_err = str(e)
        if attempt + 1 < _HISTORY_RETRIES:
            time.sleep(_HISTORY_RETRY_DELAY_SEC * (attempt + 1))
    logger.warning(
        "[DigitBrokerHook] History for prompt %s not found locally (%s).",
        prompt_id,
        last_err,
    )
    return None


def fetch_and_post_history(prompt_id):
    config = _broker_config()
    if config is None:
        return
    if not _circuit_allows():
        return

    broker_url, secret = config

    try:
        prompt_info = _fetch_history(prompt_id)
        if prompt_info is None:
            return

        priced_nodes = render_pricing.price_execution(prompt_info)
        payload = {
            "vm_name": _vm_name(),
            "prompt_id": prompt_id,
            "timestamp": new_timestamp(),
            "history": prompt_info,
            "priced_nodes": priced_nodes,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        }
        broker_req = urllib.request.Request(
            broker_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(broker_req, timeout=5) as response:
            response.read()

        _record_success()
        logger.info(
            "[DigitBrokerHook] Reported prompt %s (%d billable node(s)) to broker.",
            prompt_id,
            len(priced_nodes),
        )

    except urllib.error.HTTPError as e:
        _record_failure(f"HTTP {e.code} from broker")
    except Exception as e:
        _record_failure(str(e))


def new_timestamp():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _reset_runtime_state_for_tests():
    """Test helper: clear install + circuit state between cases."""
    global _hook_installed, _consecutive_failures, _circuit_open_until, _circuit_logged_open
    with _install_lock:
        _hook_installed = False
    with _circuit_lock:
        _consecutive_failures = 0
        _circuit_open_until = 0.0
        _circuit_logged_open = False
