#!/usr/bin/env python3
"""Live end-to-end test for MiniMax H3 on fal and MUAPI.

Exercises text-to-video, image-to-video, and reference-to-video against real
APIs using a generated test image. Confirms payload field names and returns
downloadable video URLs.

Usage:
  export FAL_KEY=...
  export MUAPIAPP_API_KEY=...
  python scripts/manual/h3_integration_test.py

  # fal only, skip R2V (cheaper):
  python scripts/manual/h3_integration_test.py --provider fal --modes text_to_video,image_to_video

  # Save MP4s locally:
  python scripts/manual/h3_integration_test.py --output-dir /tmp/h3-live

Requires: pip install fal-client requests Pillow (already in requirements.txt)
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from h3_integration import (  # noqa: E402
    format_results_report,
    has_fal_key,
    has_muapi_key,
    run_live_suite,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live MiniMax H3 integration test (fal + MUAPI)"
    )
    parser.add_argument(
        "--provider",
        choices=["all", "fal", "muapi"],
        default="all",
        help="Which provider(s) to test",
    )
    parser.add_argument(
        "--modes",
        default="text_to_video,image_to_video,reference_to_video",
        help="Comma-separated modes to test",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=4,
        help="Clip duration in seconds (4-15; use 4 to minimize cost)",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional directory to download resulting MP4 files",
    )
    args = parser.parse_args()

    providers = ["fal", "muapi"] if args.provider == "all" else [args.provider]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    missing = []
    if "fal" in providers and not has_fal_key():
        missing.append("FAL_KEY")
    if "muapi" in providers and not has_muapi_key():
        missing.append("MUAPIAPP_API_KEY")
    if missing:
        print("Missing environment variables:", ", ".join(missing))
        print("Export keys before running live integration tests.")
        return 1

    output_dir = args.output_dir or None
    results = run_live_suite(
        providers=providers,
        modes=modes,
        duration=args.duration,
        output_dir=output_dir,
    )
    print(format_results_report(results))
    return 0 if all(r.success for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
