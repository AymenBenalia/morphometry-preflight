"""Command line entry point. Exit 0: no findings, 1: review, 2: blocked or invalid invocation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audit import DEFAULT_MAX_PIXELS, audit_manifest
from .report import write_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit calibration and combined ADS masks without changing inputs.")
    parser.add_argument("manifest", type=Path, help="CSV: image, optional mask and pixel_size_um; paths relative to CSV")
    parser.add_argument("--output", type=Path, required=True, help="Report folder outside the dataset directory")
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS, help="Maximum decoded pixels per file")
    args = parser.parse_args(argv)
    try:
        results = audit_manifest(args.manifest, max_pixels=args.max_pixels)
        document = write_reports(results, args.manifest, args.output, max_pixels=args.max_pixels)
    except (OSError, ValueError) as exc:
        print(f"Cannot audit: {exc}", file=sys.stderr)
        return 2
    summary = document["summary"]
    print(f'{len(results)} inputs: {summary["blocked"]} blocked, {summary["review"]} review, '
          f'{summary["no_findings"]} without findings. Report: {args.output / "report.html"}')
    return 2 if summary["blocked"] else 1 if summary["review"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
