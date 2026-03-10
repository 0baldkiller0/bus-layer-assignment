"""
PCB 总线层分配问题 - 可布通性评价器

输入 benchmark JSON + 层分配方案，输出各项评价指标。
"""

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class LayerAssignment:
    """层分配方案: bus_id -> layer_id"""
    assignment: Dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "LayerAssignment":
        return cls(assignment={int(k): int(v) for k, v in d.items()})

    def get_layer(self, bus_id: int) -> Optional[int]:
        return self.assignment.get(bus_id)

    def buses_on_layer(self, layer_id: int) -> List[int]:
        return [bid for bid, lid in self.assignment.items() if lid == layer_id]

    def layers_used(self) -> int:
        return len(set(self.assignment.values())) if self.assignment else 0


@dataclass
class EvalResult:
    """评价结果"""
    conflict_count: int = 0        # 同层冲突对数
    crossing_count: int = 0        # 总线交叉对数
    layer_usage: int = 0           # 使用层数
    congestion_max: float = 0.0    # 最大拥塞值
    congestion_avg: float = 0.0    # 平均拥塞值
    via_estimate: int = 0          # 估计过孔数
    wirelength_estimate: float = 0.0  # 估计总线长
    unrouted_estimate: int = 0     # 估计不可布通总线数
    cost: float = 0.0              # 综合代价

    def to_dict(self) -> dict:
        return {
            "conflict_count": self.conflict_count,
            "crossing_count": self.crossing_count,
            "layer_usage": self.layer_usage,
            "congestion_max": round(self.congestion_max, 4),
            "congestion_avg": round(self.congestion_avg, 4),
            "via_estimate": self.via_estimate,
            "wirelength_estimate": round(self.wirelength_estimate, 4),
            "unrouted_estimate": self.unrouted_estimate,
            "cost": round(self.cost, 4)
        }


class Evaluator:
    """
    可布通性评价器。

    用法:
        ev = Evaluator("benchmark/bench4.json")
        result = ev.evaluate(assignment)
    """

    def __init__(
        self,
        benchmark_path: str,
        grid_size: float = 2.0,
        conflict_model: str = "bbox",
    ):
        """
        Args:
            benchmark_path: benchmark JSON 文件路径
            grid_size: 拥塞估计的栅格大小（与 board 单位一致）
            conflict_model: "bbox" 使用起终点包围盒，"corridor" 使用候选曼哈顿走廊网格重叠
        """
        with open(benchmark_path, 'r') as f:
            self.data = json.load(f)

        self.board = self.data["board"]
        self.buses = self.data["buses"]
        self.components = self.data["components"]
        self.obstacles = self.data["obstacles"]
        self.bus_by_id = {bus["id"]: bus for bus in self.buses}

        self.num_layers = self.board["layers"]
        self.board_w = self.board["width"]
        self.board_h = self.board["height"]
        self.grid_size = grid_size
        if conflict_model not in {"bbox", "corridor"}:
            raise ValueError(f"Unsupported conflict_model: {conflict_model}")
        self.conflict_model = conflict_model
        boundary = self.board.get("boundary", {})
        self.board_origin = tuple(boundary.get("dia0", [0.0, 0.0])[:2])
        self._coords_are_absolute = self._detect_absolute_coordinates()

        # 预计算每个 bus 的几何信息
        self._bus_geoms = {}
        for bus in self.buses:
            self._bus_geoms[bus["id"]] = self._compute_bus_geom(bus)

        # 默认权重
        self.weights = {
            "conflict": 10.0,
            "crossing": 5.0,
            "layer": 2.0,
            "via": 3.0,
            "wirelength": 0.01,
            "unrouted": 50.0
        }

    def _compute_bus_geom(self, bus: dict) -> dict:
        """计算总线的几何特征"""
        sx, sy = self._to_local_point(bus["start_pos"])
        ex, ey = self._to_local_point(bus["end_pos"])
        w = bus.get("width", 0.5)

        # 主方向
        dx = ex - sx
        dy = ey - sy

        # 判断主方向: 0=水平, 1=垂直, 2=斜向
        if abs(dx) > abs(dy) * 2:
            primary_dir = "horizontal"
        elif abs(dy) > abs(dx) * 2:
            primary_dir = "vertical"
        else:
            primary_dir = "diagonal"

        # 包围盒
        half_w = w / 2 + 0.2  # 加上 clearance 余量
        bbox = (
            min(sx, ex) - half_w,
            min(sy, ey) - half_w,
            max(sx, ex) + half_w,
            max(sy, ey) + half_w
        )

        # 曼哈顿距离
        manhattan = abs(dx) + abs(dy)

        geom = {
            "start": (sx, sy),
            "end": (ex, ey),
            "width": w,
            "primary_dir": primary_dir,
            "bbox": bbox,  # (x1, y1, x2, y2)
            "manhattan": manhattan,
            "num_nets": len(bus.get("net_ids", []))
        }
        geom["corridors"] = self._compute_manhattan_corridors(geom)
        return geom

    def _rect_to_cells(self, x1: float, y1: float, x2: float, y2: float) -> set:
        gx1 = max(0, int(math.floor(min(x1, x2) / self.grid_size)))
        gy1 = max(0, int(math.floor(min(y1, y2) / self.grid_size)))
        gx2 = int(math.floor(max(x1, x2) / self.grid_size))
        gy2 = int(math.floor(max(y1, y2) / self.grid_size))
        return {
            (gx, gy)
            for gx in range(gx1, gx2 + 1)
            for gy in range(gy1, gy2 + 1)
        }

    def _segment_to_cells(self, p0: tuple, p1: tuple, half_width: float) -> set:
        x0, y0 = p0
        x1, y1 = p1
        return self._rect_to_cells(
            min(x0, x1) - half_width,
            min(y0, y1) - half_width,
            max(x0, x1) + half_width,
            max(y0, y1) + half_width,
        )

    def _path_to_cells(self, points: list, half_width: float) -> set:
        cells = set()
        for p0, p1 in zip(points, points[1:]):
            cells.update(self._segment_to_cells(p0, p1, half_width))
        return cells

    def _compute_manhattan_corridors(self, geom: dict) -> List[set]:
        sx, sy = geom["start"]
        ex, ey = geom["end"]
        half_width = geom["width"] / 2 + 0.2
        mid_x = (sx + ex) / 2
        mid_y = (sy + ey) / 2
        patterns = [
            [(sx, sy), (ex, sy), (ex, ey)],
            [(sx, sy), (sx, ey), (ex, ey)],
            [(sx, sy), (mid_x, sy), (mid_x, ey), (ex, ey)],
            [(sx, sy), (sx, mid_y), (ex, mid_y), (ex, ey)],
        ]
        corridors = []
        seen = set()
        for points in patterns:
            cells = frozenset(self._path_to_cells(points, half_width))
            if cells and cells not in seen:
                seen.add(cells)
                corridors.append(set(cells))
        return corridors

    def _detect_absolute_coordinates(self) -> bool:
        """兼容旧导出文件：若坐标看起来是 KiCad 绝对坐标，则评估时转为板内局部坐标。"""
        if self.data.get("meta", {}).get("coordinate_system") == "board_local":
            return False
        ox, oy = self.board_origin
        if abs(ox) < 1e-9 and abs(oy) < 1e-9:
            return False

        coords = []
        for bus in self.buses:
            coords.extend([bus.get("start_pos", [0, 0]), bus.get("end_pos", [0, 0])])
        for comp in self.components:
            coords.extend([comp.get("dia0", [0, 0]), comp.get("dia1", [0, 0])])
        for obs in self.obstacles:
            coords.extend([obs.get("dia0", [0, 0]), obs.get("dia1", [0, 0])])
        if not coords:
            return False

        max_x = max(p[0] for p in coords)
        max_y = max(p[1] for p in coords)
        min_x = min(p[0] for p in coords)
        min_y = min(p[1] for p in coords)
        inside_absolute_board = (
            min_x >= ox - 1e-6 and min_y >= oy - 1e-6 and
            max_x <= ox + self.board_w + 1e-6 and
            max_y <= oy + self.board_h + 1e-6
        )
        outside_local_board = max_x > self.board_w + 1e-6 or max_y > self.board_h + 1e-6
        return inside_absolute_board and outside_local_board

    def _to_local_point(self, point) -> Tuple[float, float]:
        x, y = point[:2]
        if self._coords_are_absolute:
            ox, oy = self.board_origin
            return x - ox, y - oy
        return x, y

    def _bbox_overlap(self, b1: tuple, b2: tuple) -> bool:
        """判断两个包围盒是否重叠"""
        return not (b1[2] < b2[0] or b2[2] < b1[0] or
                    b1[3] < b2[1] or b2[3] < b1[1])

    def _segments_cross(self, s1, e1, s2, e2) -> bool:
        """判断两条线段是否交叉（不含共端点）"""
        def orientation(p, q, r):
            val = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
            if abs(val) < 1e-9:
                return 0
            return 1 if val > 0 else 2

        def on_segment(p, q, r):
            return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and
                    min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))

        o1 = orientation(s1, e1, s2)
        o2 = orientation(s1, e1, e2)
        o3 = orientation(s2, e2, s1)
        o4 = orientation(s2, e2, e1)

        if o1 != o2 and o3 != o4:
            return True

        if o1 == 0 and on_segment(s1, s2, e1):
            return True
        if o2 == 0 and on_segment(s1, e2, e1):
            return True
        if o3 == 0 and on_segment(s2, s1, e2):
            return True
        if o4 == 0 and on_segment(s2, e1, e2):
            return True

        return False

    def _bus_crosses(self, bus_a: dict, bus_b: dict) -> bool:
        """判断两条总线是否交叉"""
        ga = self._bus_geoms[bus_a["id"]]
        gb = self._bus_geoms[bus_b["id"]]

        # 快速包围盒检测
        if not self._bbox_overlap(ga["bbox"], gb["bbox"]):
            return False

        return self._segments_cross(ga["start"], ga["end"], gb["start"], gb["end"])

    def _bus_conflicts(self, bus_a: dict, bus_b: dict) -> bool:
        """
        判断两条同层总线是否冲突。
        冲突定义：包围盒重叠 且 方向不平行（会导致布线资源竞争）。
        """
        ga = self._bus_geoms[bus_a["id"]]
        gb = self._bus_geoms[bus_b["id"]]

        if self.conflict_model == "corridor":
            return self._corridor_overlap_score(bus_a["id"], bus_b["id"]) > 0

        if not self._bbox_overlap(ga["bbox"], gb["bbox"]):
            return False

        # 同方向平行线重叠也算冲突（通道竞争）
        if ga["primary_dir"] == gb["primary_dir"]:
            # 平行且包围盒重叠 → 通道竞争
            return True

        # 不同方向且包围盒重叠 → 冲突
        return True

    def _corridor_overlap_score(self, bus_a_id: int, bus_b_id: int) -> int:
        corridors_a = self._bus_geoms[bus_a_id].get("corridors", [])
        corridors_b = self._bus_geoms[bus_b_id].get("corridors", [])
        if not corridors_a or not corridors_b:
            return 0

        best = None
        for ca in corridors_a:
            for cb in corridors_b:
                overlap = len(ca & cb)
                best = overlap if best is None else min(best, overlap)
        return int(best or 0)

    def evaluate(self, assignment: LayerAssignment,
                 cost_weights: Optional[dict] = None) -> EvalResult:
        """
        评价一个层分配方案。

        Args:
            assignment: 层分配方案
            cost_weights: 自定义权重，None 则使用默认权重

        Returns:
            EvalResult
        """
        weights = cost_weights or self.weights
        result = EvalResult()

        if not assignment.assignment:
            # 无分配 → 全部不可布通
            result.unrouted_estimate = len(self.buses)
            result.cost = weights["unrouted"] * result.unrouted_estimate
            return result

        # --- 1. 层数 ---
        result.layer_usage = assignment.layers_used()

        # --- 2. 同层冲突 ---
        conflict_pairs = set()
        for lid in range(self.num_layers):
            buses_on_lid = assignment.buses_on_layer(lid)
            for i in range(len(buses_on_lid)):
                for j in range(i + 1, len(buses_on_lid)):
                    ba = self.bus_by_id.get(buses_on_lid[i])
                    bb = self.bus_by_id.get(buses_on_lid[j])
                    if ba is None or bb is None:
                        continue
                    if self._bus_conflicts(ba, bb):
                        pair = (min(ba["id"], bb["id"]), max(ba["id"], bb["id"]))
                        conflict_pairs.add(pair)
        result.conflict_count = len(conflict_pairs)

        # --- 3. 交叉（所有 bus 对，不论层） ---
        crossing_pairs = set()
        for i in range(len(self.buses)):
            for j in range(i + 1, len(self.buses)):
                ba = self.buses[i]
                bb = self.buses[j]
                if self._bus_crosses(ba, bb):
                    la = assignment.get_layer(ba["id"])
                    lb = assignment.get_layer(bb["id"])
                    # 同层交叉最严重，不同层交叉可以通过过孔解决
                    if la == lb:
                        crossing_pairs.add((ba["id"], bb["id"]))
        result.crossing_count = len(crossing_pairs)

        # --- 4. 拥塞估计 ---
        congestion = self._estimate_congestion(assignment)
        result.congestion_max = max(congestion.values()) if congestion else 0
        result.congestion_avg = (sum(congestion.values()) / len(congestion)) if congestion else 0

        # --- 5. 过孔估计 ---
        result.via_estimate = self._estimate_vias(assignment)

        # --- 6. 线长估计 ---
        result.wirelength_estimate = self._estimate_wirelength(assignment)

        # --- 7. 不可布通估计 ---
        result.unrouted_estimate = self._estimate_unrouted(assignment, conflict_pairs)

        # --- 8. 综合代价 ---
        result.cost = (
            weights["conflict"] * result.conflict_count +
            weights["crossing"] * result.crossing_count +
            weights["layer"] * result.layer_usage +
            weights["via"] * result.via_estimate +
            weights["wirelength"] * result.wirelength_estimate +
            weights["unrouted"] * result.unrouted_estimate
        )

        return result

    def _estimate_congestion(self, assignment: LayerAssignment) -> Dict[Tuple[int, int, int], float]:
        """
        拥塞估计：统计每个栅格-层上的总线密度。

        Returns:
            {(grid_x, grid_y, layer): congestion_value}
        """
        grid_w = int(math.ceil(self.board_w / self.grid_size))
        grid_h = int(math.ceil(self.board_h / self.grid_size))

        congestion = {}

        for bus in self.buses:
            lid = assignment.get_layer(bus["id"])
            if lid is None:
                continue

            geom = self._bus_geoms[bus["id"]]
            x1, y1, x2, y2 = geom["bbox"]

            gx1 = max(0, int(x1 / self.grid_size))
            gy1 = max(0, int(y1 / self.grid_size))
            gx2 = min(grid_w - 1, int(x2 / self.grid_size))
            gy2 = min(grid_h - 1, int(y2 / self.grid_size))

            for gx in range(gx1, gx2 + 1):
                for gy in range(gy1, gy2 + 1):
                    key = (gx, gy, lid)
                    # 拥塞值 = 总线宽度 × net 数量
                    congestion[key] = congestion.get(key, 0) + geom["width"] * geom["num_nets"]

        return congestion

    def _estimate_vias(self, assignment: LayerAssignment) -> int:
        """
        过孔估计：基于总线起止方向和层分配估算过孔需求。

        同层总线不需要过孔；不同层间需要跨层过孔。
        另外，逃逸方向变化也可能需要过孔。
        """
        via_count = 0

        for bus in self.buses:
            lid = assignment.get_layer(bus["id"])
            if lid is None:
                continue

            # 起止方向不同 → 可能需要方向转换过孔
            start_dirs = set(bus.get("start_directions", []))
            end_dirs = set(bus.get("end_directions", []))
            if start_dirs and end_dirs:
                # 方向不一致 → 至少1个方向转换过孔
                if not start_dirs.intersection(end_dirs):
                    via_count += len(bus.get("net_ids", []))

        # 层间过孔：需要根据实际层分配计算
        # 同一组 net 的不同 bus 如果在不同层，需要过孔连接
        net_to_layers = {}
        for bus in self.buses:
            lid = assignment.get_layer(bus["id"])
            if lid is None:
                continue
            for nid in bus.get("net_ids", []):
                if nid not in net_to_layers:
                    net_to_layers[nid] = set()
                net_to_layers[nid].add(lid)

        for nid, layers in net_to_layers.items():
            if len(layers) > 1:
                via_count += len(layers) - 1

        return via_count

    def _estimate_wirelength(self, assignment: LayerAssignment) -> float:
        """线长估计：所有已分配总线的曼哈顿距离之和"""
        total = 0.0
        for bus in self.buses:
            if assignment.get_layer(bus["id"]) is not None:
                total += self._bus_geoms[bus["id"]]["manhattan"]
        return total

    def _estimate_unrouted(self, assignment: LayerAssignment,
                           conflict_pairs: set) -> int:
        """
        不可布通估计：高冲突的 bus 更可能无法布通。

        如果一条 bus 与同层 3 条以上 bus 冲突，标记为高风险。
        """
        conflict_count_per_bus = {}
        for a, b in conflict_pairs:
            conflict_count_per_bus[a] = conflict_count_per_bus.get(a, 0) + 1
            conflict_count_per_bus[b] = conflict_count_per_bus.get(b, 0) + 1

        unrouted = 0
        for bid, count in conflict_count_per_bus.items():
            if count >= 3:
                unrouted += 1
        return unrouted

    def per_bus_metrics(self, assignment: LayerAssignment) -> List[dict]:
        """
        输出每条 bus 的详细指标，用于分析。

        Returns:
            [{"bus_id": ..., "layer": ..., "conflicts": ..., "crossings": ..., ...}, ...]
        """
        results = []

        for bus in self.buses:
            bid = bus["id"]
            lid = assignment.get_layer(bid)
            geom = self._bus_geoms[bid]

            conflicts = 0
            crossings = 0

            if lid is not None:
                # 同层冲突
                for other in self.buses:
                    if other["id"] == bid:
                        continue
                    if assignment.get_layer(other["id"]) == lid:
                        if self._bus_conflicts(bus, other):
                            conflicts += 1

                # 交叉
                for other in self.buses:
                    if other["id"] <= bid:
                        continue
                    if self._bus_crosses(bus, other):
                        crossings += 1

            results.append({
                "bus_id": bid,
                "layer": lid,
                "netclass": bus.get("netclass"),
                "num_nets": len(bus.get("net_ids", [])),
                "width": bus.get("width", 0),
                "manhattan": round(geom["manhattan"], 4),
                "conflicts": conflicts,
                "crossings": crossings,
                "start_directions": bus.get("start_directions", []),
                "end_directions": bus.get("end_directions", [])
            })

        return results


def load_assignment_from_json(path: str) -> LayerAssignment:
    """从 JSON 文件加载层分配方案"""
    with open(path, 'r') as f:
        data = json.load(f)
    return LayerAssignment.from_dict(data)


def quick_evaluate(benchmark_path: str,
                   assignment_dict: Optional[Dict[int, int]] = None,
                   cost_weights: Optional[dict] = None) -> dict:
    """
    快速评价接口。

    Args:
        benchmark_path: benchmark JSON 路径
        assignment_dict: {bus_id: layer_id}，None 则全部未分配
        cost_weights: 自定义权重

    Returns:
        dict: 评价结果
    """
    ev = Evaluator(benchmark_path)
    if assignment_dict is not None:
        la = LayerAssignment.from_dict({str(k): v for k, v in assignment_dict.items()})
    else:
        la = LayerAssignment()
    result = ev.evaluate(la, cost_weights)
    return result.to_dict()


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python evaluator.py <benchmark.json> [assignment.json]")
        sys.exit(1)

    bench_path = sys.argv[1]

    if len(sys.argv) >= 3:
        la = load_assignment_from_json(sys.argv[2])
    else:
        # 演示：随机分配
        import random
        with open(bench_path) as f:
            data = json.load(f)
        n_layers = data["board"]["layers"]
        n_buses = len(data["buses"])
        la = LayerAssignment(
            assignment={b["id"]: random.randint(0, n_layers - 1) for b in data["buses"]}
        )
        print(f"Random assignment: {n_buses} buses -> {n_layers} layers")

    ev = Evaluator(bench_path)
    result = ev.evaluate(la)

    print("=== Evaluation Result ===")
    for k, v in result.to_dict().items():
        print(f"  {k}: {v}")

    print()
    print("=== Per-Bus Detail ===")
    for m in ev.per_bus_metrics(la):
        print(f"  Bus {m['bus_id']}: layer={m['layer']}, "
              f"nets={m['num_nets']}, conflicts={m['conflicts']}, "
              f"crossings={m['crossings']}")
