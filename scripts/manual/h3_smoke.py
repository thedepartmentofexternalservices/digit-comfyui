#!/usr/bin/env python3
"""Manual smoke test for DIGIT MiniMax Video (fal / MUAPI text-to-video).

Usage:
  export FAL_KEY=...
  python scripts/manual/h3_smoke.py --provider fal

  export MUAPIAPP_API_KEY=...
  python scripts/manual/h3_smoke.py --provider muapi
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

from digit_loader import load_digit_module

payloads = load_digit_module("h3_payloads")
pricing = load_digit_module("h3_pricing")


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniMax H3 pricing/validation smoke test")
    parser.add_argument("--provider", choices=["fal", "muapi"], default="fal")
    parser.add_argument("--duration", type=int, default=5)
    args = parser.parse_args()

    env_key = "FAL_KEY" if args.provider == "fal" else "MUAPIAPP_API_KEY"
    if not os.environ.get(env_key):
        print(f"Missing {env_key}. Export it before running live generation.")
        return 1

    mode = payloads.validate_h3_request(
        prompt="A white kitten chases a butterfly across a sunlit garden.",
        provider=args.provider,
        resolution="2K",
        aspect_ratio="16:9",
        duration=str(args.duration),
    )
    print(f"Validation OK: mode={mode}")

    summary = pricing.estimate(
        args.provider,
        mode,
        "2K",
        args.duration,
        batch_count=1,
        use_live=True,
    )
    print(f"Estimate: ${summary.get('total')} — {summary.get('note') or 'ok'}")
    print("Live generation requires ComfyUI; this script validates config and pricing only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
