"""
Phase 1.2: full-lock guide diagnostic experiment.

This script checks whether layer assignments become visible to Freerouting when
they are exported as locked full guide traces instead of short endpoint stubs.
It is meant as a diagnostic/stress test, not as final fair-routing evidence.
"""

import argparse
import json
import os
import random
import shutil
import sys
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCHMARK_DIR = os.path.join(PROJECT_ROOT, "benchmark")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if BENCHMARK_DIR not in sys.path:
    sys.path.insert(0, BENCHMARK_DIR)

from benchmark.evaluator import Evaluator, LayerAssignment
from benchmark.freerouting_headless import route_pcb
from benchmark.pcb_exporter import export_pcb_with_assignment


AssignmentCase = Tuple[str, Optional[LayerAssignment]]


def _load_bench(bench_path: str) -> dict:
    with open(bench_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _bus_ids(bench: dict) -> List[int]:
    return [int(b["id"]) for b in bench.get("buses", [])]


def _all_layer_assignment(bench: dict, layer_id: int) -> LayerAssignment:
    return LayerAssignment({bid: layer_id for bid in _bus_ids(bench)})


def _stripe_assignment(bench: dict) -> LayerAssignment:
    layers = int(bench["board"]["layers"])
    return LayerAssignment({bid: idx % layers for idx, bid in enumerate(_bus_ids(bench))})


def _random_assignment(bench: dict, seed: int) -> LayerAssignment:
    layers = int(bench["board"]["layers"])
    rng = random.Random(seed)
    return LayerAssignment({bid: rng.randint(0, layers - 1) for bid in _bus_ids(bench)})


def _solver_cases(bench_path: str) -> List[AssignmentCase]:
    from benchmark.solvers import GraphColoringSolver, GreedySolver

    cases: List[AssignmentCase] = []
    for label, solver in (
        ("greedy_width", GreedySolver(bench_path, order="width_desc")),
        ("graphcoloring", GraphColoringSolver(bench_path)),
    ):
        cases.append((label, solver.solve()))
    return cases


def build_cases(
    bench_path: str,
    random_count: int,
    seed: int,
    include_clean: bool,
    include_solvers: bool,
) -> List[AssignmentCase]:
    bench = _load_bench(bench_path)
    layers = int(bench["board"]["layers"])

    cases: List[AssignmentCase] = []
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
        cases.extend(_solver_cases(bench_path))

    return cases


def _assignment_payload(la: Optional[LayerAssignment]) -> Optional[Dict[str, int]]:
    if la is None:
        return None
    return {str(k): int(v) for k, v in la.assignment.items()}


def _proxy_payload(ev: Evaluator, la: Optional[LayerAssignment]) -> dict:
    if la is None:
        return {}
    proxy = ev.evaluate(la)
    return {
        "proxy_cost": round(proxy.cost, 2),
        "proxy_conflict": proxy.conflict_count,
        "proxy_crossing": proxy.crossing_count,
        "proxy_layer_usage": proxy.layer_usage,
        "proxy_via_estimate": proxy.via_estimate,
    }


def _print_result(label: str, entry: dict) -> None:
    if "error" in entry:
        print(f"  {label:<14} ERROR: {entry['error']}")
        return
    print(
        f"  {label:<14} wl={entry['real_wirelength_mm']:>8.2f}mm "
        f"seg={entry['real_segments']:>5} vias={entry['real_vias']:>4} "
        f"unrouted={entry['real_unrouted_nets']:>3} "
        f"time={entry['real_runtime_sec']:>6.1f}s"
    )


def run_phase12(
    bench_path: str,
    pcb_path: str,
    output_dir: str,
    random_count: int = 2,
    seed: int = 42,
    passes: int = 20,
    threads: int = 4,
    timeout_seconds: int = 300,
    include_clean: bool = True,
    include_solvers: bool = False,
    keep_pcbs: bool = False,
) -> List[dict]:
    bench = _load_bench(bench_path)
    layers = int(bench["board"]["layers"])
    buses = len(bench.get("buses", []))
    ev = Evaluator(bench_path)
    os.makedirs(output_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(pcb_path))[0]
    cases = build_cases(
        bench_path=bench_path,
        random_count=random_count,
        seed=seed,
        include_clean=include_clean,
        include_solvers=include_solvers,
    )

    print("\n" + "=" * 72)
    print("Phase 1.2 full-lock diagnostic")
    print(f"  bench={bench_path}")
    print(f"  pcb={pcb_path}")
    print(f"  buses={buses}, layers={layers}, cases={len(cases)}")
    print(f"  passes={passes}, threads={threads}, timeout={timeout_seconds}s")

    results: List[dict] = []

    for idx, (label, la) in enumerate(cases):
        case_base = f"{base}_phase12_{idx:02d}_{label}"
        work_pcb = os.path.join(output_dir, f"{case_base}.kicad_pcb")
        entry = {
            "idx": idx,
            "label": label,
            "guide_mode": "clean" if la is None else "full",
            "locked": None if la is None else True,
            "assignment": _assignment_payload(la),
        }
        entry.update(_proxy_payload(ev, la))

        try:
            if la is None:
                shutil.copyfile(pcb_path, work_pcb)
            else:
                export_pcb_with_assignment(
                    bench_path=bench_path,
                    assignment=la.assignment,
                    pcb_path=pcb_path,
                    output_path=work_pcb,
                    guide_mode="full",
                    locked=True,
                )

            real = route_pcb(
                work_pcb,
                output_dir=output_dir,
                passes=passes,
                threads=threads,
                timeout_seconds=timeout_seconds,
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
        except Exception as exc:
            entry["error"] = str(exc)
        finally:
            if not keep_pcbs and os.path.exists(work_pcb):
                os.remove(work_pcb)

        results.append(entry)
        _print_result(label, entry)

    out_path = os.path.join(output_dir, f"{base}_phase12_full_lock.json")
    payload = {
        "phase": "1.2",
        "purpose": "full locked trace diagnostic for layer-assignment visibility",
        "bench": bench_path,
        "pcb": pcb_path,
        "config": {
            "random_count": random_count,
            "seed": seed,
            "passes": passes,
            "threads": threads,
            "timeout_seconds": timeout_seconds,
            "include_clean": include_clean,
            "include_solvers": include_solvers,
        },
        "results": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    valid = [r for r in results if "real_wirelength_mm" in r]
    if valid:
        unrouted = [r["real_unrouted_nets"] for r in valid]
        vias = [r["real_vias"] for r in valid]
        wirelength = [r["real_wirelength_mm"] for r in valid]
        print("\nSummary")
        print(f"  valid={len(valid)}/{len(results)}")
        print(f"  unrouted range=[{min(unrouted)}, {max(unrouted)}]")
        print(f"  vias range=[{min(vias)}, {max(vias)}]")
        print(f"  wirelength range=[{min(wirelength):.2f}, {max(wirelength):.2f}]mm")
    print(f"  saved={out_path}")

    return results


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1.2: full locked trace diagnostic experiment"
    )
    parser.add_argument("--bench", required=True, help="Benchmark JSON path")
    parser.add_argument("--pcb", required=True, help="Source KiCad PCB path")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--random", type=int, default=2, help="Random cases to add")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--passes", type=int, default=20)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--no-clean", action="store_true", help="Skip clean baseline")
    parser.add_argument("--include-solvers", action="store_true",
                        help="Add Greedy and GraphColoring assignments")
    parser.add_argument("--keep-pcbs", action="store_true",
                        help="Keep generated guide PCB files")
    args = parser.parse_args(argv)

    run_phase12(
        bench_path=args.bench,
        pcb_path=args.pcb,
        output_dir=args.output_dir,
        random_count=args.random,
        seed=args.seed,
        passes=args.passes,
        threads=args.threads,
        timeout_seconds=args.timeout,
        include_clean=not args.no_clean,
        include_solvers=args.include_solvers,
        keep_pcbs=args.keep_pcbs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
