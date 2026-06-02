"""
Corridor-adherence scoring for routed KiCad PCB results.

This script measures whether detailed routed traces follow the expected bus
corridors from a benchmark JSON. It is intentionally separate from routability
metrics: a design can route all nets while still ignoring the planner's bus
regions.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiutils.board import Board

from benchmark.routed_parser import _bench_point_to_pcb, _collect_trace_items, _distance


Point = Tuple[float, float]
Segment = Tuple[Point, Point]


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    ax, ay = start
    bx, by = end
    px, py = point
    dx, dy = bx - ax, by - ay
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return _distance(point, start)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


def _bus_width(bus: dict, bench: dict, margin: float) -> float:
    width = float(bus.get("width", 0.0) or 0.0)
    if width <= 0.0:
        netclass = bus.get("netclass") or "Default"
        width = float(
            bench.get("netclasses", {})
            .get(netclass, {})
            .get("track_width", 0.2)
        )
    return width / 2.0 + margin


def _bus_endpoints(bus: dict, bench: dict) -> Tuple[Point, Point]:
    dia_pos = bus.get("dia_pos") or {}
    start = dia_pos.get("start") or bus.get("start_pos")
    end = dia_pos.get("end") or bus.get("end_pos")
    if start is None or end is None:
        raise ValueError(
            f"Bus {bus.get('id')} does not contain dia_pos.start/end or start_pos/end_pos"
        )
    return _bench_point_to_pcb(bench, start), _bench_point_to_pcb(bench, end)


def _manhattan_corridor_paths(bus: dict, bench: dict) -> List[List[Point]]:
    start, end = _bus_endpoints(bus, bench)
    sx, sy = start
    ex, ey = end
    mid_x = (sx + ex) / 2.0
    mid_y = (sy + ey) / 2.0
    patterns = [
        [(sx, sy), (ex, sy), (ex, ey)],
        [(sx, sy), (sx, ey), (ex, ey)],
        [(sx, sy), (mid_x, sy), (mid_x, ey), (ex, ey)],
        [(sx, sy), (sx, mid_y), (ex, mid_y), (ex, ey)],
    ]

    unique = []
    seen = set()
    for path in patterns:
        key = tuple((round(x, 6), round(y, 6)) for x, y in path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _path_segments(paths: Iterable[List[Point]]) -> List[Segment]:
    segments = []
    for path in paths:
        for start, end in zip(path, path[1:]):
            if _distance(start, end) > 1e-9:
                segments.append((start, end))
    return segments


def _point_in_corridor(point: Point, corridor_segments: List[Segment], half_width: float) -> bool:
    return any(
        _point_segment_distance(point, start, end) <= half_width
        for start, end in corridor_segments
    )


def _segment_inside_length(
    start: Point,
    end: Point,
    corridor_segments: List[Segment],
    half_width: float,
    sample_step: float,
) -> float:
    length = _distance(start, end)
    if length <= 1e-12:
        return 0.0

    samples = max(1, int(math.ceil(length / sample_step)))
    inside = 0
    for i in range(samples):
        t = (i + 0.5) / samples
        point = (
            start[0] + (end[0] - start[0]) * t,
            start[1] + (end[1] - start[1]) * t,
        )
        if _point_in_corridor(point, corridor_segments, half_width):
            inside += 1
    return length * inside / samples


def _method_from_pcb_name(path: Path) -> str:
    match = re.search(r"phase13_\d+_([^_]+)_routed\.kicad_pcb$", path.name)
    if match:
        return match.group(1)
    stem = path.stem
    if stem.endswith("_routed"):
        stem = stem[:-7]
    return stem


def score_routed_corridors(
    bench_path: str,
    pcb_path: str,
    sample_step: float = 0.1,
    corridor_margin: float = 0.2,
) -> dict:
    with open(bench_path, "r", encoding="utf-8") as f:
        bench = json.load(f)

    board = Board().from_file(pcb_path)
    routed_segments, _ = _collect_trace_items(board)
    segments_by_net: Dict[int, List[dict]] = defaultdict(list)
    for segment in routed_segments:
        net_id = segment.get("net")
        if net_id is not None:
            segments_by_net[int(net_id)].append(segment)

    bus_results = []
    global_inside = 0.0
    global_total = 0.0

    for bus in bench.get("buses", []):
        bus_id = int(bus["id"])
        half_width = _bus_width(bus, bench, corridor_margin)
        corridor_segments = _path_segments(_manhattan_corridor_paths(bus, bench))
        bus_inside = 0.0
        bus_total = 0.0
        net_results = []

        for net_id in bus.get("net_ids", []):
            net_inside = 0.0
            net_total = 0.0
            for segment in segments_by_net.get(int(net_id), []):
                seg_len = _distance(segment["start"], segment["end"])
                net_total += seg_len
                net_inside += _segment_inside_length(
                    segment["start"],
                    segment["end"],
                    corridor_segments,
                    half_width,
                    sample_step,
                )

            bus_inside += net_inside
            bus_total += net_total
            net_results.append({
                "net_id": int(net_id),
                "inside_wirelength": round(net_inside, 4),
                "total_wirelength": round(net_total, 4),
                "inside_ratio": round(net_inside / net_total, 6) if net_total > 0 else None,
                "segment_count": len(segments_by_net.get(int(net_id), [])),
            })

        global_inside += bus_inside
        global_total += bus_total
        bus_results.append({
            "bus_id": bus_id,
            "net_ids": [int(n) for n in bus.get("net_ids", [])],
            "corridor_half_width": round(half_width, 4),
            "inside_wirelength": round(bus_inside, 4),
            "total_wirelength": round(bus_total, 4),
            "inside_ratio": round(bus_inside / bus_total, 6) if bus_total > 0 else None,
            "net_count": len(bus.get("net_ids", [])),
            "nets": net_results,
        })

    return {
        "bench": os.path.abspath(bench_path),
        "pcb": os.path.abspath(pcb_path),
        "method": _method_from_pcb_name(Path(pcb_path)),
        "sample_step": sample_step,
        "corridor_margin": corridor_margin,
        "inside_wirelength": round(global_inside, 4),
        "total_wirelength": round(global_total, 4),
        "inside_ratio": round(global_inside / global_total, 6) if global_total > 0 else None,
        "bus_count": len(bus_results),
        "buses": bus_results,
    }


def _find_bench_json(case_dir: Path) -> Optional[Path]:
    candidates = [
        p for p in case_dir.glob("*.json")
        if not p.name.endswith("_assignments.json")
        and not p.name.endswith("_split_assignment.json")
        and not p.name.endswith("_summary.json")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (len(p.name), p.name))
    return candidates[0]


def score_run_dir(
    run_dir: str,
    sample_step: float = 0.1,
    corridor_margin: float = 0.2,
) -> dict:
    root = Path(run_dir)
    entries = []
    for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        bench_path = _find_bench_json(case_dir)
        seed_dir = case_dir / "seed_only"
        if bench_path is None or not seed_dir.exists():
            continue
        for pcb_path in sorted(seed_dir.glob("*_routed.kicad_pcb")):
            result = score_routed_corridors(
                str(bench_path),
                str(pcb_path),
                sample_step=sample_step,
                corridor_margin=corridor_margin,
            )
            result["case"] = case_dir.name
            entries.append(result)

    by_method = {}
    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry["method"]].append(entry)
    for method, method_entries in sorted(grouped.items()):
        total = sum(e["total_wirelength"] for e in method_entries)
        inside = sum(e["inside_wirelength"] for e in method_entries)
        ratios = [e["inside_ratio"] for e in method_entries if e["inside_ratio"] is not None]
        by_method[method] = {
            "cases": len(method_entries),
            "inside_wirelength": round(inside, 4),
            "total_wirelength": round(total, 4),
            "inside_ratio": round(inside / total, 6) if total > 0 else None,
            "mean_case_inside_ratio": round(sum(ratios) / len(ratios), 6) if ratios else None,
        }

    return {
        "run_dir": os.path.abspath(run_dir),
        "sample_step": sample_step,
        "corridor_margin": corridor_margin,
        "entry_count": len(entries),
        "by_method": by_method,
        "entries": entries,
    }


def _write_markdown(summary: dict, output_path: str) -> None:
    lines = [
        "# Routed Corridor Adherence",
        "",
        f"- Run dir: `{summary.get('run_dir', '')}`",
        f"- Entries: {summary.get('entry_count', 0)}",
        f"- Sample step: {summary.get('sample_step')} mm",
        f"- Corridor margin: {summary.get('corridor_margin')} mm",
        "",
        "| Method | Cases | Inside ratio | Mean case ratio | Inside WL | Total WL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method, row in summary.get("by_method", {}).items():
        lines.append(
            f"| {method} | {row['cases']} | {row['inside_ratio']} | "
            f"{row['mean_case_inside_ratio']} | {row['inside_wirelength']} | "
            f"{row['total_wirelength']} |"
        )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score how much routed wirelength remains inside benchmark bus corridors."
    )
    parser.add_argument("--bench", help="Benchmark JSON for a single routed PCB")
    parser.add_argument("--pcb", help="Routed KiCad PCB for a single result")
    parser.add_argument("--run-dir", help="Batch score a seed-sweep run directory")
    parser.add_argument("--output", help="Write JSON result to this path")
    parser.add_argument("--markdown", help="Write batch summary markdown to this path")
    parser.add_argument("--sample-step", type=float, default=0.1, help="Sampling step in mm")
    parser.add_argument("--corridor-margin", type=float, default=0.2, help="Extra corridor margin in mm")
    args = parser.parse_args()

    if args.sample_step <= 0:
        raise ValueError("--sample-step must be positive")

    if args.run_dir:
        result = score_run_dir(
            args.run_dir,
            sample_step=args.sample_step,
            corridor_margin=args.corridor_margin,
        )
        print("Routed corridor adherence")
        print(f"  Entries: {result['entry_count']}")
        for method, row in result["by_method"].items():
            print(
                f"  {method}: cases={row['cases']} inside_ratio={row['inside_ratio']} "
                f"mean_case_ratio={row['mean_case_inside_ratio']}"
            )
    elif args.bench and args.pcb:
        result = score_routed_corridors(
            args.bench,
            args.pcb,
            sample_step=args.sample_step,
            corridor_margin=args.corridor_margin,
        )
        print("Routed corridor adherence")
        print(f"  Method: {result['method']}")
        print(f"  Inside ratio: {result['inside_ratio']}")
        print(f"  Inside wirelength: {result['inside_wirelength']} / {result['total_wirelength']} mm")
    else:
        parser.error("Use either --run-dir or both --bench and --pcb")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    if args.markdown:
        _write_markdown(result, args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
