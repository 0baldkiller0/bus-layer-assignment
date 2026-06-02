"""
Run a small seed-only detailed-routing sweep on SMD high-conflict boards.

This is intended to test whether planner-side assignment quality transfers to
Freerouting metrics when assignments are expressed as same-net routing seeds.
"""

import argparse
import json
import os
import sys
from typing import Iterable, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from benchmark.phase1_high_conflict_pcb import generate
from benchmark.phase1_dummy_corridor_test import run_dummy_corridor_test


DEFAULT_VARIANTS = [
    (16, 4),
    (24, 6),
    (32, 8),
]


def _variant_name(net_count: int, bus_count: int, permutation: str, seed: int) -> str:
    suffix = f"_{permutation}"
    if permutation == "shuffle":
        suffix += f"_s{seed}"
    return f"hc_smd_n{net_count}_b{bus_count}{suffix}"


def _write_split_assignment(source_path: str, output_path: str) -> str:
    with open(source_path, "r", encoding="utf-8") as f:
        assignments = json.load(f)
    split = {"split_outer_inner": assignments["split_outer_inner"]}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(split, f, indent=2)
    return output_path


def _result_row(variant: str, entry: dict) -> dict:
    return {
        "variant": variant,
        "label": entry.get("label"),
        "proxy_cost": entry.get("proxy_cost"),
        "proxy_conflict": entry.get("proxy_conflict"),
        "proxy_crossing": entry.get("proxy_crossing"),
        "proxy_via_estimate": entry.get("proxy_via_estimate"),
        "wirelength_mm": entry.get("adjusted_wirelength_mm"),
        "segments": entry.get("adjusted_segments"),
        "vias": entry.get("adjusted_vias"),
        "routed": entry.get("adjusted_routed_nets"),
        "unrouted": entry.get("adjusted_unrouted_nets"),
        "runtime_sec": entry.get("real_runtime_sec"),
        "error": entry.get("error"),
    }


def _write_markdown(rows: list[dict], output_path: str) -> None:
    lines = [
        "# Phase 1 Seed-Only Sweep",
        "",
        "| Variant | Method | Proxy cost | Conflict | Crossing | Routed | Unrouted | Wirelength | Segments | Vias | Runtime |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['variant']} | {row['label']} | "
            f"{row.get('proxy_cost', '')} | {row.get('proxy_conflict', '')} | "
            f"{row.get('proxy_crossing', '')} | {row.get('routed', '')} | "
            f"{row.get('unrouted', '')} | {row.get('wirelength_mm', '')} | "
            f"{row.get('segments', '')} | {row.get('vias', '')} | "
            f"{row.get('runtime_sec', '')} |"
        )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_sweep(
    output_dir: str,
    variants: list[tuple[int, int]],
    passes: int,
    threads: int,
    timeout_seconds: int,
    permutation: str,
    seeds: list[int],
    feedback_seed_via_weight: float,
    case_labels: set[str],
    conflict_model: str,
    router_corridor_penalty: float,
    router_corridor_margin: float,
    router_corridor_mode: str,
    router_corridor_scale: float,
    router_corridor_max_factor: float,
    router_corridor_tie_break_weight: float = 0.1,
) -> list[dict]:
    os.makedirs(output_dir, exist_ok=True)
    rows = []

    for net_count, bus_count in variants:
        variant_seeds = seeds if permutation == "shuffle" else [seeds[0]]
        for seed in variant_seeds:
            name = _variant_name(net_count, bus_count, permutation, seed)
            rows.extend(_run_one_variant(
                output_dir=output_dir,
                name=name,
                net_count=net_count,
                bus_count=bus_count,
                passes=passes,
                threads=threads,
                timeout_seconds=timeout_seconds,
                permutation=permutation,
                seed=seed,
                feedback_seed_via_weight=feedback_seed_via_weight,
                case_labels=case_labels,
                conflict_model=conflict_model,
                router_corridor_penalty=router_corridor_penalty,
                router_corridor_margin=router_corridor_margin,
                router_corridor_mode=router_corridor_mode,
                router_corridor_scale=router_corridor_scale,
                router_corridor_max_factor=router_corridor_max_factor,
                router_corridor_tie_break_weight=router_corridor_tie_break_weight,
            ))

    json_path = os.path.join(output_dir, "seed_sweep_summary.json")
    md_path = os.path.join(output_dir, "seed_sweep_summary.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    _write_markdown(rows, md_path)
    print(f"\nSummary JSON: {json_path}")
    print(f"Summary Markdown: {md_path}")
    return rows


def _run_one_variant(
    output_dir: str,
    name: str,
    net_count: int,
    bus_count: int,
    passes: int,
    threads: int,
    timeout_seconds: int,
    permutation: str,
    seed: int,
    feedback_seed_via_weight: float,
    case_labels: set[str],
    conflict_model: str,
    router_corridor_penalty: float,
    router_corridor_margin: float,
    router_corridor_mode: str,
    router_corridor_scale: float,
    router_corridor_max_factor: float,
    router_corridor_tie_break_weight: float = 0.1,
) -> list[dict]:
    rows = []
    variant_dir = os.path.join(output_dir, name)
    bench_path = os.path.join(variant_dir, f"{name}.json")
    assignments_path = os.path.join(variant_dir, f"{name}_assignments.json")

    info = generate(
        output_dir=variant_dir,
        bench_path=bench_path,
        assignments_path=assignments_path,
        pad_type="smd",
        net_count=net_count,
        bus_count=bus_count,
        permutation=permutation,
        seed=seed,
    )
    split_path = _write_split_assignment(
        assignments_path,
        os.path.join(variant_dir, f"{name}_split_assignment.json"),
    )
    route_dir = os.path.join(variant_dir, "seed_only")
    print(f"\n=== {name}: nets={net_count}, buses={bus_count} ===")
    results = run_dummy_corridor_test(
        bench_path=info["bench"],
        pcb_path=info["pcb"],
        output_dir=route_dir,
        passes=passes,
        threads=threads,
        timeout_seconds=timeout_seconds,
        include_clean=False,
        include_solvers=True,
        include_feedback=True,
        feedback_seed_via_weight=feedback_seed_via_weight,
        conflict_model=conflict_model,
        assignment_file=split_path,
        case_labels=case_labels,
        enable_corridors=False,
        seed_guide="stub",
        router_corridor_penalty=router_corridor_penalty,
        router_corridor_margin=router_corridor_margin,
        router_corridor_mode=router_corridor_mode,
        router_corridor_scale=router_corridor_scale,
        router_corridor_max_factor=router_corridor_max_factor,
        router_corridor_tie_break_weight=router_corridor_tie_break_weight,
    )
    for entry in results:
        rows.append(_result_row(name, entry))
    return rows


def _parse_variants(text: str) -> list[tuple[int, int]]:
    variants = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        n, b = item.lower().split("x")
        variants.append((int(n), int(b)))
    return variants


def _parse_seeds(text: str) -> list[int]:
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def _parse_case_labels(text: str) -> set[str]:
    return {x.strip() for x in text.split(",") if x.strip()}


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run SMD high-conflict seed-only sweep")
    parser.add_argument("--output-dir", default="runs/phase1_seed_sweep")
    parser.add_argument("--variants", default="16x4,24x6,32x8", help="Comma-separated net_count x bus_count, e.g. 24x6")
    parser.add_argument("--passes", type=int, default=20)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--permutation", choices=["reverse", "shuffle"], default="reverse")
    parser.add_argument("--seeds", default="42")
    parser.add_argument("--feedback-seed-via-weight", type=float, default=2.0)
    parser.add_argument("--conflict-model", choices=["bbox", "corridor"], default="bbox")
    parser.add_argument("--router-corridor-penalty", type=float, default=0.0)
    parser.add_argument("--router-corridor-margin", type=float, default=0.2)
    parser.add_argument("--router-corridor-mode", default="soft", choices=["soft", "hard", "tie_breaker"])
    parser.add_argument("--router-corridor-scale", type=float, default=1.0)
    parser.add_argument("--router-corridor-max-factor", type=float, default=1.0)
    parser.add_argument("--router-corridor-tie-break-weight", type=float, default=0.1)
    parser.add_argument(
        "--case-labels",
        default="all_L0,stripe,greedy,feedback,split_outer_inner",
        help="Comma-separated case labels to route",
    )
    args = parser.parse_args(argv)

    run_sweep(
        output_dir=args.output_dir,
        variants=_parse_variants(args.variants),
        passes=args.passes,
        threads=args.threads,
        timeout_seconds=args.timeout,
        permutation=args.permutation,
        seeds=_parse_seeds(args.seeds),
        feedback_seed_via_weight=args.feedback_seed_via_weight,
        case_labels=_parse_case_labels(args.case_labels),
        conflict_model=args.conflict_model,
        router_corridor_penalty=args.router_corridor_penalty,
        router_corridor_margin=args.router_corridor_margin,
        router_corridor_mode=args.router_corridor_mode,
        router_corridor_scale=args.router_corridor_scale,
        router_corridor_max_factor=args.router_corridor_max_factor,
        router_corridor_tie_break_weight=args.router_corridor_tie_break_weight,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
