"""ComfyUI Server execution hook for 1986 Studios GCP Compute Broker.
Monitors all executed prompts globally and async-reports renders to the ledger.
"""

import os
import socket
import urllib.request
import json
import threading
import logging

logger = logging.getLogger("DigitBrokerHook")

DEFAULT_BROKER_URL = "http://10.155.1.39:5000/api/renders/log-execution"


def init_broker_hook():
    try:
        from server import PromptServer
    except ImportError:
        logger.warning("[DigitBrokerHook] server.PromptServer not found, skipping hook initialization.")
        return

    # Store reference to original send_sync
    _original_send_sync = PromptServer.send_sync

    def _patched_send_sync(self, event, data, sid=None):
        # Always run the original first to avoid delaying ComfyUI execution
        res = _original_send_sync(self, event, data, sid)

        try:
            if event == "execution_success":
                prompt_id = data.get("prompt_id")
                if prompt_id:
                    # Fetch prompt history asynchronously in a background thread
                    threading.Thread(
                        target=fetch_and_post_history,
                        args=(prompt_id,),
                        daemon=True
                    ).start()
        except Exception as e:
            logger.error(f"[DigitBrokerHook] Error in patched send_sync: {e}")

        return res

    # Inject the patch
    PromptServer.send_sync = _patched_send_sync
    if not os.environ.get("RENDER_HOOK_SECRET"):
        logger.warning(
            "[DigitBrokerHook] RENDER_HOOK_SECRET unset — broker will reject cost logging with 401."
        )
    logger.info("[DigitBrokerHook] Successfully initialized global execution monitor.")


def fetch_and_post_history(prompt_id):
    try:
        # Step 1: Query the local history API to get the executed graph
        history_url = f"http://127.0.0.1:8188/history/{prompt_id}"
        req = urllib.request.Request(history_url)
        with urllib.request.urlopen(req, timeout=5) as response:
            history_data = json.loads(response.read().decode("utf-8"))

        if not history_data or prompt_id not in history_data:
            logger.warning(f"[DigitBrokerHook] History for prompt {prompt_id} not found locally.")
            return

        # Step 2: Package the metadata
        prompt_info = history_data[prompt_id]
        
        payload = {
            "vm_name": socket.gethostname(),
            "prompt_id": prompt_id,
            "timestamp": new_timestamp(),
            "history": prompt_info
        }

        # Step 3: POST to broker with the same Bearer secret the broker requires.
        broker_url = os.environ.get("DIGIT_BROKER_URL", DEFAULT_BROKER_URL)
        headers = {"Content-Type": "application/json"}
        secret = os.environ.get("RENDER_HOOK_SECRET", "").strip()
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        broker_req = urllib.request.Request(
            broker_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(broker_req, timeout=5) as response:
            response.read()

        logger.info(f"[DigitBrokerHook] Successfully reported execution of prompt {prompt_id} to broker.")

    except Exception as e:
        logger.error(f"[DigitBrokerHook] Failed to fetch and post history for {prompt_id}: {e}")


def new_timestamp():
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"
