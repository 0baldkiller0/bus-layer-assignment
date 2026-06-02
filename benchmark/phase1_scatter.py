"""
Phase 1.1: Proxy-Reality correlation experiment

Generates random layer assignments, computes proxy evaluator cost,
runs Freerouting headless on each, and collects real routing metrics.
Outputs a JSON for scatter plot analysis.
"""
import json
import os
import random
import sys
import time
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark.evaluator import Evaluator, LayerAssignment
from benchmark.freerouting_headless import route_pcb
from benchmark.pcb_exporter import export_pcb_with_assignment


def generate_random_assignments(
    bench_path: str,
    num_layers: int,
    n: int = 30,
    seed: int = 42,
) -> List[LayerAssignment]:
    """Generate N random layer assignments stratified across cost range."""
    with open(bench_path) as f:
        data = json.load(f)
    bus_ids = [b["id"] for b in data["buses"]]

    ev = Evaluator(bench_path)
    rng = random.Random(seed)

    assignments = []
    for _ in range(n * 3):  # oversample, then pick diverse ones
        a = {bid: rng.randint(0, num_layers - 1) for bid in bus_ids}
        la = LayerAssignment(assignment=a)
        result = ev.evaluate(la)
        assignments.append((result.cost, la))

    assignments.sort(key=lambda x: x[0])

    # Stratified sampling: pick evenly across the cost range
    if len(assignments) <= n:
        return [la for _, la in assignments]

    step = len(assignments) // n
    picked = [assignments[i][1] for i in range(0, len(assignments), step)]
    return picked[:n]


def run_phase1(
    bench_path: str,
    pcb_path: str,
    output_dir: str,
    n_assignments: int = 30,
    seed: int = 42,
):
    """Run Phase 1.1 for a single benchmark."""
    with open(bench_path) as f:
        bench = json.load(f)
    num_layers = bench["board"]["layers"]
    n_buses = len(bench["buses"])

    print(f"\n{'='*60}")
    print(f"Phase 1.1: {os.path.basename(bench_path)}")
    print(f"  Buses: {n_buses}, Layers: {num_layers}")
    print(f"  Generating {n_assignments} assignments...")

    ev = Evaluator(bench_path)
    assignments = generate_random_assignments(bench_path, num_layers, n_assignments, seed=seed)

    print(f"  Running Freerouting for each assignment...")

    results = []
    for idx, la in enumerate(assignments):
        proxy_result = ev.evaluate(la)
        proxy_cost = proxy_result.cost

        # Export stub-guided PCB with this assignment
        base = os.path.splitext(os.path.basename(pcb_path))[0]
        stub_pcb = os.path.join(output_dir, f"{base}_phase1_{idx:03d}.kicad_pcb")

        try:
            export_pcb_with_assignment(
                bench_path, la.assignment, pcb_path, stub_pcb,
                guide_mode="stub", stub_length=2.0, locked=True
            )

            # Run Freerouting headless
            real = route_pcb(
                stub_pcb,
                output_dir=output_dir,
                passes=20,
                threads=4,
                timeout_seconds=300,
            )

            entry = {
                "idx": idx,
                "proxy_cost": round(proxy_cost, 2),
                "proxy_conflict": proxy_result.conflict_count,
                "proxy_crossing": proxy_result.crossing_count,
                "proxy_layer_usage": proxy_result.layer_usage,
                "proxy_via_estimate": proxy_result.via_estimate,
                "real_wirelength_mm": real["total_wirelength"],
                "real_segments": real["total_segments"],
                "real_vias": real["total_vias"],
                "real_routed_nets": real["routed_net_count"],
                "real_unrouted_nets": real["unrouted_net_count"],
                "real_runtime_sec": real["runtime_seconds"],
                "assignment": {str(k): v for k, v in la.assignment.items()},
            }
            results.append(entry)

            print(f"  [{idx+1}/{n_assignments}] proxy_cost={proxy_cost:.1f}  "
                  f"real_wl={real['total_wirelength']:.0f}mm  "
                  f"vias={real['total_vias']}  "
                  f"unrouted={real['unrouted_net_count']}  "
                  f"({real['runtime_seconds']:.0f}s)")

        except Exception as e:
            print(f"  [{idx+1}/{n_assignments}] ERROR: {e}")
            results.append({
                "idx": idx,
                "proxy_cost": round(proxy_cost, 2),
                "error": str(e),
            })

        # Cleanup intermediate stub PCB to save disk
        if os.path.exists(stub_pcb):
            os.remove(stub_pcb)

    # Save results
    out_path = os.path.join(output_dir, f"{base}_phase1_scatter.json")
    with open(out_path, "w") as f:
        json.dump({"bench": bench_path, "results": results}, f, indent=2, default=str)

    print(f"\n  Saved {len(results)} entries to {out_path}")

    # Quick summary
    valid = [r for r in results if "real_wirelength_mm" in r]
    if valid:
        costs = [r["proxy_cost"] for r in valid]
        unrouted = [r["real_unrouted_nets"] for r in valid]
        print(f"  Proxy cost range: [{min(costs):.1f}, {max(costs):.1f}]")
        print(f"  Real unrouted range: [{min(unrouted)}, {max(unrouted)}]")

    return results


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Phase 1.1: Proxy-Reality scatter experiment")
    ap.add_argument("--bench", required=True, help="Benchmark JSON path")
    ap.add_argument("--pcb", required=True, help="Source KiCad PCB path")
    ap.add_argument("--output-dir", required=True, help="Output directory")
    ap.add_argument("--n", type=int, default=30, help="Number of random assignments")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    run_phase1(args.bench, args.pcb, args.output_dir, n_assignments=args.n, seed=args.seed)
