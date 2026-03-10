"""
PCB 总线层分配问题 - Baseline 求解器

包含:
  - RandomSolver: 随机层分配
  - GreedySolver: 贪心层分配
  - GraphColoringSolver: 图着色层分配
"""

import json
import random
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from evaluator import Evaluator, LayerAssignment


class BaseSolver(ABC):
    """求解器基类"""

    def __init__(self, benchmark_path: str):
        with open(benchmark_path, 'r') as f:
            self.data = json.load(f)
        self.buses = self.data["buses"]
        self.num_layers = self.data["board"]["layers"]
        self.evaluator = Evaluator(benchmark_path)

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def solve(self) -> LayerAssignment:
        pass

    def solve_and_evaluate(self) -> Tuple[LayerAssignment, dict, float]:
        """求解并评价，返回 (分配方案, 评价结果, 耗时秒数)"""
        t0 = time.time()
        assignment = self.solve()
        elapsed = time.time() - t0
        result = self.evaluator.evaluate(assignment)
        return assignment, result.to_dict(), elapsed


class RandomSolver(BaseSolver):
    """随机层分配 baseline"""

    def __init__(self, benchmark_path: str, seed: Optional[int] = None):
        super().__init__(benchmark_path)
        self.seed = seed

    @property
    def name(self) -> str:
        return "Random"

    def solve(self) -> LayerAssignment:
        rng = random.Random(self.seed)
        assignment = {}
        for bus in self.buses:
            assignment[bus["id"]] = rng.randint(0, self.num_layers - 1)
        return LayerAssignment(assignment=assignment)


class GreedySolver(BaseSolver):
    """
    贪心层分配 baseline。

    按总线宽度降序排列，逐条分配到冲突最小的层。
    """

    def __init__(
        self,
        benchmark_path: str,
        order: str = "width_desc",
        conflict_model: str = "bbox",
    ):
        """
        Args:
            order: 排序方式
                - "width_desc": 按总线宽度降序（宽的先分）
                - "nets_desc": 按 net 数量降序
                - "random": 随机顺序
        """
        super().__init__(benchmark_path)
        self.order = order
        self.conflict_model = conflict_model
        if conflict_model != "bbox":
            self.evaluator = Evaluator(benchmark_path, conflict_model=conflict_model)

    @property
    def name(self) -> str:
        if self.conflict_model == "bbox":
            return f"Greedy({self.order})"
        return f"Greedy({self.order},{self.conflict_model})"

    def solve(self) -> LayerAssignment:
        # 排序
        buses = list(self.buses)
        if self.order == "width_desc":
            buses.sort(key=lambda b: b.get("width", 0), reverse=True)
        elif self.order == "nets_desc":
            buses.sort(key=lambda b: len(b.get("net_ids", [])), reverse=True)
        elif self.order == "random":
            random.shuffle(buses)

        # 预计算包围盒
        bboxes = {}
        for bus in self.buses:
            sx, sy = bus["start_pos"]
            ex, ey = bus["end_pos"]
            w = bus.get("width", 0.5) / 2 + 0.2
            bboxes[bus["id"]] = (
                min(sx, ex) - w, min(sy, ey) - w,
                max(sx, ex) + w, max(sy, ey) + w
            )

        # 每层已分配 bus 的包围盒列表
        layer_bboxes: Dict[int, List[Tuple[int, tuple]]] = {
            lid: [] for lid in range(self.num_layers)
        }

        assignment = {}

        for bus in buses:
            bid = bus["id"]
            bb = bboxes[bid]
            best_layer = 0
            best_conflicts = float('inf')

            for lid in range(self.num_layers):
                # 数当前层的冲突数
                conflicts = 0
                for obid, obb in layer_bboxes[lid]:
                    if self.conflict_model == "corridor":
                        conflicts += self.evaluator._corridor_overlap_score(bid, obid)
                    elif not (bb[2] < obb[0] or obb[2] < bb[0] or
                              bb[3] < obb[1] or obb[3] < bb[1]):
                        conflicts += 1

                if conflicts < best_conflicts:
                    best_conflicts = conflicts
                    best_layer = lid

            assignment[bid] = best_layer
            layer_bboxes[best_layer].append((bid, bb))

        return LayerAssignment(assignment=assignment)


class GraphColoringSolver(BaseSolver):
    """
    图着色层分配 baseline。

    构建总线冲突图，用图着色求最小层数分配。
    先尝试用最少颜色，逐步增加直到着色成功。
    """

    @property
    def name(self) -> str:
        return "GraphColoring"

    def _build_conflict_graph(self) -> List[List[int]]:
        """
        构建冲突图邻接矩阵。

        两条总线冲突的条件：包围盒重叠 且（方向不同 或 平行紧邻）。
        """
        n = len(self.buses)
        graph = [[0] * n for _ in range(n)]

        # 预计算包围盒
        bboxes = []
        for bus in self.buses:
            sx, sy = bus["start_pos"]
            ex, ey = bus["end_pos"]
            w = bus.get("width", 0.5) / 2 + 0.2
            bboxes.append((
                min(sx, ex) - w, min(sy, ey) - w,
                max(sx, ex) + w, max(sy, ey) + w
            ))

        for i in range(n):
            for j in range(i + 1, n):
                # 包围盒重叠检测
                bi, bj = bboxes[i], bboxes[j]
                if not (bi[2] < bj[0] or bj[2] < bi[0] or
                        bi[3] < bj[1] or bj[3] < bi[1]):
                    graph[i][j] = 1
                    graph[j][i] = 1

        return graph

    def _is_safe(self, graph: List[List[int]], colors: List[int], c: int, v: int) -> bool:
        """检查给定点 v 染色 c 是否安全"""
        for i in range(len(graph)):
            if graph[v][i] == 1 and colors[i] == c:
                return False
        return True

    def _color_util(self, graph: List[List[int]], m: int,
                    colors: List[int], v: int) -> bool:
        """图着色回溯算法"""
        if v == len(graph):
            return True
        for c in range(m):
            if self._is_safe(graph, colors, c, v):
                colors[v] = c
                if self._color_util(graph, m, colors, v + 1):
                    return True
                colors[v] = -1
        return False

    def _graph_coloring(self, graph: List[List[int]], m: int) -> Optional[List[int]]:
        """尝试用 m 种颜色着色"""
        n = len(graph)
        colors = [-1] * n
        if self._color_util(graph, m, colors, 0):
            return colors
        return None

    def solve(self) -> LayerAssignment:
        n = len(self.buses)
        if n == 0:
            return LayerAssignment()

        graph = self._build_conflict_graph()

        # 计算每个节点的度数，确定下界
        max_degree = max(sum(row) for row in graph) if n > 0 else 0
        min_colors = max(1, max_degree)  # 至少需要 degree+1 种颜色

        # 从下界开始尝试，逐步增加
        for m in range(min_colors, n + 1):
            colors = self._graph_coloring(graph, m)
            if colors is not None:
                # 着色成功，但层数可能超过可用层数
                # 如果超过，取模映射到可用层
                assignment = {}
                for i, bus in enumerate(self.buses):
                    assignment[bus["id"]] = colors[i] % self.num_layers
                return LayerAssignment(assignment=assignment)

        # 极端情况：每条 bus 一层
        return LayerAssignment(
            assignment={b["id"]: i % self.num_layers
                        for i, b in enumerate(self.buses)}
        )


class OptimalSearchSolver(BaseSolver):
    """
    暴力搜索 baseline（小规模适用）。

    对 bus 数量 <= 12 的情况，穷举所有层分配方案取最优。
    """

    def __init__(self, benchmark_path: str, max_buses: int = 12):
        super().__init__(benchmark_path)
        self.max_buses = max_buses

    @property
    def name(self) -> str:
        return "OptimalSearch"

    def solve(self) -> LayerAssignment:
        n = len(self.buses)
        if n == 0:
            return LayerAssignment()
        if n > self.max_buses:
            # 回退到贪心
            return GreedySolver._solve_fallback(self)

        best_assignment = None
        best_cost = float('inf')

        # 递归穷举
        def search(idx: int, current: dict):
            nonlocal best_assignment, best_cost
            if idx == n:
                la = LayerAssignment(assignment=dict(current))
                result = self.evaluator.evaluate(la)
                if result.cost < best_cost:
                    best_cost = result.cost
                    best_assignment = dict(current)
                return

            bus_id = self.buses[idx]["id"]
            for lid in range(self.num_layers):
                current[bus_id] = lid
                search(idx + 1, current)
            del current[bus_id]

        search(0, {})
        return LayerAssignment(assignment=best_assignment or {})


def run_all_baselines(benchmark_path: str, seeds: int = 5) -> dict:
    """
    运行所有 baseline 并返回对比结果。

    Args:
        benchmark_path: benchmark JSON 路径
        seeds: Random solver 的随机种子数量（取最优结果）

    Returns:
        dict: {solver_name: {"assignment": ..., "metrics": ..., "time": ...}}
    """
    results = {}

    # Random（多次取最优）
    best_random = None
    best_random_cost = float('inf')
    for s in range(seeds):
        solver = RandomSolver(benchmark_path, seed=s)
        la, metrics, elapsed = solver.solve_and_evaluate()
        if metrics["cost"] < best_random_cost:
            best_random_cost = metrics["cost"]
            best_random = {
                "assignment": la.assignment,
                "metrics": metrics,
                "time": round(elapsed, 4)
            }
    results["Random"] = best_random

    # Greedy（width_desc）
    solver = GreedySolver(benchmark_path, order="width_desc")
    la, metrics, elapsed = solver.solve_and_evaluate()
    results["Greedy"] = {
        "assignment": la.assignment,
        "metrics": metrics,
        "time": round(elapsed, 4)
    }

    # Graph Coloring
    solver = GraphColoringSolver(benchmark_path)
    la, metrics, elapsed = solver.solve_and_evaluate()
    results["GraphColoring"] = {
        "assignment": la.assignment,
        "metrics": metrics,
        "time": round(elapsed, 4)
    }

    # Optimal Search（仅 bus <= 8 时，组合爆炸）
    with open(benchmark_path) as f:
        data = json.load(f)
    if len(data["buses"]) <= 8:
        solver = OptimalSearchSolver(benchmark_path)
        la, metrics, elapsed = solver.solve_and_evaluate()
        results["OptimalSearch"] = {
            "assignment": la.assignment,
            "metrics": metrics,
            "time": round(elapsed, 4)
        }

    return results


if __name__ == '__main__':
    import sys

    bench_path = sys.argv[1] if len(sys.argv) > 1 else "benchmark/bench4.json"

    print(f"Running baselines on {bench_path}")
    print("=" * 60)

    results = run_all_baselines(bench_path)

    # 打印对比表
    print(f"\n{'Method':<16} {'Cost':>8} {'Conflict':>9} {'Cross':>6} "
          f"{'Layers':>7} {'Via':>5} {'Wire':>8} {'Time':>8}")
    print("-" * 65)

    for name, r in sorted(results.items(), key=lambda x: x[1]["metrics"]["cost"]):
        m = r["metrics"]
        print(f"{name:<16} {m['cost']:>8.2f} {m['conflict_count']:>9} "
              f"{m['crossing_count']:>6} {m['layer_usage']:>7} "
              f"{m['via_estimate']:>5} {m['wirelength_estimate']:>8.1f} "
              f"{r['time']:>7.4f}s")

    # 保存结果
    output_path = bench_path.replace(".json", "_results.json")
    # 把 assignment 的 int key 转为 str（JSON 兼容）
    for name in results:
        results[name]["assignment"] = {
            str(k): v for k, v in results[name]["assignment"].items()
        }
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")
