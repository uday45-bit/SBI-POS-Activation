#!/usr/bin/env python3
"""
Developer/admin command-line tool: rebuild the permanent reference-data CSV
files (data/*.csv) from two source files.

Usage:
    python scripts/build_reference_data.py \
        --master "Office Master.csv" \
        --devices "SBI POS machines Division wise details.xlsx"

All the actual extraction/reconciliation logic (and the data-quality notes
explaining the known conflicts it resolves) lives in src/reference_builder.py
so that both this CLI tool and the in-app "Admin: Refresh Reference Data"
panel in app.py stay perfectly in sync.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.reference_builder import build, DATA_DIR  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Rebuild POS activation reference-data CSVs.")
    parser.add_argument("--master", required=True, type=Path, help="Path to the Office Master file (.xlsx or .csv)")
    parser.add_argument("--devices", required=True, type=Path, help="Path to the Device Supply list (.xlsx or .csv)")
    parser.add_argument("--out", default=DATA_DIR, type=Path, help="Output directory (default: data/)")
    args = parser.parse_args()

    if not args.master.exists():
        sys.exit(f"Master file not found: {args.master}")
    if not args.devices.exists():
        sys.exit(f"Device supply file not found: {args.devices}")

    try:
        meta = build(args.master, args.devices, args.out)
    except ValueError as e:
        sys.exit(f"\nSTOPPED — data contradiction detected:\n{e}\n")

    print(json.dumps(meta, indent=2))
    print(f"\nReference data written to: {args.out}")


if __name__ == "__main__":
    main()
