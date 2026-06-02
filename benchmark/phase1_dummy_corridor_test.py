"""
Phase 1.3: dummy-net corridor diagnostic experiment.

Unlike same-net guide traces, dummy corridors are exported as independent
through-hole BLOCK nets with locked full-width segments. Freerouting should see
them as physical occupancy from other nets. This script is a diagnostic for the
assignment-expression layer, not a final solver benchmark.
"""

import argparse
import json
import math
import os
import random
import re
import shutil
import sys
from typing import Iterable, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from benchmark.evaluator import Evaluator, LayerAssignment
from benchmark.freerouting_headless import route_pcb
from benchmark.pcb_exporter import (
    _load_bench,
    _point_to_pcb,
    _signal_layer_map,
    export_pcb_with_assignment,
)

from kiutils.board import Board


BLOCK_PREFIX = "__BA_BLOCK_"


def _bus_ids(bench: dict) -> list[int]:
    return [int(bus["id"]) for bus in bench.get("buses", [])]


def _all_layer_assignment(bench: dict, layer_id: int) -> LayerAssignment:
    return LayerAssignment({bid: layer_id for bid in _bus_ids(bench)})


def _stripe_assignment(bench: dict) -> LayerAssignment:
    layers = int(bench["board"]["layers"])
    return LayerAssignment({bid: idx % layers for idx, bid in enumerate(_bus_ids(bench))})


def _random_assignment(bench: dict, seed: int) -> LayerAssignment:
    layers = int(bench["board"]["layers"])
    rng = random.Random(seed)
    return LayerAssignment({bid: rng.randint(0, layers - 1) for bid in _bus_ids(bench)})


def _max_net_id(pcb_text: str) -> int:
    ids = [int(m.group(1)) for m in re.finditer(r'\(net\s+(\d+)\s+', pcb_text)]
    return max(ids) if ids else 0


def _insert_before_first_footprint(pcb_text: str, insert: str) -> str:
    marker = "\n  (footprint "
    idx = pcb_text.find(marker)
    if idx < 0:
        raise ValueError("Could not find first footprint in KiCad PCB")
    return pcb_text[:idx] + "\n" + insert + pcb_text[idx:]


def _insert_before_final_close(pcb_text: str, insert: str) -> str:
    idx = pcb_text.rfind("\n)")
    if idx < 0:
        raise ValueError("Could not find final KiCad PCB close paren")
    return pcb_text[:idx] + "\n" + insert + pcb_text[idx:]


def _net_decl(net_id: int, name: str) -> str:
    return f'  (net {net_id} "{name}")\n'


def _block_footprint(
    ref: str,
    x: float,
    y: float,
    net_id: int,
    net_name: str,
    layer: str,
    endpoint_mode: str,
) -> str:
    if endpoint_mode == "through_hole":
        pad = (
            f'    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2) '
            f'(drill 0.5) (layers *.Cu *.Mask)\n'
            f'      (net {net_id} "{net_name}") (tstamp {ref}-pad))'
        )
        attr = "through_hole"
    elif endpoint_mode == "smd":
        pad = (
            f'    (pad "1" smd circle (at 0 0) (size 1.0 1.0) '
            f'(layers "{layer}")\n'
            f'      (net {net_id} "{net_name}") (tstamp {ref}-pad))'
        )
        attr = "smd"
    else:
        raise ValueError(f"Unsupported endpoint mode for footprint: {endpoint_mode}")

    return f"""  (footprint "BA:BlockPad" (layer "F.Cu")
    (tstamp {ref}-fp)
    (at {x:.4f} {y:.4f})
    (attr {attr})
    (fp_text reference "{ref}" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp {ref}-ref))
    (fp_text value "" (at 0 3) (layer "F.Fab")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp {ref}-val))
{pad}
  )
"""


def _block_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    width: float,
    layer: str,
    net_id: int,
    tstamp: str,
) -> str:
    return (
        f'  (segment locked (start {start[0]:.4f} {start[1]:.4f}) '
        f'(end {end[0]:.4f} {end[1]:.4f}) (width {width:.4f}) '
        f'(layer "{layer}") (net {net_id}) (tstamp {tstamp}))\n'
    )


def _scale_segment_about_center(
    start: tuple[float, float],
    end: tuple[float, float],
    length_fraction: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    if not 0.0 < length_fraction <= 1.0:
        raise ValueError("length_fraction must be in (0, 1]")
    if length_fraction >= 1.0:
        return start, end

    sx, sy = start
    ex, ey = end
    cx = (sx + ex) / 2.0
    cy = (sy + ey) / 2.0
    hx = (ex - sx) * length_fraction / 2.0
    hy = (ey - sy) * length_fraction / 2.0
    return (cx - hx, cy - hy), (cx + hx, cy + hy)


def _path_segments(points: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return [
        (points[i], points[i + 1])
        for i in range(len(points) - 1)
        if points[i] != points[i + 1]
    ]


def _manhattan_guide_segments(
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    sx, sy = start
    ex, ey = end
    mid_x = (sx + ex) / 2.0
    mid_y = (sy + ey) / 2.0
    paths = [
        [(sx, sy), (ex, sy), (ex, ey)],
        [(sx, sy), (sx, ey), (ex, ey)],
        [(sx, sy), (mid_x, sy), (mid_x, ey), (ex, ey)],
        [(sx, sy), (sx, mid_y), (ex, mid_y), (ex, ey)],
    ]
    segments = []
    seen = set()
    for path in paths:
        for seg_start, seg_end in _path_segments(path):
            key = (
                round(seg_start[0], 6),
                round(seg_start[1], 6),
                round(seg_end[0], 6),
                round(seg_end[1], 6),
            )
            if key in seen:
                continue
            seen.add(key)
            segments.append((seg_start, seg_end))
    return segments


def export_router_corridor_guide(
    bench_path: str,
    assignment: LayerAssignment,
    output_path: str,
    corridor_bus_ids: Optional[set[int]] = None,
    corridor_margin: float = 0.2,
) -> str:
    """Export BA bus corridors as a soft guide consumed by patched Freerouting."""
    bench = _load_bench(bench_path)
    segments = []
    for bus in bench.get("buses", []):
        bid = int(bus["id"])
        if corridor_bus_ids is not None and bid not in corridor_bus_ids:
            continue
        layer = int(assignment.assignment.get(bid, -1))
        half_width = (float(bus.get("width", 0.2)) / 2.0) + corridor_margin
        start = tuple(bus.get("start_pos") or bus.get("dia_pos", {}).get("start"))
        end = tuple(bus.get("end_pos") or bus.get("dia_pos", {}).get("end"))
        for net_id in bus.get("net_ids", []):
            for seg_start, seg_end in _manhattan_guide_segments(start, end):
                segments.append({
                    "bus": bid,
                    "net": int(net_id),
                    "layer": layer,
                    "x1": float(seg_start[0]) * 1000.0,
                    "y1": float(seg_start[1]) * 1000.0,
                    "x2": float(seg_end[0]) * 1000.0,
                    "y2": float(seg_end[1]) * 1000.0,
                    "half_width": half_width * 1000.0,
                })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "coordinate_system": "dsn_um",
            "corridor_margin_mm": corridor_margin,
            "segments": segments,
        }, f, indent=2)
    return output_path


def _corridor_segment_points(
    start: tuple[float, float],
    end: tuple[float, float],
    length_fraction: float,
    geometry: str,
    crossbar_length: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    if geometry == "bus_center":
        return _scale_segment_about_center(start, end, length_fraction)
    if geometry != "crossbar":
        raise ValueError(f"Unsupported corridor geometry: {geometry}")

    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    norm = math.hypot(dx, dy)
    if norm <= 1e-9:
        return start, end

    px = -dy / norm
    py = dx / norm
    half = crossbar_length * length_fraction / 2.0
    cx = (sx + ex) / 2.0
    cy = (sy + ey) / 2.0
    return (cx - px * half, cy - py * half), (cx + px * half, cy + py * half)


def export_dummy_corridor_pcb(
    bench_path: str,
    assignment: dict,
    pcb_path: str,
    output_path: str,
    width_scale: float = 1.0,
    min_width: float = 0.5,
    length_fraction: float = 1.0,
    corridor_endpoint: str = "through_hole",
    corridor_geometry: str = "bus_center",
    crossbar_length: float = 18.0,
    corridor_bus_ids: Optional[set[int]] = None,
) -> str:
    bench = _load_bench(bench_path)
    assignment = {int(k): int(v) for k, v in assignment.items()}
    board = Board().from_file(pcb_path)
    layer_map = _signal_layer_map(board, bench)

    tmp_path = output_path + ".tmp"
    shutil.copyfile(pcb_path, tmp_path)
    with open(tmp_path, "r", encoding="utf-8") as f:
        text = f.read()
    os.remove(tmp_path)

    next_net = _max_net_id(text) + 1
    net_decls = []
    items = []
    added = 0

    for bus in bench.get("buses", []):
        bid = int(bus["id"])
        if corridor_bus_ids is not None and bid not in corridor_bus_ids:
            continue
        layer_id = assignment.get(bid)
        if layer_id is None:
            continue
        layer_name = layer_map.get(str(layer_id), "F.Cu" if layer_id == 0 else "B.Cu")
        sx, sy = _point_to_pcb(bench, bus["start_pos"])
        ex, ey = _point_to_pcb(bench, bus["end_pos"])
        seg_start, seg_end = _corridor_segment_points(
            start=(sx, sy),
            end=(ex, ey),
            length_fraction=length_fraction,
            geometry=corridor_geometry,
            crossbar_length=crossbar_length,
        )
        net_name = f"{BLOCK_PREFIX}{bid}"
        net_id = next_net
        next_net += 1
        width = max(float(bus.get("width", min_width)) * width_scale, min_width)

        net_decls.append(_net_decl(net_id, net_name))
        if corridor_endpoint != "segment_only":
            items.append(_block_footprint(
                f"BA{bid}A", sx, sy, net_id, net_name, layer_name, corridor_endpoint
            ))
            items.append(_block_footprint(
                f"BA{bid}B", ex, ey, net_id, net_name, layer_name, corridor_endpoint
            ))
        items.append(
            _block_segment(
                start=seg_start,
                end=seg_end,
                width=width,
                layer=layer_name,
                net_id=net_id,
                tstamp=f"ba-block-{bid}",
            )
        )
        added += 1

    text = _insert_before_first_footprint(text, "".join(net_decls))
    text = _insert_before_final_close(text, "".join(items))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Exported dummy corridor PCB: {output_path}")
    print(f"  Dummy corridors added: {added}")
    print(f"  Corridor endpoint mode: {corridor_endpoint}")
    print(f"  Corridor geometry: {corridor_geometry}")
    return output_path


def _adjusted_metrics(real: dict) -> dict:
    per_net = real.get("per_net", {})
    block_nets = {name for name in per_net if name.startswith(BLOCK_PREFIX)}
    block_nets.update(name for name in real.get("unrouted_nets", []) if str(name).startswith(BLOCK_PREFIX))
    non_block = {name: metrics for name, metrics in per_net.items() if name not in block_nets}
    adjusted_unrouted = [
        name for name in real.get("unrouted_nets", [])
        if not str(name).startswith(BLOCK_PREFIX)
    ]
    return {
        "adjusted_wirelength_mm": round(sum(m.get("wirelength", 0.0) for m in non_block.values()), 2),
        "adjusted_segments": sum(int(m.get("segments", 0)) for m in non_block.values()),
        "adjusted_vias": sum(int(m.get("vias", 0)) for m in non_block.values()),
        "adjusted_routed_nets": len(non_block),
        "adjusted_unrouted_nets": len(adjusted_unrouted),
        "block_net_count": len(block_nets),
        "block_wirelength_mm": round(sum(per_net.get(n, {}).get("wirelength", 0.0) for n in block_nets), 2),
    }


def _select_corridor_bus_ids(
    ev: Evaluator,
    assignment: LayerAssignment,
    max_corridors: Optional[int],
    strategy: str,
) -> Optional[set[int]]:
    if max_corridors is None or max_corridors <= 0:
        return None

    metrics = ev.per_bus_metrics(assignment)
    if strategy == "first":
        ordered = sorted(metrics, key=lambda m: int(m["bus_id"]))
    elif strategy == "top-conflict":
        ordered = sorted(
            metrics,
            key=lambda m: (
                int(m.get("conflicts", 0)),
                int(m.get("crossings", 0)),
                float(m.get("width", 0.0)),
                int(m.get("num_nets", 0)),
            ),
            reverse=True,
        )
    else:
        raise ValueError(f"Unsupported corridor selection strategy: {strategy}")

    return {int(m["bus_id"]) for m in ordered[:max_corridors]}


def _solver_cases(
    bench_path: str,
    include_feedback: bool,
    feedback_seed_via_weight: float = 2.0,
    conflict_model: str = "bbox",
) -> list[tuple[str, LayerAssignment]]:
    from benchmark.solvers import GreedySolver

    cases: list[tuple[str, LayerAssignment]] = [
        ("greedy", GreedySolver(
            bench_path,
            order="width_desc",
            conflict_model=conflict_model,
        ).solve()),
    ]
    if include_feedback:
        from benchmark.feedback_solver import FeedbackSolver

        cases.append((
            "feedback",
            FeedbackSolver(
                bench_path,
                max_iterations=30,
                patience=8,
                seed_via_weight=feedback_seed_via_weight,
                conflict_model=conflict_model,
            ).solve(),
        ))
    return cases


def _assignment_file_cases(path: Optional[str]) -> list[tuple[str, LayerAssignment]]:
    if path is None:
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for label, assignment in data.items():
        cases.append((
            str(label),
            LayerAssignment({int(k): int(v) for k, v in assignment.items()}),
        ))
    return cases


def _cases(
    bench_path: str,
    bench: dict,
    random_count: int,
    seed: int,
    include_clean: bool,
    include_solvers: bool,
    include_feedback: bool,
    feedback_seed_via_weight: float,
    conflict_model: str,
    assignment_file: Optional[str],
) -> list[tuple[str, Optional[LayerAssignment]]]:
    layers = int(bench["board"]["layers"])
    cases: list[tuple[str, Optional[LayerAssignment]]] = []
    if include_clean:
        cases.append(("clean", None))
    cases.extend([
        ("all_L0", _all_layer_assignment(bench, 0)),
        (f"all_L{layers - 1}", _all_layer_assignment(bench, layers - 1)),
        ("stripe", _stripe_assignment(bench)),
    ])
    for idx in range(random_count):
        cases.append((f"random_{idx:02d}", _random_assignment(bench, seed + idx)))
    if include_solvers:
        cases.extend(_solver_cases(
            bench_path,
            include_feedback=include_feedback,
            feedback_seed_via_weight=feedback_seed_via_weight,
            conflict_model=conflict_model,
        ))
    cases.extend(_assignment_file_cases(assignment_file))
    return cases


def _prepare_case_pcb(
    bench_path: str,
    source_pcb: str,
    output_pcb: str,
    assignment: LayerAssignment,
    width_scale: float,
    min_width: float,
    length_fraction: float,
    corridor_endpoint: str,
    corridor_geometry: str,
    crossbar_length: float,
    corridor_bus_ids: Optional[set[int]],
    enable_corridors: bool,
    seed_guide: str,
    seed_stub_length: float,
    seed_locked: bool,
) -> str:
    input_pcb = source_pcb
    seed_path = None

    if seed_guide != "none":
        seed_path = output_pcb.replace(".kicad_pcb", f"_{seed_guide}_seed.kicad_pcb")
        export_pcb_with_assignment(
            bench_path=bench_path,
            assignment=assignment.assignment,
            pcb_path=source_pcb,
            output_path=seed_path,
            guide_mode=seed_guide,
            stub_length=seed_stub_length,
            locked=seed_locked,
        )
        input_pcb = seed_path

    if enable_corridors:
        return export_dummy_corridor_pcb(
            bench_path=bench_path,
            assignment=assignment.assignment,
            pcb_path=input_pcb,
            output_path=output_pcb,
            width_scale=width_scale,
            min_width=min_width,
            length_fraction=length_fraction,
            corridor_endpoint=corridor_endpoint,
            corridor_geometry=corridor_geometry,
            crossbar_length=crossbar_length,
            corridor_bus_ids=corridor_bus_ids,
        )

    if seed_path is not None:
        shutil.copyfile(seed_path, output_pcb)
    else:
        shutil.copyfile(source_pcb, output_pcb)
    print(f"Exported case PCB: {output_pcb}")
    return output_pcb


def run_dummy_corridor_test(
    bench_path: str,
    pcb_path: str,
    output_dir: str,
    random_count: int = 0,
    seed: int = 42,
    passes: int = 10,
    threads: int = 4,
    timeout_seconds: int = 180,
    width_scale: float = 1.0,
    min_width: float = 0.5,
    length_fraction: float = 1.0,
    corridor_endpoint: str = "through_hole",
    corridor_geometry: str = "bus_center",
    crossbar_length: float = 18.0,
    max_corridors: Optional[int] = None,
    corridor_select: str = "top-conflict",
    enable_corridors: bool = True,
    seed_guide: str = "none",
    seed_stub_length: float = 2.0,
    seed_locked: bool = True,
    include_clean: bool = True,
    include_solvers: bool = False,
    include_feedback: bool = False,
    feedback_seed_via_weight: float = 2.0,
    conflict_model: str = "bbox",
    assignment_file: Optional[str] = None,
    router_corridor_penalty: float = 0.0,
    router_corridor_margin: float = 0.2,
    router_corridor_mode: str = "soft",
    router_corridor_scale: float = 1.0,
    router_corridor_max_factor: float = 1.0,
    router_corridor_tie_break_weight: float = 0.1,
    keep_pcbs: bool = True,
    case_labels: Optional[set[str]] = None,
) -> list[dict]:
    os.makedirs(output_dir, exist_ok=True)
    bench = _load_bench(bench_path)
    ev = Evaluator(bench_path)
    cases = _cases(
        bench_path=bench_path,
        bench=bench,
        random_count=random_count,
        seed=seed,
        include_clean=include_clean,
        include_solvers=include_solvers,
        include_feedback=include_feedback,
        feedback_seed_via_weight=feedback_seed_via_weight,
        conflict_model=conflict_model,
        assignment_file=assignment_file,
    )
    if case_labels is not None:
        cases = [(label, la) for label, la in cases if label in case_labels]
    base = os.path.splitext(os.path.basename(pcb_path))[0]

    print("\n" + "=" * 72)
    print("Phase 1.3 dummy-net corridor diagnostic")
    print(f"  bench={bench_path}")
    print(f"  pcb={pcb_path}")
    print(f"  cases={len(cases)}, passes={passes}, timeout={timeout_seconds}s")

    results = []
    for idx, (label, la) in enumerate(cases):
        work_pcb = os.path.join(output_dir, f"{base}_phase13_{idx:02d}_{label}.kicad_pcb")
        entry = {
            "idx": idx,
            "label": label,
            "assignment": None if la is None else {str(k): int(v) for k, v in la.assignment.items()},
            "seed_guide": "none" if la is None else seed_guide,
            "corridors_enabled": False if la is None else enable_corridors,
            "router_corridor_penalty": router_corridor_penalty,
            "router_corridor_margin": router_corridor_margin,
            "router_corridor_mode": router_corridor_mode,
            "router_corridor_scale": router_corridor_scale,
            "router_corridor_max_factor": router_corridor_max_factor,
            "router_corridor_tie_break_weight": router_corridor_tie_break_weight,
        }
        if la is not None:
            proxy = ev.evaluate(la)
            corridor_bus_ids = _select_corridor_bus_ids(
                ev=ev,
                assignment=la,
                max_corridors=max_corridors,
                strategy=corridor_select,
            )
            entry.update({
                "proxy_cost": round(proxy.cost, 2),
                "proxy_conflict": proxy.conflict_count,
                "proxy_crossing": proxy.crossing_count,
                "proxy_layer_usage": proxy.layer_usage,
                "proxy_via_estimate": proxy.via_estimate,
                "corridor_bus_ids": None if corridor_bus_ids is None else sorted(corridor_bus_ids),
            })
        else:
            corridor_bus_ids = None

        try:
            if la is None:
                shutil.copyfile(pcb_path, work_pcb)
                router_guide_path = None
            else:
                _prepare_case_pcb(
                    bench_path=bench_path,
                    source_pcb=pcb_path,
                    output_pcb=work_pcb,
                    assignment=la,
                    width_scale=width_scale,
                    min_width=min_width,
                    length_fraction=length_fraction,
                    corridor_endpoint=corridor_endpoint,
                    corridor_geometry=corridor_geometry,
                    crossbar_length=crossbar_length,
                    corridor_bus_ids=corridor_bus_ids,
                    enable_corridors=enable_corridors,
                    seed_guide=seed_guide,
                    seed_stub_length=seed_stub_length,
                    seed_locked=seed_locked,
                )
                router_guide_path = None
                if router_corridor_penalty > 0:
                    router_guide_path = os.path.join(
                        output_dir,
                        f"{base}_phase13_{idx:02d}_{label}_router_corridor_guide.json",
                    )
                    export_router_corridor_guide(
                        bench_path=bench_path,
                        assignment=la,
                        output_path=router_guide_path,
                        corridor_bus_ids=corridor_bus_ids,
                        corridor_margin=router_corridor_margin,
                    )
                entry["router_corridor_guide"] = router_guide_path
            real = route_pcb(
                work_pcb,
                output_dir=output_dir,
                passes=passes,
                threads=threads,
                timeout_seconds=timeout_seconds,
                corridor_guide_path=router_guide_path,
                corridor_penalty=router_corridor_penalty,
                corridor_mode=router_corridor_mode,
                corridor_scale=router_corridor_scale,
                corridor_max_factor=router_corridor_max_factor,
                corridor_tie_break_weight=router_corridor_tie_break_weight,
            )
            entry.update({
                "real_wirelength_mm": real["total_wirelength"],
                "real_segments": real["total_segments"],
                "real_vias": real["total_vias"],
                "real_routed_nets": real["routed_net_count"],
                "real_unrouted_nets": real["unrouted_net_count"],
                "real_runtime_sec": real["runtime_seconds"],
                "dsn": real["dsn"],
                "ses": real["ses"],
                "routed_pcb": real["routed_pcb"],
            })
            entry.update(_adjusted_metrics(real))
            print(
                f"  {label:<10} raw_wl={entry['real_wirelength_mm']:>8.2f} "
                f"adj_wl={entry['adjusted_wirelength_mm']:>8.2f} "
                f"vias={entry['adjusted_vias']:>4} unrouted={entry['real_unrouted_nets']:>3} "
                f"blocks={entry['block_net_count']}"
            )
        except Exception as exc:
            entry["error"] = str(exc)
            print(f"  {label:<10} ERROR: {exc}")
        finally:
            if not keep_pcbs and os.path.exists(work_pcb):
                os.remove(work_pcb)

        results.append(entry)

    out_path = os.path.join(output_dir, f"{base}_phase13_dummy_corridor.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "phase": "1.3",
            "purpose": "dummy-net corridor diagnostic for layer-assignment visibility",
            "bench": bench_path,
            "pcb": pcb_path,
            "config": {
                "random_count": random_count,
                "seed": seed,
                "passes": passes,
                "threads": threads,
                "timeout_seconds": timeout_seconds,
                "width_scale": width_scale,
                "min_width": min_width,
                "length_fraction": length_fraction,
                "corridor_endpoint": corridor_endpoint,
                "corridor_geometry": corridor_geometry,
                "crossbar_length": crossbar_length,
                "max_corridors": max_corridors,
                "corridor_select": corridor_select,
                "enable_corridors": enable_corridors,
                "seed_guide": seed_guide,
                "seed_stub_length": seed_stub_length,
                "seed_locked": seed_locked,
                "include_clean": include_clean,
                "include_solvers": include_solvers,
                "include_feedback": include_feedback,
                "feedback_seed_via_weight": feedback_seed_via_weight,
                "conflict_model": conflict_model,
                "assignment_file": assignment_file,
                "router_corridor_penalty": router_corridor_penalty,
                "router_corridor_margin": router_corridor_margin,
                "router_corridor_mode": router_corridor_mode,
                "router_corridor_scale": router_corridor_scale,
                "router_corridor_max_factor": router_corridor_max_factor,
                "router_corridor_tie_break_weight": router_corridor_tie_break_weight,
                "case_labels": None if case_labels is None else sorted(case_labels),
            },
            "results": results,
        }, f, indent=2, default=str)
    print(f"  saved={out_path}")
    return results


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 1.3 dummy-net corridor diagnostic")
    parser.add_argument("--bench", required=True)
    parser.add_argument("--pcb", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--random", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--passes", type=int, default=10)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--width-scale", type=float, default=1.0)
    parser.add_argument("--min-width", type=float, default=0.5)
    parser.add_argument("--length-fraction", type=float, default=1.0)
    parser.add_argument(
        "--corridor-endpoint",
        choices=["through_hole", "smd", "segment_only"],
        default="through_hole",
    )
    parser.add_argument(
        "--corridor-geometry",
        choices=["bus_center", "crossbar"],
        default="bus_center",
    )
    parser.add_argument("--crossbar-length", type=float, default=18.0)
    parser.add_argument("--max-corridors", type=int, default=None)
    parser.add_argument("--corridor-select", choices=["top-conflict", "first"], default="top-conflict")
    parser.add_argument("--no-corridors", action="store_true")
    parser.add_argument("--seed-guide", choices=["none", "stub"], default="none")
    parser.add_argument("--seed-stub-length", type=float, default=2.0)
    parser.add_argument("--seed-unlocked", action="store_true")
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--include-solvers", action="store_true")
    parser.add_argument("--include-feedback", action="store_true")
    parser.add_argument("--feedback-seed-via-weight", type=float, default=2.0)
    parser.add_argument("--conflict-model", choices=["bbox", "corridor"], default="bbox")
    parser.add_argument("--assignment-file", default=None)
    parser.add_argument("--router-corridor-penalty", type=float, default=0.0)
    parser.add_argument("--router-corridor-margin", type=float, default=0.2)
    parser.add_argument("--router-corridor-mode", default="soft", choices=["soft", "hard", "tie_breaker"])
    parser.add_argument("--router-corridor-scale", type=float, default=1.0)
    parser.add_argument("--router-corridor-max-factor", type=float, default=1.0)
    parser.add_argument("--router-corridor-tie-break-weight", type=float, default=0.1)
    parser.add_argument("--case-labels", default=None, help="Comma-separated subset of case labels to run")
    parser.add_argument("--remove-input-pcbs", action="store_true")
    args = parser.parse_args(argv)

    run_dummy_corridor_test(
        bench_path=args.bench,
        pcb_path=args.pcb,
        output_dir=args.output_dir,
        random_count=args.random,
        seed=args.seed,
        passes=args.passes,
        threads=args.threads,
        timeout_seconds=args.timeout,
        width_scale=args.width_scale,
        min_width=args.min_width,
        length_fraction=args.length_fraction,
        corridor_endpoint=args.corridor_endpoint,
        corridor_geometry=args.corridor_geometry,
        crossbar_length=args.crossbar_length,
        max_corridors=args.max_corridors,
        corridor_select=args.corridor_select,
        enable_corridors=not args.no_corridors,
        seed_guide=args.seed_guide,
        seed_stub_length=args.seed_stub_length,
        seed_locked=not args.seed_unlocked,
        include_clean=not args.no_clean,
        include_solvers=args.include_solvers,
        include_feedback=args.include_feedback,
        feedback_seed_via_weight=args.feedback_seed_via_weight,
        conflict_model=args.conflict_model,
        assignment_file=args.assignment_file,
        router_corridor_penalty=args.router_corridor_penalty,
        router_corridor_margin=args.router_corridor_margin,
        router_corridor_mode=args.router_corridor_mode,
        router_corridor_scale=args.router_corridor_scale,
        router_corridor_max_factor=args.router_corridor_max_factor,
        router_corridor_tie_break_weight=args.router_corridor_tie_break_weight,
        keep_pcbs=not args.remove_input_pcbs,
        case_labels=None if args.case_labels is None else {x.strip() for x in args.case_labels.split(",") if x.strip()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
