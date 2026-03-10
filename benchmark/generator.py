"""
PCB 总线层分配问题 - 合成 Benchmark 生成器

生成不同规模、不同难度的合成 PCB 总线场景。
"""

import json
import math
import os
import random
from typing import List, Optional


def generate_benchmark(
    num_buses: int,
    num_layers: int,
    board_width: float = 100.0,
    board_height: float = 100.0,
    num_components: Optional[int] = None,
    obstacle_density: float = 0.0,
    seed: Optional[int] = None,
    scenario: str = "random"
) -> dict:
    """
    生成合成 benchmark。

    Args:
        num_buses: 总线数量
        num_layers: 板层数量
        board_width: 板宽度
        board_height: 板高度
        num_components: 组件数量（None 则自动计算）
        obstacle_density: 障碍物密度 0-1
        seed: 随机种子
        scenario: 场景类型
            - "random": 随机
            - "parallel": 平行总线
            - "crossing": 交叉总线
            - "dense": 高密度

    Returns:
        dict: benchmark JSON 格式数据
    """
    rng = random.Random(seed)

    if num_components is None:
        num_components = max(4, num_buses // 3)

    # 生成组件（作为总线的起止点）
    components = _generate_components(
        num_components, board_width, board_height, rng
    )

    # 生成总线
    buses = _generate_buses(
        num_buses, components, board_width, board_height,
        rng, scenario
    )

    # 生成障碍物
    obstacles = _generate_obstacles(
        obstacle_density, board_width, board_height,
        components, rng
    )

    # 生成 netclass
    netclasses = {
        "Default": {
            "track_width": 0.25,
            "clearance_with_track": 0.25,
            "clearance_with_microvia": 0.3,
            "microvia_diameter": 0.3,
            "microvia_drill": 0.15
        }
    }

    # 构建 benchmark
    bench = {
        "meta": {
            "source": "synthetic",
            "scenario": scenario,
            "seed": seed,
            "num_buses_param": num_buses,
            "num_layers_param": num_layers
        },
        "board": {
            "width": board_width,
            "height": board_height,
            "layers": num_layers,
            "layer_names": {str(i): f"L{i+1}" for i in range(num_layers)},
            "boundary": {
                "dia0": [0, 0],
                "dia1": [board_width, board_height]
            }
        },
        "netclasses": netclasses,
        "components": components,
        "pads": [],
        "nets": [],
        "buses": buses,
        "obstacles": obstacles,
        "stats": {
            "num_components": len(components),
            "num_nets": sum(len(b.get("net_ids", [])) for b in buses),
            "num_buses": len(buses),
            "num_pads": 0,
            "num_obstacles": len(obstacles),
            "num_netclasses": len(netclasses),
            "total_bus_width": round(sum(b["width"] for b in buses), 4)
        }
    }

    return bench


def _generate_components(
    n: int, w: float, h: float, rng: random.Random
) -> List[dict]:
    """生成不重叠的组件"""
    components = []
    margin = 5.0
    attempts = 0

    while len(components) < n and attempts < n * 100:
        attempts += 1
        comp_w = rng.uniform(3, 8)
        comp_h = rng.uniform(3, 8)
        x = rng.uniform(margin, w - margin - comp_w)
        y = rng.uniform(margin, h - margin - comp_h)

        # 检查不与已有组件重叠
        dia0 = [x, y]
        dia1 = [x + comp_w, y + comp_h]
        overlap = False
        for c in components:
            if not (dia1[0] < c["dia0"][0] or c["dia1"][0] < dia0[0] or
                    dia1[1] < c["dia0"][1] or c["dia1"][1] < dia0[1]):
                overlap = True
                break

        if not overlap:
            components.append({
                "id": len(components),
                "name": f"COMP_{len(components)}",
                "dia0": [round(dia0[0], 4), round(dia0[1], 4)],
                "dia1": [round(dia1[0], 4), round(dia1[1], 4)],
                "pad_count": 0,
                "bus_ids": []
            })

    return components


def _generate_buses(
    n: int, components: List[dict], w: float, h: float,
    rng: random.Random, scenario: str
) -> List[dict]:
    """生成总线"""
    buses = []
    net_id = 0

    if len(components) < 2:
        return buses

    for i in range(n):
        # 随机选择起止组件
        ci = rng.randint(0, len(components) - 1)
        cj = rng.randint(0, len(components) - 1)
        while cj == ci:
            cj = rng.randint(0, len(components) - 1)

        comp_s = components[ci]
        comp_e = components[cj]

        # 起止位置：在组件边界上
        start_pos = _point_on_boundary(comp_s, comp_e, rng)
        end_pos = _point_on_boundary(comp_e, comp_s, rng)

        # 逃逸方向
        start_dirs = _escape_directions(comp_s, start_pos)
        end_dirs = _escape_directions(comp_e, end_pos)

        # 根据场景调整
        if scenario == "parallel":
            # 平行总线：起止 y 坐标接近
            mid_y = h / 2
            spread = h * 0.3
            offset = (i - n / 2) * (spread / max(1, n - 1))
            start_pos[1] = mid_y + offset
            end_pos[1] = mid_y + offset

        elif scenario == "crossing":
            # 交叉总线：交替从上到下和从下到上
            if i % 2 == 0:
                start_pos[1] = rng.uniform(h * 0.7, h * 0.9)
                end_pos[1] = rng.uniform(h * 0.1, h * 0.3)
            else:
                start_pos[1] = rng.uniform(h * 0.1, h * 0.3)
                end_pos[1] = rng.uniform(h * 0.7, h * 0.9)

        elif scenario == "dense":
            # 高密度：集中在中心区域
            cx, cy = w / 2, h / 2
            spread = min(w, h) * 0.2
            start_pos[0] = cx + rng.uniform(-spread, spread)
            start_pos[1] = cy + rng.uniform(-spread, spread)
            end_pos[0] = cx + rng.uniform(-spread, spread)
            end_pos[1] = cy + rng.uniform(-spread, spread)

        # 总线宽度和 net 数量
        num_nets = rng.randint(2, 8)
        bus_width = num_nets * 0.3

        net_ids = list(range(net_id, net_id + num_nets))
        net_id += num_nets

        buses.append({
            "id": i,
            "netclass": "Default",
            "net_ids": net_ids,
            "start_comp": ci,
            "end_comp": cj,
            "start_pos": [round(start_pos[0], 4), round(start_pos[1], 4)],
            "end_pos": [round(end_pos[0], 4), round(end_pos[1], 4)],
            "width": round(bus_width, 4),
            "start_directions": start_dirs,
            "end_directions": end_dirs
        })

    return buses


def _point_on_boundary(comp: dict, target_comp: dict,
                       rng: random.Random) -> List[float]:
    """在组件朝向目标组件的边界上取一个点"""
    cx = (comp["dia0"][0] + comp["dia1"][0]) / 2
    cy = (comp["dia0"][1] + comp["dia1"][1]) / 2
    tx = (target_comp["dia0"][0] + target_comp["dia1"][0]) / 2
    ty = (target_comp["dia0"][1] + target_comp["dia1"][1]) / 2

    dx = tx - cx
    dy = ty - cy

    if abs(dx) > abs(dy):
        # 目标在水平方向
        if dx > 0:
            # 右边界
            return [comp["dia1"][0], rng.uniform(comp["dia0"][1], comp["dia1"][1])]
        else:
            # 左边界
            return [comp["dia0"][0], rng.uniform(comp["dia0"][1], comp["dia1"][1])]
    else:
        # 目标在垂直方向
        if dy > 0:
            # 上边界
            return [rng.uniform(comp["dia0"][0], comp["dia1"][0]), comp["dia1"][1]]
        else:
            # 下边界
            return [rng.uniform(comp["dia0"][0], comp["dia1"][0]), comp["dia0"][1]]


def _escape_directions(comp: dict, point: list) -> List[int]:
    """判断点在组件的哪个边界，返回逃逸方向"""
    dirs = []
    tol = 0.5
    if abs(point[1] - comp["dia1"][1]) < tol:
        dirs.append(0)  # N
    if abs(point[0] - comp["dia1"][0]) < tol:
        dirs.append(1)  # E
    if abs(point[1] - comp["dia0"][1]) < tol:
        dirs.append(2)  # S
    if abs(point[0] - comp["dia0"][0]) < tol:
        dirs.append(3)  # W
    return dirs if dirs else [0]


def _generate_obstacles(
    density: float, w: float, h: float,
    components: List[dict], rng: random.Random
) -> List[dict]:
    """生成障碍物"""
    if density <= 0:
        return []

    n_obs = int(density * 20)
    obstacles = []

    for i in range(n_obs):
        ox = rng.uniform(0, w - 3)
        oy = rng.uniform(0, h - 3)
        ow = rng.uniform(1, 4)
        oh = rng.uniform(1, 4)
        obstacles.append({
            "id": i,
            "dia0": [round(ox, 4), round(oy, 4)],
            "dia1": [round(ox + ow, 4), round(oy + oh, 4)],
            "type": "obstacle"
        })

    return obstacles


def save_benchmark(bench: dict, path: str):
    """保存 benchmark 到 JSON 文件"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        json.dump(bench, f, indent=2)
    print(f"Saved: {path} (buses={bench['stats']['num_buses']}, "
          f"layers={bench['board']['layers']})")


def generate_suite(output_dir: str = "benchmark/synthetic"):
    """
    生成标准测试集。

    覆盖不同规模 × 不同场景 × 不同层数的组合。
    """
    configs = []

    # 规模梯度
    for n_buses in [5, 10, 20, 50]:
        for n_layers in [2, 4]:
            for scenario in ["random", "crossing", "dense"]:
                configs.append({
                    "num_buses": n_buses,
                    "num_layers": n_layers,
                    "scenario": scenario,
                    "seed": 42
                })

    for i, cfg in enumerate(configs):
        bench = generate_benchmark(**cfg)
        fname = (f"buses{cfg['num_buses']}_layers{cfg['num_layers']}_"
                 f"{cfg['scenario']}.json")
        save_benchmark(bench, os.path.join(output_dir, fname))

    print(f"\nGenerated {len(configs)} benchmarks in {output_dir}/")


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "suite":
        out = sys.argv[2] if len(sys.argv) > 2 else "benchmark/synthetic"
        generate_suite(out)
    else:
        # 生成单个示例
        bench = generate_benchmark(
            num_buses=10, num_layers=4,
            scenario="crossing", seed=42
        )
        save_benchmark(bench, "benchmark/synthetic/example.json")
