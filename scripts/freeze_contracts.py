from __future__ import annotations

import argparse
from pathlib import Path

from sounio_stroke_lab.rewrite_contracts import build_contract_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the Python source-of-truth contracts for the F# rewrite lane.")
    parser.add_argument(
        "--output-root",
        default="contracts",
        help="Directory where OpenAPI, JSON schemas, golden fixtures, and the comparison registry will be written.",
    )
    args = parser.parse_args()
    written = build_contract_snapshot(Path(args.output_root))
    for key, value in sorted(written.items()):
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
