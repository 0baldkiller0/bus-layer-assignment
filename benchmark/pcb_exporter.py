"""
PCB 总线层分配 - PCB 导出器

将层分配方案写入 KiCad PCB 文件。默认只添加短逃逸 stub，避免把
规划结果伪装成完整布线结果。
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Position


DIR_VECTORS = {
    0: (0.0, 1.0),   # N
    1: (1.0, 0.0),   # E
    2: (0.0, -1.0),  # S
    3: (-1.0, 0.0),  # W
}


def _load_bench(bench_path: str) -> dict:
    with open(bench_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _signal_layer_map(board: Board, bench: dict) -> dict:
    """返回 local layer id -> KiCad layer name 的映射。"""
    board_signal_layers = []
    for layer in board.layers:
        if getattr(layer, "type", None) == "signal":
            name = getattr(layer, "name", None) or getattr(layer, "canonicalName", None)
            if name is not None:
                board_signal_layers.append(name)

    layer_names = bench["board"].get("layer_names", {})
    layer_map = {}
    if isinstance(layer_names, list):
        layer_map = {str(i): name for i, name in enumerate(layer_names)}
    elif isinstance(layer_names, dict):
        for k, v in layer_names.items():
            if isinstance(v, str):
                layer_map[str(k)] = v

    if not layer_map:
        layer_map = {str(i): name for i, name in enumerate(board_signal_layers)}
    elif board_signal_layers:
        # Some generated benchmarks use abstract names such as L1/L2/L3/L4,
        # while the source KiCad PCB uses F.Cu/In*.Cu/B.Cu. DSN paths must use
        # actual KiCad layer names; otherwise Freerouting receives wires on
        # layers that are not declared in the board structure.
        board_layer_set = set(board_signal_layers)
        layer_map = {
            str(k): (v if v in board_layer_set else board_signal_layers[int(k)])
            for k, v in layer_map.items()
            if str(k).isdigit() and int(k) < len(board_signal_layers)
        }
    return layer_map


def _point_to_pcb(bench: dict, point) -> tuple:
    """
    benchmark 统一使用板内局部坐标；旧 benchmark 可能已经是 KiCad 绝对坐标。
    写回 PCB 时需要转为 KiCad 绝对坐标。
    """
    x, y = point[:2]
    board = bench.get("board", {})
    boundary = board.get("boundary", {})
    ox, oy = boundary.get("dia0", [0.0, 0.0])[:2]
    width = board.get("width", 0.0)
    height = board.get("height", 0.0)

    coord_system = bench.get("meta", {}).get("coordinate_system")
    if coord_system == "board_local":
        return x + ox, y + oy

    # Legacy compatibility: if the point fits local board bounds, treat it as local.
    if -1e-6 <= x <= width + 1e-6 and -1e-6 <= y <= height + 1e-6:
        return x + ox, y + oy
    return x, y


def _metric_value(value, default):
    """兼容旧 benchmark 中由 grid units 写出的 netclass 参数。"""
    if value is None:
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value > 2.0:
        return value / 12.0
    return value


def _netclass_for_bus(bench: dict, bus: dict) -> dict:
    netclasses = bench.get("netclasses", {"Default": {"track_width": 0.25}})
    default_nc = netclasses.get("Default") or next(iter(netclasses.values()), {"track_width": 0.25})
    nc_name = bus.get("netclass") or "Default"
    return netclasses.get(nc_name, default_nc)


def _build_net_to_pads(bench: dict) -> dict:
    net_to_pads = {}
    for net in bench.get("nets", []):
        net_to_pads[int(net["id"])] = list(net.get("pad_ids", []))
    if net_to_pads:
        return net_to_pads
    for pad in bench.get("pads", []):
        nid = pad.get("net_id")
        if nid is not None:
            net_to_pads.setdefault(int(nid), []).append(pad["id"])
    return net_to_pads


def _bus_endpoint_pads(bus: dict, net_id: int, net_to_pads: dict) -> list:
    candidates = set(net_to_pads.get(int(net_id), []))
    endpoints = []
    for key in ("start_pad_ids", "end_pad_ids"):
        matched = [pid for pid in bus.get(key, []) if pid in candidates]
        endpoints.extend((key, pid) for pid in matched)
    return endpoints


def _choose_direction(bus: dict, endpoint_key: str, pad: dict, opposite_pos: list) -> int:
    dirs = bus.get("start_directions" if endpoint_key == "start_pad_ids" else "end_directions", [])
    if dirs:
        return int(dirs[0])

    px, py = pad["position"][:2]
    ox, oy = opposite_pos[:2]
    dx, dy = px - ox, py - oy
    if abs(dx) >= abs(dy):
        return 1 if dx >= 0 else 3
    return 0 if dy >= 0 else 2


def _add_stub_segment(
    board: Board,
    bench: dict,
    pad: dict,
    net_id: int,
    layer_name: str,
    direction: int,
    track_width: float,
    stub_length: float,
    locked: bool,
    tstamp: str
):
    vx, vy = DIR_VECTORS.get(direction, DIR_VECTORS[0])
    sx, sy = pad["position"][:2]
    ex = sx + vx * stub_length
    ey = sy + vy * stub_length
    start = Position(*_point_to_pcb(bench, (sx, sy)))
    end = Position(*_point_to_pcb(bench, (ex, ey)))
    board.traceItems.append(Segment(
        start=start,
        end=end,
        width=track_width,
        layer=layer_name,
        locked=locked,
        net=int(net_id),
        tstamp=tstamp
    ))
    return ex, ey


def _add_guide_via(
    board: Board,
    bench: dict,
    pos,
    net_id: int,
    layers: list,
    size: float,
    drill: float,
    locked: bool,
    tstamp: str
):
    board.traceItems.append(Via(
        type=None,
        locked=locked,
        position=Position(*_point_to_pcb(bench, pos)),
        size=size,
        drill=drill,
        layers=layers,
        net=int(net_id),
        tstamp=tstamp
    ))


def export_pcb_with_assignment(
    bench_path: str,
    assignment: dict,
    pcb_path: str,
    output_path: str = None,
    guide_mode: str = "stub",
    stub_length: float = 2.0,
    locked: bool = True
):
    """
    将层分配方案写入 KiCad PCB 文件。

    guide_mode:
      - "stub": 只为每个 bus net 在两端添加短逃逸 stub（默认）
      - "full": 兼容旧行为，添加完整起终点 guide trace，不建议作为验证证据

    Args:
        bench_path: benchmark JSON 路径
        assignment: {bus_id: layer_id} 分配方案
        pcb_path: 原始 KiCad PCB 文件路径
        output_path: 输出 PCB 路径，None 则自动生成
    """
    bench = _load_bench(bench_path)
    assignment = {int(k): int(v) for k, v in assignment.items()}

    if output_path is None:
        base = os.path.splitext(pcb_path)[0]
        output_path = f"{base}_{guide_mode}_guided.kicad_pcb"

    board = Board().from_file(pcb_path)

    layer_map = _signal_layer_map(board, bench)
    pads = {int(p["id"]): p for p in bench.get("pads", [])}
    net_to_pads = _build_net_to_pads(bench)

    buses = bench["buses"]
    item_id = 0

    for bus in buses:
        bid = bus["id"]
        layer_id = assignment.get(bid)
        if layer_id is None:
            continue

        layer_name = layer_map.get(str(layer_id), "F.Cu" if layer_id == 0 else "B.Cu")

        nc = _netclass_for_bus(bench, bus)
        track_width = _metric_value(nc.get("track_width"), 0.25)
        via_size = _metric_value(nc.get("microvia_diameter"), 0.45)
        via_drill = _metric_value(nc.get("microvia_drill"), 0.2)

        net_ids = bus.get("net_ids", [])
        if not net_ids:
            net_ids = [None]

        for net_id in net_ids:
            if net_id is None:
                continue

            if guide_mode == "full":
                start_pos = Position(*_point_to_pcb(bench, bus["start_pos"]))
                end_pos = Position(*_point_to_pcb(bench, bus["end_pos"]))
                board.traceItems.append(Segment(
                    start=start_pos,
                    end=end_pos,
                    width=track_width,
                    layer=layer_name,
                    locked=locked,
                    net=int(net_id),
                    tstamp=f"guide-full-{item_id}"
                ))
                item_id += 1
                continue

            if guide_mode != "stub":
                raise ValueError(f"Unsupported guide_mode: {guide_mode}")

            endpoints = _bus_endpoint_pads(bus, int(net_id), net_to_pads)
            if not endpoints:
                # Fallback for synthetic/older benchmark without pad-level mapping.
                continue

            for endpoint_key, pad_id in endpoints:
                pad = pads.get(int(pad_id))
                if pad is None:
                    continue
                opposite = bus["end_pos"] if endpoint_key == "start_pad_ids" else bus["start_pos"]
                direction = _choose_direction(bus, endpoint_key, pad, opposite)
                pad_layer = pad.get("layer") if isinstance(pad.get("layer"), str) else layer_name

                if pad_layer != layer_name:
                    via_pos = _add_stub_segment(
                        board=board,
                        bench=bench,
                        pad=pad,
                        net_id=int(net_id),
                        layer_name=pad_layer,
                        direction=direction,
                        track_width=track_width,
                        stub_length=stub_length,
                        locked=locked,
                        tstamp=f"guide-pad-stub-{item_id}"
                    )
                    item_id += 1
                    _add_guide_via(
                        board=board,
                        bench=bench,
                        pos=via_pos,
                        net_id=int(net_id),
                        layers=[pad_layer, layer_name],
                        size=via_size,
                        drill=via_drill,
                        locked=locked,
                        tstamp=f"guide-via-{item_id}"
                    )
                    item_id += 1
                    virtual_pad = dict(pad)
                    virtual_pad["position"] = list(via_pos)
                    _add_stub_segment(
                        board=board,
                        bench=bench,
                        pad=virtual_pad,
                        net_id=int(net_id),
                        layer_name=layer_name,
                        direction=direction,
                        track_width=track_width,
                        stub_length=stub_length,
                        locked=locked,
                        tstamp=f"guide-layer-stub-{item_id}"
                    )
                    item_id += 1
                    continue

                _add_stub_segment(
                    board=board,
                    bench=bench,
                    pad=pad,
                    net_id=int(net_id),
                    layer_name=layer_name,
                    direction=direction,
                    track_width=track_width,
                    stub_length=stub_length,
                    locked=locked,
                    tstamp=f"guide-stub-{item_id}"
                )
                item_id += 1

    board.to_file(output_path)
    print(f"Exported {guide_mode} guided PCB: {output_path}")
    print(f"  Guide items added: {item_id}")
    return output_path


def export_pcb_clean(pcb_path: str, output_path: str = None):
    """
    复制 KiCad PCB 文件（不添加引导迹线）用于对照实验。

    Args:
        pcb_path: 原始 KiCad PCB 文件路径
        output_path: 输出路径
    """
    if output_path is None:
        base = os.path.splitext(pcb_path)[0]
        output_path = f"{base}_clean.kicad_pcb"

    board = Board().from_file(pcb_path)
    # 清除所有已有的迹线（只保留元件和网络定义）
    board.traceItems = []
    board.to_file(output_path)
    print(f"Exported clean PCB: {output_path}")
    return output_path


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Export KiCad PCB with layer assignment guide traces')
    parser.add_argument('--bench', type=str, required=True,
                        help='Benchmark JSON path')
    parser.add_argument('--pcb', type=str, required=True,
                        help='Original KiCad PCB file')
    parser.add_argument('--assignment', type=str, default=None,
                        help='Assignment JSON (solver output)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output PCB path')
    parser.add_argument('--clean', action='store_true',
                        help='Export clean PCB (no guide traces)')
    parser.add_argument('--guide-mode', choices=['stub', 'full'], default='stub',
                        help='Guide style for assignment export')
    parser.add_argument('--stub-length', type=float, default=2.0,
                        help='Stub length in mm for --guide-mode stub')
    parser.add_argument('--unlocked', action='store_true',
                        help='Create guide traces unlocked')
    args = parser.parse_args()

    if args.clean:
        export_pcb_clean(args.pcb, args.output)
    else:
        if args.assignment is None:
            print("Error: --assignment required (or use --clean)")
            sys.exit(1)

        with open(args.assignment, 'r') as f:
            data = json.load(f)

        # 支持多种格式: 直接 assignment 或 solver 输出结构
        if "assignment" in data:
            assignment = {int(k): int(v) for k, v in data["assignment"].items()}
        else:
            assignment = {int(k): int(v) for k, v in data.items()}

        export_pcb_with_assignment(
            args.bench, assignment, args.pcb, args.output,
            guide_mode=args.guide_mode,
            stub_length=args.stub_length,
            locked=not args.unlocked
        )
