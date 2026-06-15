"""
Analyze seed-only sweep results and produce summary tables.

Usage:
    python benchmark/analyze_formal_sweep.py --run-dir runs/phase1_formal_seed_only_24x6_20seeds
"""

import json
import os
import sys
from collections import defaultdict


def load_summary(run_dir: str) -> list[dict]:
    path = os.path.join(run_dir, "seed_sweep_summary.json")
    if not os.path.exists(path):
        print(f"ERROR: {path} not found")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def method_order(method: str) -> int:
    order = {"all_L0": 0, "stripe": 1, "greedy": 2, "feedback": 3, "split_outer_inner": 4}
    return order.get(method, 99)


def print_summary_table(rows: list[dict]):
    """Print per-method aggregate table."""
    methods = defaultdict(list)
    for row in rows:
        methods[row["label"]].append(row)

    print("\n## Aggregate Summary (mean ± std across seeds)\n")
    print(f"| Method | Seeds | Avg Routed | Avg Unrouted | Avg Wirelength | Avg Segments | Avg Vias | Avg Proxy Cost | Avg Conflict |")
    print(f"|---|---:|---:|---:|---:|---:|---:|---:|---:|")

    for method in sorted(methods, key=method_order):
        entries = methods[method]
        n = len(entries)
        routed = [e.get("routed", 0) for e in entries]
        unrouted = [e.get("unrouted", 0) for e in entries]
        wl = [e.get("wirelength_mm", 0) for e in entries]
        seg = [e.get("segments", 0) for e in entries]
        vias = [e.get("vias", 0) for e in entries]
        proxy = [e.get("proxy_cost", 0) for e in entries]
        conflict = [e.get("proxy_conflict", 0) for e in entries]

        def fmt_mean_std(vals, decimals=1):
            m = sum(vals) / n
            if n > 1:
                var = sum((v - m) ** 2 for v in vals) / (n - 1)
                s = var ** 0.5
                return f"{m:.{decimals}f} ± {s:.{decimals}f}"
            return f"{m:.{decimals}f}"

        print(f"| {method:<20} | {n} | {fmt_mean_std(routed, 0)} | {fmt_mean_std(unrouted, 0)} | "
              f"{fmt_mean_std(wl, 1)} | {fmt_mean_std(seg, 0)} | {fmt_mean_std(vias, 0)} | "
              f"{fmt_mean_std(proxy, 1)} | {fmt_mean_std(conflict, 0)} |")


def print_per_seed_table(rows: list[dict]):
    """Print per-seed comparison of greedy vs feedback."""
    print("\n## Per-Seed: Greedy vs Feedback\n")
    print(f"| Seed | Greedy Routed | FB Routed | Greedy WL | FB WL | Greedy Vias | FB Vias | Greedy Proxy | FB Proxy | FB vs Greedy |")
    print(f"|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    seed_entries = defaultdict(dict)
    for row in rows:
        seed_entries[row["variant"]][row["label"]] = row

    for seed_name in sorted(seed_entries):
        se = seed_entries[seed_name]
        g = se.get("greedy", {})
        f = se.get("feedback", {})
        if not g or not f:
            continue

        g_r = g.get("routed", "?")
        f_r = f.get("routed", "?")
        g_wl = g.get("wirelength_mm", 0)
        f_wl = f.get("wirelength_mm", 0)
        g_v = g.get("vias", 0)
        f_v = f.get("vias", 0)
        g_p = g.get("proxy_cost", 0)
        f_p = f.get("proxy_cost", 0)

        if g_r != f_r:
            diff = f"ROUTED DIFF: g={g_r} f={f_r}"
        elif g_p == f_p and g_wl == f_wl:
            diff = "identical"
        else:
            wl_diff = f_wl - g_wl
            via_diff = f_v - g_v
            parts = []
            if abs(wl_diff) > 0.01:
                parts.append(f"WL {'+' if wl_diff > 0 else ''}{wl_diff:.1f}")
            if via_diff != 0:
                parts.append(f"Vias {'+' if via_diff > 0 else ''}{via_diff}")
            parts.append(f"Proxy {'+' if f_p > g_p else ''}{f_p - g_p:.1f}")
            diff = ", ".join(parts) if parts else "same metrics"

        print(f"| {seed_name:<20} | {g_r} | {f_r} | {g_wl:.1f} | {f_wl:.1f} | "
              f"{g_v} | {f_v} | {g_p:.1f} | {f_p:.1f} | {diff} |")


def print_best_worst(rows: list[dict]):
    """Show best and worst methods per metric."""
    print("\n## Best / Worst per Metric\n")
    methods = defaultdict(list)
    for row in rows:
        methods[row["label"]].append(row)

    for metric, label, lower_better in [
        ("unrouted", "Unrouted", True),
        ("wirelength_mm", "Wirelength", True),
        ("vias", "Vias", True),
        ("segments", "Segments", True),
    ]:
        avgs = {}
        for method, entries in methods.items():
            vals = [e.get(metric, 0) for e in entries if metric in e]
            if vals:
                avgs[method] = sum(vals) / len(vals)

        if lower_better:
            best = min(avgs, key=avgs.get)
            worst = max(avgs, key=avgs.get)
        else:
            best = max(avgs, key=avgs.get)
            worst = min(avgs, key=avgs.get)

        print(f"  {label:<15}: best={best} ({avgs[best]:.1f}), worst={worst} ({avgs[worst]:.1f})")


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_formal_sweep.py --run-dir <dir> [--markdown <out.md>]")
        sys.exit(1)

    run_dir = sys.argv[sys.argv.index("--run-dir") + 1] if "--run-dir" in sys.argv else sys.argv[1]
    md_out = None
    if "--markdown" in sys.argv:
        md_out = sys.argv[sys.argv.index("--markdown") + 1]

    rows = load_summary(run_dir)
    if not rows:
        return

    # Filter to only the methods we care about
    target_methods = {"all_L0", "stripe", "greedy", "feedback", "split_outer_inner"}
    rows = [r for r in rows if r.get("label") in target_methods]

    print(f"Loaded {len(rows)} entries from {run_dir}")
    print(f"Methods: {sorted(set(r['label'] for r in rows))}")
    print(f"Variants: {sorted(set(r['variant'] for r in rows))}")

    if md_out:
        import io
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()

    print_summary_table(rows)
    print_per_seed_table(rows)
    print_best_worst(rows)

    if md_out:
        sys.stdout = old_stdout
        with open(md_out, "w", encoding="utf-8") as f:
            f.write(buffer.getvalue())
        print(f"Markdown saved to {md_out}")


if __name__ == "__main__":
    main()
