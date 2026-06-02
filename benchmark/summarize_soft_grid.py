"""Combine soft corridor penalty sweep directories into one table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _max_factor_from_dir(path: Path) -> float:
    return float(path.name.split("_", 1)[1].replace("p", "."))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--baseline-inside-ratio", type=float, default=0.149255)
    args = parser.parse_args()

    base = Path(args.base_dir)
    rows = []
    for directory in sorted(base.glob("max_*")):
        summary_path = directory / "penalty_sweep_summary.json"
        if not summary_path.exists():
            continue
        max_factor = _max_factor_from_dir(directory)
        for row in json.loads(summary_path.read_text(encoding="utf-8")):
            item = dict(row)
            item["max_factor"] = max_factor
            rows.append(item)

    rows.sort(key=lambda item: (item["max_factor"], item["penalty"]))
    (base / "combined_summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    lines = [
        "# Soft corridor grid summary",
        "",
        "| max_factor | penalty | routed | unrouted | wirelength_mm | segments | inside_ratio | runtime_s |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {max_factor:.1f} | {penalty:g} | {routed} | {unrouted} | {wirelength_mm:.2f} | "
            "{segments} | {inside_ratio:.6f} | {runtime_s:.2f} |".format(**row)
        )
    (base / "combined_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("ALL")
    for row in rows:
        print(
            row["max_factor"],
            row["penalty"],
            row["routed"],
            row["unrouted"],
            row["wirelength_mm"],
            row["segments"],
            row["inside_ratio"],
        )
    print("CANDIDATES")
    for row in rows:
        if row["unrouted"] == 0 and row["inside_ratio"] > args.baseline_inside_ratio:
            print(
                row["max_factor"],
                row["penalty"],
                row["wirelength_mm"],
                row["segments"],
                row["inside_ratio"],
            )


if __name__ == "__main__":
    main()
