"""
PCB 总线层分配 - Freerouting 验证工作流

使用方法:
  1. python freerouting_workflow.py prepare --bench benchmark/bench4.json --pcb bench4/bm4.unrouted.kicad_pcb
     → 生成 clean PCB 和若干 stub-guided PCB
  2. 用户在 KiCad 中用 Freerouting 打开每个 PCB 进行布线
  3. python freerouting_workflow.py parse --dir bench4/ --bench benchmark/bench4.json
     → 解析所有布线后的 PCB，对比结果
"""

import argparse
import json
import os
import sys

_bench_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _bench_dir)
sys.path.insert(0, os.path.dirname(_bench_dir))

from evaluator import Evaluator, LayerAssignment
from solvers import GreedySolver, GraphColoringSolver
from feedback_solver import FeedbackSolver
from pcb_exporter import export_pcb_with_assignment, export_pcb_clean
from routed_parser import parse_routed_pcb


def _parse_methods(methods: str):
    return [m.strip().lower() for m in methods.split(',') if m.strip()]


def _write_markdown_summary(results: dict, out_path: str):
    lines = [
        "# Routed Comparison",
        "",
        "| Method | Routed Nets | Unrouted | Vias | Wirelength | Segments |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, r in sorted(results.items(), key=lambda x: x[1].get("unrouted_count", 0)):
        routed = r.get("routed_net_count", "N/A")
        lines.append(
            f"| {name} | {routed} | {r['unrouted_count']} | {r['total_vias']} | "
            f"{r['total_wirelength']:.1f} | {r['total_segments']} |"
        )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def prepare(
    bench_path: str,
    pcb_path: str,
    output_dir: str = None,
    methods: str = "clean,greedy,graphcoloring,feedback",
    guide: str = "stub",
    stub_length: float = 2.0,
    unlocked: bool = False
):
    """
    运行所有求解器，为每个方案生成引导 PCB 文件。

    Args:
        bench_path: benchmark JSON 路径
        pcb_path: 原始 KiCad PCB 文件
        output_dir: 输出目录
        methods: 逗号分隔的方法列表，支持 clean/greedy/graphcoloring/feedback
        guide: guided PCB 的导出模式，默认 stub
        stub_length: stub 长度（mm）
        unlocked: 是否导出未锁定 guide
    """
    if output_dir is None:
        output_dir = os.path.dirname(pcb_path) or '.'
    os.makedirs(output_dir, exist_ok=True)

    with open(bench_path) as f:
        bench = json.load(f)

    n_buses = len(bench["buses"])
    n_layers = bench["board"]["layers"]
    print(f"Benchmark: {os.path.basename(bench_path)}")
    print(f"  Buses: {n_buses}, Layers: {n_layers}")

    selected_methods = _parse_methods(methods)
    ev = Evaluator(bench_path)
    solvers = {}
    solver_results = {}

    if "greedy" in selected_methods:
        g = GreedySolver(bench_path)
        la = g.solve()
        solvers["greedy"] = la
        r = ev.evaluate(la)
        solver_results["greedy"] = r.to_dict()
        print(f"  Greedy: cost={r.cost:.2f}, conflicts={r.conflict_count}")

    if "graphcoloring" in selected_methods:
        gc = GraphColoringSolver(bench_path)
        la = gc.solve()
        solvers["graphcoloring"] = la
        r = ev.evaluate(la)
        solver_results["graphcoloring"] = r.to_dict()
        print(f"  GraphColoring: cost={r.cost:.2f}, conflicts={r.conflict_count}")

    if "feedback" in selected_methods:
        fb = FeedbackSolver(bench_path, max_iterations=30, patience=8)
        la = fb.solve()
        solvers["feedback"] = la
        r = ev.evaluate(la)
        solver_results["feedback"] = r.to_dict()
        print(f"  Feedback: cost={r.cost:.2f}, conflicts={r.conflict_count}")

    # 生成引导 PCB
    base = os.path.splitext(os.path.basename(pcb_path))[0]
    outputs = []

    if "clean" in selected_methods:
        clean_path = os.path.join(output_dir, f"{base}_clean.kicad_pcb")
        export_pcb_clean(pcb_path, clean_path)
        outputs.append(("clean", clean_path))

    # 各求解器版本
    for name, la in solvers.items():
        out_name = f"{base}_{name}_{guide}.kicad_pcb"
        out_path = os.path.join(output_dir, out_name)
        export_pcb_with_assignment(
            bench_path, la.assignment, pcb_path, out_path,
            guide_mode=guide,
            stub_length=stub_length,
            locked=not unlocked
        )
        outputs.append((f"{name}_{guide}", out_path))

    # 保存 assignment 供后续解析
    assignments_path = os.path.join(output_dir, f"{base}_assignments.json")
    with open(assignments_path, 'w', encoding='utf-8') as f:
        data = {}
        for name, la in solvers.items():
            data[name] = {str(k): v for k, v in la.assignment.items()}
        json.dump(data, f, indent=2)

    # 保存 export config 供复现和文档
    export_config = {
        "bench": bench_path,
        "source_pcb": pcb_path,
        "guide": guide,
        "stub_length": stub_length,
        "locked": not unlocked,
        "solvers": solver_results
    }
    export_config_path = os.path.join(output_dir, f"{base}_export_config.json")
    with open(export_config_path, 'w', encoding='utf-8') as f:
        json.dump(export_config, f, indent=2)

    generated_path = os.path.join(output_dir, f"{base}_generated_files.json")
    with open(generated_path, 'w', encoding='utf-8') as f:
        json.dump({
            "bench": bench_path,
            "source_pcb": pcb_path,
            "guide": guide,
            "stub_length": stub_length,
            "locked": not unlocked,
            "outputs": [{"method": name, "path": path} for name, path in outputs]
        }, f, indent=2)

    print(f"\nGenerated {len(outputs)} PCB files:")
    for name, path in outputs:
        print(f"  {name}: {path}")
    print(f"\nAssignments saved to: {assignments_path}")
    print(f"Generated file manifest saved to: {generated_path}")
    print("\nNext steps:")
    print("  1. Open each PCB in KiCad")
    print("  2. Run Freerouting (Tools → Freerouting) on each")
    print("  3. Save routed PCBs with _routed suffix")
    print("  4. Run: python freerouting_workflow.py parse --dir <dir> --bench <bench>")


def parse_results(pcb_dir: str, bench_path: str):
    """
    Parse all _routed PCB files in a directory and compare results.

    Expected file naming: <base>_<method>_routed.kicad_pcb
    Method is extracted as the suffix between the base filename stem
    and '_routed.kicad_pcb'.
    """
    results = {}

    # Try to load generated_files.json to get method→file mapping
    manifest = None
    gf_path = os.path.join(pcb_dir, os.path.basename(pcb_dir) + "_generated_files.json")
    if not os.path.exists(gf_path):
        for f in os.listdir(pcb_dir):
            if f.endswith("_generated_files.json"):
                gf_path = os.path.join(pcb_dir, f)
                break
    if os.path.exists(gf_path):
        try:
            with open(gf_path) as fh:
                manifest = json.load(fh)
        except Exception:
            pass

    # Build method name lookup from manifest outputs
    method_from_path = {}
    if manifest and "outputs" in manifest:
        for entry in manifest["outputs"]:
            method_from_path[os.path.basename(entry["path"])] = entry["method"]

    for fname in sorted(os.listdir(pcb_dir)):
        if not (fname.endswith('_routed.kicad_pcb') or fname.endswith('.routed.kicad_pcb')):
            continue

        # Derive the un-routed source filename
        if fname.endswith('_routed.kicad_pcb'):
            source_name = fname.replace('_routed.kicad_pcb', '.kicad_pcb')
        else:
            source_name = fname.replace('.routed.kicad_pcb', '.kicad_pcb')

        # Prefer method name from manifest
        if source_name in method_from_path:
            method = method_from_path[source_name]
        else:
            # Fallback: extract method from filename
            stem = fname.replace('_routed.kicad_pcb', '').replace('.routed.kicad_pcb', '')
            # The method suffix is after the PCB base name
            # e.g. bm4.unrouted_clean_routed → clean
            #      bm4.unrouted_greedy_stub_routed → greedy_stub
            parts = stem.rsplit('_', 2)
            if len(parts) >= 3 and parts[-2] in ("greedy", "feedback", "graphcoloring"):
                method = f"{parts[-2]}_{parts[-1]}"
            else:
                method = parts[-1]

        pcb_path = os.path.join(pcb_dir, fname)
        print(f"Parsing: {fname}  (method={method})")
        result = parse_routed_pcb(pcb_path, bench_path)
        results[method] = result

    if not results:
        print(f"No *_routed.kicad_pcb files found in {pcb_dir}")
        print("Expected files like: bm4_clean_routed.kicad_pcb")
        return

    # 对比
    print(f"\n{'Method':<20} {'Routed':>8} {'Wirelength':>12} {'Vias':>6} {'Unrouted':>9}")
    print("-" * 62)

    for name, r in sorted(results.items(),
                          key=lambda x: (x[1].get('unrouted_count', 0),
                                         x[1].get('total_wirelength', float('inf')))):
        routed = r.get('routed_net_count', 'N/A')
        print(f"{name:<20} {routed:>8} {r['total_wirelength']:>12.1f} "
              f"{r['total_vias']:>6} {r['unrouted_count']:>9}")

    # 保存
    out_path = os.path.join(pcb_dir, "routed_comparison.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, default=str)
    md_path = os.path.join(pcb_dir, "routed_comparison.md")
    _write_markdown_summary(results, md_path)

    # 保存 per-net connectivity 详情
    pn_path = os.path.join(pcb_dir, "per_net_connectivity.json")
    per_net_data = {}
    for name, r in results.items():
        per_net_data[name] = r.get("per_net", {})
    with open(pn_path, 'w', encoding='utf-8') as f:
        json.dump(per_net_data, f, indent=2, default=str)

    print(f"\nSaved to {out_path}")
    print(f"Saved to {md_path}")
    print(f"Saved to {pn_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Freerouting verification workflow')
    sub = parser.add_subparsers(dest='command')

    # prepare
    p_prep = sub.add_parser('prepare', help='Generate guided PCB files')
    p_prep.add_argument('--bench', required=True, help='Benchmark JSON')
    p_prep.add_argument('--pcb', required=True, help='Original KiCad PCB')
    p_prep.add_argument('--output-dir', default=None, help='Output directory')
    p_prep.add_argument('--methods', default='clean,greedy,graphcoloring,feedback',
                        help='Comma-separated methods to export')
    p_prep.add_argument('--guide', choices=['stub', 'full'], default='stub',
                        help='Guided PCB export mode')
    p_prep.add_argument('--stub-length', type=float, default=2.0,
                        help='Stub length in mm for stub guide')
    p_prep.add_argument('--unlocked', action='store_true',
                        help='Create guide traces unlocked')

    # parse
    p_parse = sub.add_parser('parse', help='Parse routed PCB results')
    p_parse.add_argument('--dir', required=True, help='Directory with routed PCBs')
    p_parse.add_argument('--bench', required=True, help='Benchmark JSON')

    args = parser.parse_args()

    if args.command == 'prepare':
        prepare(
            args.bench, args.pcb, args.output_dir,
            methods=args.methods,
            guide=args.guide,
            stub_length=args.stub_length,
            unlocked=args.unlocked
        )
    elif args.command == 'parse':
        parse_results(args.dir, args.bench)
    else:
        parser.print_help()
