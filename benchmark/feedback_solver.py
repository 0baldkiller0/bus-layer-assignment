"""
PCB 总线层分配问题 - 可布通性反馈优化算法

核心创新模块：在 baseline 层分配基础上，通过多轮反馈迭代改善分配质量。

算法流程:
  初始分配 (Greedy)
  → 可布通性评估
  → 失败模式识别
  → 生成候选调整
  → 模拟退火选择
  → 重新评估
  → 直到收敛或达到迭代上限
"""

import json
import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from evaluator import Evaluator, LayerAssignment
from solvers import GreedySolver, BaseSolver


# ============================================================
# 失败模式定义
# ============================================================

@dataclass
class FailurePattern:
    """识别出的失败模式"""
    pattern_type: str       # "high_conflict" | "layer_overload" | "crossing_hotspot" | "isolated_conflict"
    severity: float         # 严重程度 0-1
    bus_ids: List[int]      # 涉及的 bus
    layer_id: Optional[int] # 涉及的层
    description: str = ""   # 可读描述


@dataclass
class AdjustmentAction:
    """候选调整动作"""
    action_type: str        # "move" | "swap"
    bus_id: Optional[int]   # 主 bus
    target_layer: Optional[int]  # 目标层
    swap_bus_id: Optional[int] = None  # swap 时的对端 bus


@dataclass
class IterationLog:
    """单轮迭代日志"""
    iteration: int
    cost: float
    conflict_count: int
    action_taken: Optional[AdjustmentAction]
    failures_detected: int
    candidates_evaluated: int
    accepted: bool


# ============================================================
# 失败模式识别器
# ============================================================

class FailureDetector:
    """
    识别当前层分配中的失败模式。

    支持的失败类型:
    1. high_conflict: 某 bus 与同层多个 bus 冲突
    2. layer_overload: 某层 bus 密度过高
    3. isolated_conflict: 两个 bus 单独冲突，移到不同层可解决
    """

    def __init__(self, evaluator: Evaluator):
        self.ev = evaluator
        self.buses = evaluator.buses
        self.num_layers = evaluator.num_layers

        # 预计算包围盒
        self._bboxes = {}
        for bus in self.buses:
            sx, sy = bus["start_pos"]
            ex, ey = bus["end_pos"]
            w = bus.get("width", 0.5) / 2 + 0.2
            self._bboxes[bus["id"]] = (
                min(sx, ex) - w, min(sy, ey) - w,
                max(sx, ex) + w, max(sy, ey) + w
            )

    def detect(self, assignment: LayerAssignment) -> List[FailurePattern]:
        patterns = []

        conflict_counts = self._count_conflicts_per_bus(assignment)
        patterns.extend(self._detect_high_conflict(assignment, conflict_counts))
        patterns.extend(self._detect_layer_overload(assignment, conflict_counts))
        patterns.extend(self._detect_isolated_conflicts(assignment, conflict_counts))

        patterns.sort(key=lambda p: p.severity, reverse=True)
        return patterns

    def _count_conflicts_per_bus(self, assignment: LayerAssignment) -> Dict[int, int]:
        counts = {bus["id"]: 0 for bus in self.buses}

        for i, ba in enumerate(self.buses):
            la = assignment.get_layer(ba["id"])
            if la is None:
                continue
            bb_a = self._bboxes[ba["id"]]

            for j in range(i + 1, len(self.buses)):
                bb = self.buses[j]
                lb = assignment.get_layer(bb["id"])
                if lb is None or lb != la:
                    continue
                bb_b = self._bboxes[bb["id"]]

                if self.ev._bus_conflicts(ba, bb):
                    counts[ba["id"]] += 1
                    counts[bb["id"]] += 1

        return counts

    def get_conflict_pairs(self, assignment: LayerAssignment) -> List[Tuple[int, int]]:
        """返回所有冲突对"""
        pairs = []
        for i, ba in enumerate(self.buses):
            la = assignment.get_layer(ba["id"])
            if la is None:
                continue
            bb_a = self._bboxes[ba["id"]]
            for j in range(i + 1, len(self.buses)):
                bb = self.buses[j]
                lb = assignment.get_layer(bb["id"])
                if lb is None or lb != la:
                    continue
                bb_b = self._bboxes[bb["id"]]
                if self.ev._bus_conflicts(ba, bb):
                    pairs.append((ba["id"], bb["id"]))
        return pairs

    def _detect_high_conflict(
        self, assignment: LayerAssignment, conflict_counts: Dict[int, int]
    ) -> List[FailurePattern]:
        patterns = []
        if not conflict_counts:
            return patterns

        max_conflict = max(conflict_counts.values())
        if max_conflict == 0:
            return patterns

        for bid, count in conflict_counts.items():
            if count >= 2 and count >= max_conflict * 0.5:
                lid = assignment.get_layer(bid)
                severity = count / max(max_conflict, 1)
                patterns.append(FailurePattern(
                    pattern_type="high_conflict",
                    severity=severity,
                    bus_ids=[bid],
                    layer_id=lid,
                    description=f"Bus {bid} has {count} conflicts on layer {lid}"
                ))

        return patterns

    def _detect_layer_overload(
        self, assignment: LayerAssignment, conflict_counts: Dict[int, int]
    ) -> List[FailurePattern]:
        patterns = []

        layer_info: Dict[int, Dict] = {}
        for lid in range(self.num_layers):
            buses_on_layer = assignment.buses_on_layer(lid)
            total_conflicts = sum(
                conflict_counts.get(bid, 0) for bid in buses_on_layer
            ) // 2
            layer_info[lid] = {
                "bus_count": len(buses_on_layer),
                "total_conflicts": total_conflicts,
                "bus_ids": buses_on_layer
            }

        avg_count = sum(d["bus_count"] for d in layer_info.values()) / max(self.num_layers, 1)
        for lid, info in layer_info.items():
            if info["bus_count"] > avg_count * 1.5 and info["total_conflicts"] > 0:
                severity = info["total_conflicts"] / max(
                    max(d["total_conflicts"] for d in layer_info.values()), 1
                )
                patterns.append(FailurePattern(
                    pattern_type="layer_overload",
                    severity=severity * 0.8,
                    bus_ids=info["bus_ids"],
                    layer_id=lid,
                    description=(f"Layer {lid} overloaded: {info['bus_count']} buses, "
                                 f"{info['total_conflicts']} conflicts")
                ))

        return patterns

    def _detect_isolated_conflicts(
        self, assignment: LayerAssignment, conflict_counts: Dict[int, int]
    ) -> List[FailurePattern]:
        patterns = []

        for i, ba in enumerate(self.buses):
            la = assignment.get_layer(ba["id"])
            if la is None:
                continue
            bb_a = self._bboxes[ba["id"]]

            for j in range(i + 1, len(self.buses)):
                bb = self.buses[j]
                lb = assignment.get_layer(bb["id"])
                if lb is None or lb != la:
                    continue
                bb_b = self._bboxes[bb["id"]]

                if self.ev._bus_conflicts(ba, bb):
                    if conflict_counts.get(ba["id"], 0) <= 1 and conflict_counts.get(bb["id"], 0) <= 1:
                        patterns.append(FailurePattern(
                            pattern_type="isolated_conflict",
                            severity=0.3,
                            bus_ids=[ba["id"], bb["id"]],
                            layer_id=la,
                            description=f"Isolated conflict: Bus {ba['id']} vs Bus {bb['id']} on layer {la}"
                        ))

        return patterns


# ============================================================
# 调整策略生成器
# ============================================================

class AdjustmentGenerator:
    """
    根据失败模式生成候选调整动作，预评分排序。

    策略:
    1. move: 将高冲突 bus 移到其他层，用快速冲突估计排序
    2. swap: 交换两个不同层上的 bus
    3. 随机探索: 补充随机候选确保搜索空间
    """

    def __init__(self, evaluator: Evaluator):
        self.ev = evaluator
        self.buses = evaluator.buses
        self.num_layers = evaluator.num_layers

        self._bboxes = {}
        for bus in self.buses:
            sx, sy = bus["start_pos"]
            ex, ey = bus["end_pos"]
            w = bus.get("width", 0.5) / 2 + 0.2
            self._bboxes[bus["id"]] = (
                min(sx, ex) - w, min(sy, ey) - w,
                max(sx, ex) + w, max(sy, ey) + w
            )

    def _count_conflicts_on_layer(
        self, bid: int, lid: int, assignment: LayerAssignment
    ) -> int:
        """快速计算 bus bid 在 layer lid 上会产生的冲突数"""
        bb_a = self._bboxes[bid]
        count = 0
        for ob in self.buses:
            oid = ob["id"]
            if oid == bid:
                continue
            if assignment.get_layer(oid) != lid:
                continue
            bb_b = self._bboxes[oid]
            if self.ev._bus_conflicts(self.ev.bus_by_id[bid], ob):
                count += 1
        return count

    def generate(
        self, assignment: LayerAssignment, failures: List[FailurePattern],
        conflict_pairs: List[Tuple[int, int]],
        max_candidates: int = 50
    ) -> List[tuple]:  # List[(score, AdjustmentAction)]
        scored_candidates = []
        seen = set()

        # 收集需要处理的 bus（冲突 bus + 邻居 bus）
        priority_buses = set()
        for fp in failures:
            for bid in fp.bus_ids:
                priority_buses.add(bid)
        for a, b in conflict_pairs:
            priority_buses.add(a)
            priority_buses.add(b)

        # 对每个优先 bus 生成并评分 move 候选
        for bid in priority_buses:
            cur_layer = assignment.get_layer(bid)
            if cur_layer is None:
                continue
            old_conflicts = self._count_conflicts_on_layer(bid, cur_layer, assignment)

            for lid in range(self.num_layers):
                if lid == cur_layer:
                    continue
                key = ("move", bid, lid)
                if key in seen:
                    continue
                seen.add(key)
                new_conflicts = self._count_conflicts_on_layer(bid, lid, assignment)
                score = new_conflicts - old_conflicts  # 负值=改善
                scored_candidates.append((score, AdjustmentAction(
                    action_type="move", bus_id=bid, target_layer=lid
                )))

        # swap 候选（与目标层的 bus 交换）
        for bid in priority_buses:
            cur_layer = assignment.get_layer(bid)
            if cur_layer is None:
                continue
            old_conflicts = self._count_conflicts_on_layer(bid, cur_layer, assignment)

            for target_lid in range(self.num_layers):
                if target_lid == cur_layer:
                    continue
                for obid in assignment.buses_on_layer(target_lid):
                    key = ("swap", min(bid, obid), max(bid, obid))
                    if key in seen:
                        continue
                    seen.add(key)
                    # swap 的快速估计: bid 去 target_lid, obid 来 cur_layer
                    bid_new = self._count_conflicts_on_layer(bid, target_lid, assignment)
                    obid_old = self._count_conflicts_on_layer(obid, target_lid, assignment)
                    obid_new = self._count_conflicts_on_layer(obid, cur_layer, assignment)
                    score = (bid_new - old_conflicts) + (obid_new - obid_old)
                    scored_candidates.append((score, AdjustmentAction(
                        action_type="swap", bus_id=bid,
                        target_layer=target_lid, swap_bus_id=obid
                    )))

        # 按分数排序（低分 = 更多改善）
        scored_candidates.sort(key=lambda x: x[0])

        # 如果候选太少，补充随机探索（有最大尝试次数防止死循环）
        all_bus_ids = [b["id"] for b in self.buses]
        rng = random.Random(42)
        attempts = 0
        while len(scored_candidates) < max_candidates and attempts < max_candidates * 10:
            attempts += 1
            bid = rng.choice(all_bus_ids)
            lid = rng.randint(0, self.num_layers - 1)
            if assignment.get_layer(bid) == lid:
                continue
            key = ("move", bid, lid)
            if key not in seen:
                seen.add(key)
                scored_candidates.append((0, AdjustmentAction(
                    action_type="move", bus_id=bid, target_layer=lid
                )))

        return scored_candidates[:max_candidates]


# ============================================================
# 反馈优化求解器
# ============================================================

class FeedbackSolver(BaseSolver):
    """
    可布通性反馈优化求解器。

    算法:
    1. 用 Greedy 生成初始分配
    2. 评估当前分配
    3. 识别失败模式
    4. 生成候选调整
    5. 模拟退火选择最优候选
    6. 应用调整，回到步骤 2
    7. 直到收敛或达到迭代上限
    """

    def __init__(
        self,
        benchmark_path: str,
        initial_solver: str = "greedy",
        max_iterations: int = 50,
        patience: int = 15,
        max_candidates_per_round: int = 50,
        initial_temp: float = 5.0,
        cooling_rate: float = 0.92,
        seed: int = 42,
        seed_via_weight: float = 2.0,
        conflict_model: str = "bbox",
    ):
        super().__init__(benchmark_path)
        self.bench_path = benchmark_path
        self.initial_solver = initial_solver
        self.max_iterations = max_iterations
        self.patience = patience
        self.max_candidates = max_candidates_per_round
        self.initial_temp = initial_temp
        self.cooling_rate = cooling_rate
        self.seed = seed
        self.seed_via_weight = seed_via_weight
        self.conflict_model = conflict_model
        if conflict_model != "bbox":
            self.evaluator = Evaluator(benchmark_path, conflict_model=conflict_model)

        self.detector = FailureDetector(self.evaluator)
        self.generator = AdjustmentGenerator(self.evaluator)

        self.iteration_logs: List[IterationLog] = []

    @property
    def name(self) -> str:
        return "FeedbackOpt"

    def _initial_assignment(self) -> LayerAssignment:
        if self.initial_solver == "greedy":
            solver = GreedySolver(self.bench_path, conflict_model=self.conflict_model)
            return solver.solve()
        else:
            rng = random.Random(self.seed)
            return LayerAssignment(assignment={
                bus["id"]: rng.randint(0, self.num_layers - 1)
                for bus in self.buses
            })

    def _apply_action(
        self, assignment: LayerAssignment, action: AdjustmentAction
    ) -> LayerAssignment:
        new_a = dict(assignment.assignment)

        if action.action_type == "move":
            new_a[action.bus_id] = action.target_layer

        elif action.action_type == "swap":
            # 交换两个 bus 的层
            new_a[action.bus_id] = action.target_layer
            new_a[action.swap_bus_id] = assignment.get_layer(action.bus_id)

        return LayerAssignment(assignment=new_a)

    def _seed_via_estimate(self, assignment: LayerAssignment) -> int:
        """
        Estimate same-net seed vias introduced by pcb_exporter stub mode.

        Current SMD high-conflict and most KiCad exports use F.Cu pads for
        escape seeds. Assigning a bus to a non-top layer creates one via per
        endpoint per net. This term discourages small conflict improvements
        that require many additional seed vias.
        """
        seed_vias = 0
        for bus in self.buses:
            lid = assignment.get_layer(bus["id"])
            if lid is None or int(lid) == 0:
                continue
            net_count = len(bus.get("net_ids", []))
            if net_count == 0:
                continue
            start_count = len(bus.get("start_pad_ids", [])) or net_count
            end_count = len(bus.get("end_pad_ids", [])) or net_count
            seed_vias += min(start_count, net_count) + min(end_count, net_count)
        return seed_vias

    def _optimization_cost(self, assignment: LayerAssignment) -> float:
        result = self.evaluator.evaluate(assignment)
        return result.cost + self.seed_via_weight * self._seed_via_estimate(assignment)

    def solve(self) -> LayerAssignment:
        self.iteration_logs = []

        # 1. 初始分配
        current = self._initial_assignment()
        current_result = self.evaluator.evaluate(current)
        current_score = self._optimization_cost(current)
        best = current
        best_score = current_score

        self.iteration_logs.append(IterationLog(
            iteration=0, cost=current_result.cost,
            conflict_count=current_result.conflict_count,
            action_taken=None, failures_detected=0,
            candidates_evaluated=0, accepted=True
        ))

        rng = random.Random(self.seed)
        temp = self.initial_temp
        no_improve_count = 0

        for it in range(1, self.max_iterations + 1):
            # 2. 失败模式识别
            failures = self.detector.detect(current)
            conflict_pairs = self.detector.get_conflict_pairs(current)

            if not failures and not conflict_pairs:
                break

            # 3. 生成候选调整（已按预评分排序）
            scored = self.generator.generate(
                current, failures, conflict_pairs, self.max_candidates
            )

            # 4. 用完整 evaluator 评估 top 候选
            best_candidate = None
            best_candidate_score = current_score
            candidates_evaluated = 0

            # 只评估预评分最低（最可能改善）的几个
            eval_limit = min(len(scored), 5)
            for _, action in scored[:eval_limit]:
                test_assignment = self._apply_action(current, action)
                test_result = self.evaluator.evaluate(test_assignment)
                test_score = (
                    test_result.cost
                    + self.seed_via_weight * self._seed_via_estimate(test_assignment)
                )
                candidates_evaluated += 1

                structural_improves = (
                    test_result.unrouted_estimate < current_result.unrouted_estimate
                    or test_result.conflict_count < current_result.conflict_count
                )
                if structural_improves and test_score < best_candidate_score:
                    best_candidate_score = test_score
                    best_candidate = action

            # 5. 模拟退火：接受概率 = exp(-delta / temp)
            accepted = False
            if best_candidate is not None:
                delta = best_candidate_score - current_score

                if delta < 0:
                    # 严格改善，直接接受
                    accept = True
                elif temp > 0.01 and delta > 0:
                    # 非改善，以概率接受
                    accept_prob = math.exp(-delta / temp)
                    accept = rng.random() < accept_prob
                else:
                    accept = False

                if accept:
                    current = self._apply_action(current, best_candidate)
                    current_result = self.evaluator.evaluate(current)
                    current_score = self._optimization_cost(current)
                    accepted = True

                    if current_score < best_score:
                        best = current
                        best_score = current_score
                        no_improve_count = 0
                    else:
                        no_improve_count += 1
                else:
                    no_improve_count += 1
            else:
                no_improve_count += 1

            # 降温
            temp *= self.cooling_rate

            self.iteration_logs.append(IterationLog(
                iteration=it, cost=current_result.cost,
                conflict_count=current_result.conflict_count,
                action_taken=best_candidate,
                failures_detected=len(failures) + len(conflict_pairs),
                candidates_evaluated=candidates_evaluated,
                accepted=accepted
            ))

            if no_improve_count >= self.patience:
                break

        return best

    def get_log_summary(self) -> List[dict]:
        return [
            {
                "iteration": log.iteration,
                "cost": round(log.cost, 2),
                "conflicts": log.conflict_count,
                "failures": log.failures_detected,
                "candidates": log.candidates_evaluated,
                "accepted": log.accepted,
                "action": (f"{log.action_taken.action_type}(bus={log.action_taken.bus_id}, "
                           f"layer={log.action_taken.target_layer})"
                           if log.action_taken else "init")
            }
            for log in self.iteration_logs
        ]


def run_all_benchmarks():
    """在所有 benchmark 上跑对比"""
    import os
    bench_dir = "benchmark/synthetic"
    json_files = sorted([
        os.path.join(bench_dir, f) for f in os.listdir(bench_dir)
        if f.endswith('.json') and 'result' not in f and 'ablation' not in f
    ])

    from solvers import GreedySolver, GraphColoringSolver

    print(f"{'Benchmark':<35} {'Greedy':>10} {'GraphCol':>10} {'Feedback':>10} {'Improve':>8}")
    print("-" * 78)

    for bf in json_files:
        name = os.path.basename(bf).replace('.json', '')
        ev = Evaluator(bf)

        g_cost = ev.evaluate(GreedySolver(bf).solve()).cost
        gc_cost = ev.evaluate(GraphColoringSolver(bf).solve()).cost
        fb_la = FeedbackSolver(bf, max_iterations=50, patience=15,
                               initial_temp=5.0, cooling_rate=0.92).solve()
        fb_cost = ev.evaluate(fb_la).cost

        improve = (g_cost - fb_cost) / g_cost * 100 if g_cost > 0 else 0

        print(f"{name:<35} {g_cost:>10.1f} {gc_cost:>10.1f} {fb_cost:>10.1f} {improve:>+7.1f}%")


if __name__ == '__main__':
    run_all_benchmarks()
