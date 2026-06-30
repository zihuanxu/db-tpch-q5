#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify successful benchmark rows agree on result_hash"
    )
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()

    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with args.csv_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") == "ok":
                groups[(row.get("region", ""), row.get("date", ""))].append(row)

    if not groups:
        print("no successful benchmark rows found")
        return 1

    failed = False
    for key, rows in sorted(groups.items()):
        hashes = {row.get("result_hash", "") for row in rows}
        if len(hashes) == 1:
            only_hash = next(iter(hashes))
            engines = ",".join(row.get("engine", "") for row in rows)
            print(f"ok {key[0]} {key[1]} hash={only_hash} engines={engines}")
            continue

        failed = True
        print(f"mismatch {key[0]} {key[1]}")
        for row in rows:
            print(f"  {row.get('engine')}: {row.get('result_hash')}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
