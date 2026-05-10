"""
PCB 总线层分配 - Baseline 对比实验

批量运行所有 baseline 在所有 benchmark 上，输出对比表格。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evaluator import Evaluator, LayerAssignment
from solvers import RandomSolver, GreedySolver, GraphColoringSolver, OptimalSearchSolver
from feedback_solver import FeedbackSolver


def run_experiment(bench_dir: str, output_path: str = None):
    """
    在指定目录下所有 .json benchmark 上运行所有 baseline。

    Args:
        bench_dir: benchmark JSON 文件目录
        output_path: 结果输出路径
    """
    # 收集所有 benchmark 文件（排除结果文件）
    json_files = sorted([
        os.path.join(bench_dir, f)
        for f in os.listdir(bench_dir)
        if f.endswith('.json') and 'result' not in f
    ])

    if not json_files:
        print(f"No JSON files found in {bench_dir}")
        return

    all_results = {}

    for bench_path in json_files:
        bench_name = os.path.basename(bench_path).replace('.json', '')
        print(f"\n--- {bench_name} ---")

        with open(bench_path) as f:
            data = json.load(f)

        n_buses = len(data["buses"])
        n_layers = data["board"]["layers"]
        print(f"  Buses: {n_buses}, Layers: {n_layers}")

        bench_results = {}

        # Random (5 seeds, 取最优)
        best_r = None
        for s in range(5):
            solver = RandomSolver(bench_path, seed=s)
            la, metrics, elapsed = solver.solve_and_evaluate()
            if best_r is None or metrics["cost"] < best_r[1]["cost"]:
                best_r = (la, metrics, elapsed)
        bench_results["Random"] = {
            "metrics": best_r[1],
            "time": round(best_r[3], 4) if len(best_r) > 3 else 0
        }
        bench_results["Random"]["time"] = round(best_r[2], 4)

        # Greedy
        solver = GreedySolver(bench_path)
        la, metrics, elapsed = solver.solve_and_evaluate()
        bench_results["Greedy"] = {"metrics": metrics, "time": round(elapsed, 4)}

        # Graph Coloring
        solver = GraphColoringSolver(bench_path)
        la, metrics, elapsed = solver.solve_and_evaluate()
        bench_results["GraphColoring"] = {"metrics": metrics, "time": round(elapsed, 4)}

        # Feedback optimization
        solver = FeedbackSolver(bench_path, max_iterations=30, patience=8)
        la, metrics, elapsed = solver.solve_and_evaluate()
        bench_results["Feedback"] = {"metrics": metrics, "time": round(elapsed, 4)}

        # Optimal (only for small instances)
        if n_buses <= 8:
            solver = OptimalSearchSolver(bench_path)
            la, metrics, elapsed = solver.solve_and_evaluate()
            bench_results["Optimal"] = {"metrics": metrics, "time": round(elapsed, 4)}

        all_results[bench_name] = bench_results

        # 打印该 benchmark 的结果
        print(f"  {'Method':<16} {'Cost':>8} {'Conflict':>9} {'Layers':>7} {'Time':>8}")
        for name, r in sorted(bench_results.items(),
                              key=lambda x: x[1]["metrics"]["cost"]):
            m = r["metrics"]
            print(f"  {name:<16} {m['cost']:>8.2f} {m['conflict_count']:>9} "
                  f"{m['layer_usage']:>7} {r['time']:>7.4f}s")

    # 汇总表格
    print("\n" + "=" * 80)
    print("SUMMARY: Conflict Count Comparison")
    print("=" * 80)

    # 收集所有方法名
    all_methods = set()
    for br in all_results.values():
        all_methods.update(br.keys())
    all_methods = sorted(all_methods)

    # 按场景分组
    scenarios = ["random", "crossing", "dense"]
    bus_counts = [5, 10, 20, 50]

    for scenario in scenarios:
        print(f"\n--- Scenario: {scenario} ---")
        header = f"{'Config':<30}"
        for m in all_methods:
            header += f" {m:>12}"
        print(header)
        print("-" * (30 + 13 * len(all_methods)))

        for buses in bus_counts:
            for layers in [2, 4]:
                key = f"buses{buses}_layers{layers}_{scenario}"
                if key not in all_results:
                    continue
                row = f"buses={buses}, layers={layers:<8}"
                for m in all_methods:
                    if m in all_results[key]:
                        cost = all_results[key][m]["metrics"]["cost"]
                        row += f" {cost:>12.1f}"
                    else:
                        row += f" {'N/A':>12}"
                print(row)

    # 保存结果
    if output_path:
        # 转换 key 为 str
        for bench_name in all_results:
            for method in all_results[bench_name]:
                pass  # already serializable
        with open(output_path, 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults saved to {output_path}")


if __name__ == '__main__':
    bench_dir = sys.argv[1] if len(sys.argv) > 1 else "benchmark/synthetic"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "benchmark/experiment_results.json"
    run_experiment(bench_dir, output_path)
