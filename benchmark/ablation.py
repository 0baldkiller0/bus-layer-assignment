"""
PCB 总线层分配 - 消融实验

分析反馈优化算法中各模块的贡献:
  A1. 完整 FeedbackOpt（对照组）
  A2. 去掉失败识别（随机选 bus 调整）
  A3. 只用 move 策略（去掉 swap）
  A4. 只用 high_conflict 检测（去掉层过载和可解冲突）
  A5. 随机初始 + 反馈（代替 Greedy 初始）
  A6. 不同迭代轮数: 5, 10, 20, 50
  A7. 不同候选数量: 10, 30, 50
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evaluator import Evaluator, LayerAssignment
from solvers import GreedySolver, GraphColoringSolver
from feedback_solver import (
    FeedbackSolver, FailureDetector, AdjustmentGenerator,
    FailurePattern, AdjustmentAction
)


# ============================================================
# 变体求解器定义
# ============================================================

class FeedbackNoFailureDetection(FeedbackSolver):
    """A2: 去掉失败识别，每轮随机选 bus 尝试 move"""

    @property
    def name(self):
        return "Feedback(no_failure)"

    def solve(self):
        import random
        self.iteration_logs = []

        current = self._initial_assignment()
        current_result = self.evaluator.evaluate(current)
        best = current
        best_cost = current_result.cost

        rng = random.Random(42)
        bus_ids = [b["id"] for b in self.buses]
        no_improve = 0

        for it in range(1, self.max_iterations + 1):
            # 随机选 bus 和目标层
            candidates = []
            for _ in range(self.max_candidates):
                bid = rng.choice(bus_ids)
                lid = rng.randint(0, self.num_layers - 1)
                if current.assignment.get(bid) == lid:
                    continue
                candidates.append(AdjustmentAction(
                    action_type="move", bus_id=bid, target_layer=lid
                ))

            # 评估候选
            best_candidate = None
            best_candidate_cost = current_result.cost
            for action in candidates:
                test = self._apply_action(current, action)
                test_result = self.evaluator.evaluate(test)
                if test_result.cost < best_candidate_cost:
                    best_candidate_cost = test_result.cost
                    best_candidate = action

            if best_candidate and best_candidate_cost < current_result.cost:
                current = self._apply_action(current, best_candidate)
                current_result = self.evaluator.evaluate(current)
                if current_result.cost < best_cost:
                    best = current
                    best_cost = current_result.cost
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                no_improve += 1

            self.iteration_logs.append(0)  # placeholder

            if no_improve >= self.patience:
                break

        return best


class FeedbackMoveOnly(FeedbackSolver):
    """A3: 只用 move 策略"""

    @property
    def name(self):
        return "Feedback(move_only)"

    def solve(self):
        self.iteration_logs = []
        current = self._initial_assignment()
        current_result = self.evaluator.evaluate(current)
        best = current
        best_cost = current_result.cost

        detector = FailureDetector(self.evaluator)
        no_improve = 0

        # 预计算包围盒
        bboxes = {}
        for bus in self.buses:
            sx, sy = bus["start_pos"]
            ex, ey = bus["end_pos"]
            w = bus.get("width", 0.5) / 2 + 0.2
            bboxes[bus["id"]] = (min(sx,ex)-w, min(sy,ey)-w, max(sx,ex)+w, max(sy,ey)+w)

        for it in range(1, self.max_iterations + 1):
            failures = detector.detect(current)
            if not failures:
                break

            # 只生成 move 候选
            priority_buses = set()
            for fp in failures:
                for bid in fp.bus_ids:
                    priority_buses.add(bid)

            candidates = []
            for bid in priority_buses:
                cur_layer = current.get_layer(bid)
                for lid in range(self.num_layers):
                    if lid != cur_layer:
                        candidates.append(AdjustmentAction(
                            action_type="move", bus_id=bid, target_layer=lid
                        ))

            best_candidate = None
            best_candidate_cost = current_result.cost
            for action in candidates[:self.max_candidates]:
                test = self._apply_action(current, action)
                test_result = self.evaluator.evaluate(test)
                if test_result.cost < best_candidate_cost:
                    best_candidate_cost = test_result.cost
                    best_candidate = action

            if best_candidate and best_candidate_cost < current_result.cost:
                current = self._apply_action(current, best_candidate)
                current_result = self.evaluator.evaluate(current)
                if current_result.cost < best_cost:
                    best = current
                    best_cost = current_result.cost
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                no_improve += 1

            self.iteration_logs.append(0)
            if no_improve >= self.patience:
                break

        return best


class FeedbackHighConflictOnly(FeedbackSolver):
    """A4: 只用 high_conflict 检测"""

    @property
    def name(self):
        return "Feedback(conflict_only)"

    def solve(self):
        self.iteration_logs = []
        current = self._initial_assignment()
        current_result = self.evaluator.evaluate(current)
        best = current
        best_cost = current_result.cost

        bboxes = {}
        for bus in self.buses:
            sx, sy = bus["start_pos"]
            ex, ey = bus["end_pos"]
            w = bus.get("width", 0.5) / 2 + 0.2
            bboxes[bus["id"]] = (min(sx,ex)-w, min(sy,ey)-w, max(sx,ex)+w, max(sy,ey)+w)

        no_improve = 0

        for it in range(1, self.max_iterations + 1):
            # 只做 high_conflict 检测
            conflict_counts = {}
            for i, ba in enumerate(self.buses):
                la = current.get_layer(ba["id"])
                if la is None:
                    continue
                bb_a = bboxes[ba["id"]]
                count = 0
                for j in range(i+1, len(self.buses)):
                    bb = self.buses[j]
                    if current.get_layer(bb["id"]) != la:
                        continue
                    bb_b = bboxes[bb["id"]]
                    if not (bb_a[2]<bb_b[0] or bb_b[2]<bb_a[0] or bb_a[3]<bb_b[1] or bb_b[3]<bb_a[1]):
                        count += 1
                conflict_counts[ba["id"]] = count

            high_conflict_buses = [bid for bid, c in conflict_counts.items() if c >= 2]
            if not high_conflict_buses:
                break

            # 生成 move 候选
            candidates = []
            for bid in high_conflict_buses:
                cur_layer = current.get_layer(bid)
                for lid in range(self.num_layers):
                    if lid != cur_layer:
                        candidates.append(AdjustmentAction(
                            action_type="move", bus_id=bid, target_layer=lid
                        ))

            best_candidate = None
            best_candidate_cost = current_result.cost
            for action in candidates[:self.max_candidates]:
                test = self._apply_action(current, action)
                test_result = self.evaluator.evaluate(test)
                if test_result.cost < best_candidate_cost:
                    best_candidate_cost = test_result.cost
                    best_candidate = action

            if best_candidate and best_candidate_cost < current_result.cost:
                current = self._apply_action(current, best_candidate)
                current_result = self.evaluator.evaluate(current)
                if current_result.cost < best_cost:
                    best = current
                    best_cost = current_result.cost
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                no_improve += 1

            self.iteration_logs.append(0)
            if no_improve >= self.patience:
                break

        return best


class FeedbackRandomInit(FeedbackSolver):
    """A5: 随机初始 + 反馈"""

    def __init__(self, benchmark_path, **kwargs):
        kwargs["initial_solver"] = "random"
        super().__init__(benchmark_path, **kwargs)

    @property
    def name(self):
        return "Feedback(random_init)"


# ============================================================
# 消融实验主函数
# ============================================================

def run_ablation(bench_path: str) -> dict:
    """
    在单个 benchmark 上运行消融实验。

    Returns:
        dict: {variant_name: {metrics, time, iterations}}
    """
    results = {}

    variants = [
        ("A1_Full", lambda: FeedbackSolver(bench_path, max_iterations=30, patience=8)),
        ("A2_NoFailure", lambda: FeedbackNoFailureDetection(bench_path, max_iterations=30, patience=8)),
        ("A3_MoveOnly", lambda: FeedbackMoveOnly(bench_path, max_iterations=30, patience=8)),
        ("A4_ConflictOnly", lambda: FeedbackHighConflictOnly(bench_path, max_iterations=30, patience=8)),
        ("A5_RandomInit", lambda: FeedbackRandomInit(bench_path, max_iterations=30, patience=8)),
        ("Greedy", lambda: GreedySolver(bench_path)),
        ("GraphColoring", lambda: GraphColoringSolver(bench_path)),
    ]

    for name, factory in variants:
        solver = factory()
        t0 = time.time()
        la = solver.solve()
        elapsed = time.time() - t0
        ev = Evaluator(bench_path)
        result = ev.evaluate(la)

        results[name] = {
            "metrics": result.to_dict(),
            "time": round(elapsed, 4),
            "iterations": len(getattr(solver, 'iteration_logs', []))
        }

    return results


def run_ablation_iterations(bench_path: str) -> dict:
    """A6: 不同迭代轮数对比"""
    results = {}
    for max_it in [5, 10, 20, 50]:
        solver = FeedbackSolver(bench_path, max_iterations=max_it, patience=max_it)
        t0 = time.time()
        la = solver.solve()
        elapsed = time.time() - t0
        ev = Evaluator(bench_path)
        result = ev.evaluate(la)
        results[f"iters_{max_it}"] = {
            "metrics": result.to_dict(),
            "time": round(elapsed, 4),
            "iterations": len(solver.iteration_logs)
        }
    return results


def run_ablation_candidates(bench_path: str) -> dict:
    """A7: 不同候选数量对比"""
    results = {}
    for max_c in [10, 30, 50]:
        solver = FeedbackSolver(bench_path, max_iterations=30, patience=8,
                                max_candidates_per_round=max_c)
        t0 = time.time()
        la = solver.solve()
        elapsed = time.time() - t0
        ev = Evaluator(bench_path)
        result = ev.evaluate(la)
        results[f"cand_{max_c}"] = {
            "metrics": result.to_dict(),
            "time": round(elapsed, 4),
            "iterations": len(solver.iteration_logs)
        }
    return results


if __name__ == '__main__':
    bench_path = sys.argv[1] if len(sys.argv) > 1 else "benchmark/synthetic/buses20_layers4_dense.json"

    with open(bench_path) as f:
        bench_data = json.load(f)

    print("=" * 80)
    print("ABLATION STUDY")
    print(f"Benchmark: {os.path.basename(bench_path)}")
    print(f"Buses: {len(bench_data['buses'])}, Layers: {bench_data['board']['layers']}")
    print("=" * 80)

    # A1-A5 + baselines
    print("\n--- A1-A5: Module Ablation ---")
    module_results = run_ablation(bench_path)

    header = '{:<20} {:>10} {:>9} {:>8} {:>8} {:>5}'.format(
        'Variant', 'Cost', 'Conflict', 'Via', 'Time', 'Iters')
    print(header)
    print('-' * 65)

    for name, r in sorted(module_results.items(), key=lambda x: x[1]["metrics"]["cost"]):
        m = r["metrics"]
        print('{:<20} {:>10.2f} {:>9} {:>8} {:>7.3f}s {:>5}'.format(
            name, m["cost"], m["conflict_count"], m["via_estimate"],
            r["time"], r["iterations"]))

    # A6: iteration count
    print("\n--- A6: Iteration Count Ablation ---")
    iter_results = run_ablation_iterations(bench_path)

    header = '{:<20} {:>10} {:>9} {:>8} {:>8}'.format(
        'Config', 'Cost', 'Conflict', 'Time', 'Iters')
    print(header)
    print('-' * 60)

    for name, r in sorted(iter_results.items(), key=lambda x: x[1]["metrics"]["cost"]):
        m = r["metrics"]
        print('{:<20} {:>10.2f} {:>9} {:>8} {:>7.3f}s'.format(
            name, m["cost"], m["conflict_count"], m["via_estimate"], r["time"]))

    # A7: candidate count
    print("\n--- A7: Candidate Count Ablation ---")
    cand_results = run_ablation_candidates(bench_path)

    header = '{:<20} {:>10} {:>9} {:>8} {:>8}'.format(
        'Config', 'Cost', 'Conflict', 'Time', 'Iters')
    print(header)
    print('-' * 60)

    for name, r in sorted(cand_results.items(), key=lambda x: x[1]["metrics"]["cost"]):
        m = r["metrics"]
        print('{:<20} {:>10.2f} {:>9} {:>8} {:>7.3f}s'.format(
            name, m["cost"], m["conflict_count"], m["via_estimate"], r["time"]))

    # Save
    all_results = {
        "benchmark": os.path.basename(bench_path),
        "module_ablation": {k: v for k, v in module_results.items()},
        "iteration_ablation": {k: v for k, v in iter_results.items()},
        "candidate_ablation": {k: v for k, v in cand_results.items()}
    }
    out_path = bench_path.replace('.json', '_ablation.json')
    with open(out_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {out_path}")
