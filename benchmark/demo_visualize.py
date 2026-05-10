"""
可视化使用示例

运行: python benchmark/demo_visualize.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluator import Evaluator, LayerAssignment
from solvers import GreedySolver, GraphColoringSolver
from feedback_solver import FeedbackSolver
from visualize import draw_board_view, draw_comparison, draw_layer_detail


def demo_board_view():
    """示例1: 板面视图 — 对比 Greedy 和 Feedback 的层分配"""
    bench = "benchmark/synthetic/buses10_layers4_crossing.json"
    out_dir = "benchmark/demo_output"
    os.makedirs(out_dir, exist_ok=True)

    ev = Evaluator(bench)

    # Greedy
    g = GreedySolver(bench)
    la_greedy = g.solve()
    r = ev.evaluate(la_greedy)
    draw_board_view(bench, la_greedy.assignment,
                    save_path=f"{out_dir}/board_greedy.png",
                    show_conflicts=True,
                    title=f"Greedy (cost={r.cost:.1f}, conflicts={r.conflict_count})")

    # Feedback
    fb = FeedbackSolver(bench, max_iterations=30, patience=8)
    la_fb = fb.solve()
    r = ev.evaluate(la_fb)
    draw_board_view(bench, la_fb.assignment,
                    save_path=f"{out_dir}/board_feedback.png",
                    show_conflicts=True,
                    title=f"FeedbackOpt (cost={r.cost:.1f}, conflicts={r.conflict_count})")

    print(f"\n  Greedy:    {la_greedy.assignment}")
    print(f"  Feedback:  {la_fb.assignment}")


def demo_layer_detail():
    """示例2: 分层视图 — 每层独立显示"""
    bench = "benchmark/synthetic/buses20_layers4_dense.json"
    out_dir = "benchmark/demo_output"
    os.makedirs(out_dir, exist_ok=True)

    fb = FeedbackSolver(bench, max_iterations=30, patience=8)
    la = fb.solve()

    ev = Evaluator(bench)
    r = ev.evaluate(la)
    print(f"Feedback: cost={r.cost:.2f}, conflicts={r.conflict_count}")

    draw_layer_detail(bench, la.assignment,
                      save_path=f"{out_dir}/layers_dense.png")


def demo_comparison():
    """示例3: 多方案对比柱状图"""
    bench = "benchmark/synthetic/buses10_layers4_crossing.json"
    out_dir = "benchmark/demo_output"
    os.makedirs(out_dir, exist_ok=True)

    ev = Evaluator(bench)
    results = {}

    configs = [
        ("Random",      lambda: _random_best(bench, ev)),
        ("Greedy",      lambda: GreedySolver(bench).solve()),
        ("GraphColor",  lambda: GraphColoringSolver(bench).solve()),
        ("Feedback",    lambda: FeedbackSolver(bench, max_iterations=30, patience=8).solve()),
    ]

    for name, factory in configs:
        t0 = time.time()
        la = factory()
        elapsed = time.time() - t0
        r = ev.evaluate(la)
        results[name] = {"metrics": r.to_dict(), "time": round(elapsed, 4)}
        print(f"  {name:<12} cost={r.cost:>8.2f}  conflicts={r.conflict_count}  time={elapsed:.3f}s")

    draw_comparison(results,
                    save_path=f"{out_dir}/comparison.png",
                    title="buses10_layers4_crossing")

    # 保存结果 JSON
    with open(f"{out_dir}/comparison_results.json", "w") as f:
        json.dump(results, f, indent=2)


def _random_best(bench, ev, seeds=5):
    """Random 多次取最优"""
    import random
    from solvers import RandomSolver
    best = None
    best_cost = float("inf")
    for s in range(seeds):
        la = RandomSolver(bench, seed=s).solve()
        r = ev.evaluate(la)
        if r.cost < best_cost:
            best_cost = r.cost
            best = la
    return best


if __name__ == "__main__":
    print("=" * 60)
    print("Demo 1: Board View (Greedy vs Feedback)")
    print("=" * 60)
    demo_board_view()

    print()
    print("=" * 60)
    print("Demo 2: Layer Detail")
    print("=" * 60)
    demo_layer_detail()

    print()
    print("=" * 60)
    print("Demo 3: Comparison Bar Charts")
    print("=" * 60)
    demo_comparison()

    print()
    print("All figures saved to benchmark/demo_output/")
