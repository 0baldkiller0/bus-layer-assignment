"""Run and summarize a small Freerouting corridor-penalty sweep."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _penalty_label(value: float) -> str:
    text = f"{value:g}".replace(".", "p")
    return f"p{text}"


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _load_summary(run_dir: Path, penalty: float) -> dict[str, object]:
    seed_summary = json.loads((run_dir / "seed_sweep_summary.json").read_text(encoding="utf-8"))
    route = seed_summary[0]
    adherence = json.loads((run_dir / "corridor_adherence_summary.json").read_text(encoding="utf-8"))
    by_method = adherence["by_method"]["greedy"]
    return {
        "penalty": penalty,
        "routed": route.get("routed"),
        "unrouted": route.get("unrouted"),
        "wirelength_mm": route.get("wirelength_mm"),
        "segments": route.get("segments"),
        "vias": route.get("vias"),
        "runtime_s": route.get("runtime_s", route.get("runtime_sec")),
        "inside_ratio": by_method.get("inside_ratio"),
        "inside_wirelength_mm": by_method.get("inside_wirelength"),
        "scored_wirelength_mm": by_method.get("total_wirelength"),
        "run_dir": str(run_dir),
    }


def _write_markdown(rows: list[dict[str, object]], path: Path) -> None:
    lines = [
        "# Corridor-aware penalty sweep",
        "",
        "| penalty | routed | unrouted | wirelength_mm | segments | vias | inside_ratio | runtime_s |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        wirelength = row.get("wirelength_mm")
        inside_ratio = row.get("inside_ratio")
        runtime = row.get("runtime_s")
        lines.append(
            "| {penalty:g} | {routed} | {unrouted} | {wirelength} | "
            "{segments} | {vias} | {inside_ratio} | {runtime} |".format(
                penalty=row["penalty"],
                routed=row.get("routed"),
                unrouted=row.get("unrouted"),
                wirelength=f"{wirelength:.2f}" if isinstance(wirelength, (int, float)) else "",
                segments=row.get("segments"),
                vias=row.get("vias"),
                inside_ratio=f"{inside_ratio:.6f}" if isinstance(inside_ratio, (int, float)) else "",
                runtime=f"{runtime:.2f}" if isinstance(runtime, (int, float)) else "",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--variant", default="16x4")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--penalties", type=float, nargs="+", default=[0, 0.5, 1, 2, 5])
    parser.add_argument("--corridor-margin", type=float, default=0.2)
    parser.add_argument("--corridor-mode", default="soft", choices=["soft", "hard", "tie_breaker"])
    parser.add_argument("--corridor-scale", type=float, default=1.0)
    parser.add_argument("--corridor-max-factor", type=float, default=1.0)
    parser.add_argument("--corridor-tie-break-weight", type=float, default=0.1)
    parser.add_argument("--case-labels", default="greedy")
    parser.add_argument("--conflict-model", default="corridor")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    base = Path(args.output_dir)
    if not base.is_absolute():
        base = root / base
    base.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    rows: list[dict[str, object]] = []
    for penalty in args.penalties:
        run_dir = base / _penalty_label(penalty)
        if not args.summarize_only:
            _run(
                [
                    sys.executable,
                    "benchmark/phase1_seed_sweep.py",
                    "--output-dir",
                    str(run_dir),
                    "--variants",
                    args.variant,
                    "--passes",
                    str(args.passes),
                    "--threads",
                    str(args.threads),
                    "--timeout",
                    str(args.timeout),
                    "--permutation",
                    "shuffle",
                    "--seeds",
                    str(args.seed),
                    "--case-labels",
                    args.case_labels,
                    "--conflict-model",
                    args.conflict_model,
                    "--router-corridor-penalty",
                    str(penalty),
                    "--router-corridor-margin",
                    str(args.corridor_margin),
                    "--router-corridor-mode",
                    args.corridor_mode,
                    "--router-corridor-scale",
                    str(args.corridor_scale),
                    "--router-corridor-max-factor",
                    str(args.corridor_max_factor),
                    "--router-corridor-tie-break-weight",
                    str(args.corridor_tie_break_weight),
                ],
                root,
                env,
            )
            _run(
                [
                    sys.executable,
                    "benchmark/routed_corridor_score.py",
                    "--run-dir",
                    str(run_dir),
                    "--output",
                    str(run_dir / "corridor_adherence_summary.json"),
                    "--markdown",
                    str(run_dir / "corridor_adherence_summary.md"),
                ],
                root,
            )
        rows.append(_load_summary(run_dir, penalty))

    (base / "penalty_sweep_summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    _write_markdown(rows, base / "penalty_sweep_summary.md")
    print(json.dumps(rows, indent=2), flush=True)


if __name__ == "__main__":
    main()
