"""
PCB 总线层分配 - 布线结果解析器

从 Freerouting/KiCad 布线后的 PCB 文件中提取布线质量指标。
未布通判断使用 pad-segment-via 连通性，而不是简单检查 net id 是否出现。
"""

import json
import math
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiutils.board import Board


DEFAULT_TOLERANCE = 0.08


class UnionFind:
    def __init__(self):
        self.parent = []

    def add(self) -> int:
        idx = len(self.parent)
        self.parent.append(idx)
        return idx

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _safe_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _distance(a: tuple, b: tuple) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_segment_distance(p: tuple, a: tuple, b: tuple) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return _distance(p, a)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


def _point_in_bbox(point: tuple, bbox: tuple, tol: float) -> bool:
    x, y = point
    x0, y0, x1, y1 = bbox
    return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol


def _bench_point_to_pcb(bench: dict, point) -> tuple:
    """兼容 board-local benchmark 和旧的 KiCad absolute benchmark。"""
    x, y = point[:2]
    board = bench.get("board", {})
    boundary = board.get("boundary", {})
    ox, oy = boundary.get("dia0", [0.0, 0.0])[:2]
    width = board.get("width", 0.0)
    height = board.get("height", 0.0)

    if bench.get("meta", {}).get("coordinate_system") == "board_local":
        return x + ox, y + oy
    if -1e-6 <= x <= width + 1e-6 and -1e-6 <= y <= height + 1e-6:
        return x + ox, y + oy
    return x, y


def _bench_bbox_to_pcb(bench: dict, dia0, dia1) -> tuple:
    x0, y0 = _bench_point_to_pcb(bench, dia0)
    x1, y1 = _bench_point_to_pcb(bench, dia1)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _signal_layers(board: Board, bench: Optional[dict]) -> List[str]:
    layers = []
    for layer in board.layers:
        if getattr(layer, "type", None) == "signal":
            name = getattr(layer, "name", None) or getattr(layer, "canonicalName", None)
            if name is not None:
                layers.append(name)
    if layers:
        return layers

    if bench:
        names = bench.get("board", {}).get("layer_names", {})
        if isinstance(names, dict):
            return [v for _, v in sorted(names.items()) if isinstance(v, str)]
        if isinstance(names, list):
            return [v for v in names if isinstance(v, str)]
    return ["F.Cu", "B.Cu"]


def _collect_trace_items(board: Board):
    segments = []
    vias = []
    for item in board.traceItems:
        item_type = type(item).__name__
        net_id = _safe_int(getattr(item, "net", None))
        if item_type == "Segment":
            segments.append({
                "start": (item.start.X, item.start.Y),
                "end": (item.end.X, item.end.Y),
                "width": float(item.width),
                "layer": item.layer,
                "net": net_id,
                "locked": bool(getattr(item, "locked", False)),
                "tstamp": getattr(item, "tstamp", "")
            })
        elif item_type == "Via":
            vias.append({
                "pos": (item.position.X, item.position.Y),
                "size": float(item.size),
                "drill": float(item.drill),
                "layers": [l for l in (item.layers or [])],
                "net": net_id,
                "locked": bool(getattr(item, "locked", False)),
                "tstamp": getattr(item, "tstamp", "")
            })
    return segments, vias


def _expected_nets_from_bench(bench: dict) -> List[int]:
    expected = set()
    for bus in bench.get("buses", []):
        for nid in bus.get("net_ids", []):
            nid = _safe_int(nid)
            if nid is not None:
                expected.add(nid)
    return sorted(expected)


def _pads_by_net(bench: dict) -> Dict[int, List[dict]]:
    result = defaultdict(list)
    for pad in bench.get("pads", []):
        nid = _safe_int(pad.get("net_id"))
        if nid is not None:
            result[nid].append(pad)
    return result


def _pad_layers(pad: dict, signal_layers: List[str]) -> List[str]:
    pad_type = str(pad.get("type", "")).lower()
    if "thru" in pad_type:
        return signal_layers
    layer = pad.get("layer")
    return [layer] if isinstance(layer, str) else signal_layers


def _analyze_net_connectivity(
    net_id: int,
    bench: dict,
    pads: List[dict],
    segments: List[dict],
    vias: List[dict],
    signal_layers: List[str],
    tolerance: float
) -> dict:
    net_segments = [s for s in segments if s["net"] == net_id]
    net_vias = [v for v in vias if v["net"] == net_id]

    uf = UnionFind()
    nodes = []

    def add_node(kind: str, pos: tuple, layer: str, ref=None) -> int:
        idx = uf.add()
        nodes.append({"kind": kind, "pos": pos, "layer": layer, "ref": ref})
        return idx

    pad_nodes = []
    for pad in pads:
        pos = _bench_point_to_pcb(bench, pad.get("position", [0, 0]))
        bbox = _bench_bbox_to_pcb(bench, pad.get("dia0", pos), pad.get("dia1", pos))
        for layer in _pad_layers(pad, signal_layers):
            nid = add_node("pad", pos, layer, {"pad": pad, "bbox": bbox})
            pad_nodes.append(nid)

    endpoint_nodes = []
    segment_nodes = []
    for seg in net_segments:
        a = add_node("endpoint", seg["start"], seg["layer"], seg)
        b = add_node("endpoint", seg["end"], seg["layer"], seg)
        uf.union(a, b)
        endpoint_nodes.extend([a, b])
        segment_nodes.append((a, b, seg))

    via_nodes = []
    for via in net_vias:
        layers = via["layers"] or signal_layers
        current = []
        for layer in layers:
            node = add_node("via", via["pos"], layer, via)
            current.append(node)
            via_nodes.append(node)
        for node in current[1:]:
            uf.union(current[0], node)

    # Connect close endpoints/vias on the same layer.
    connectable = endpoint_nodes + via_nodes
    for i in range(len(connectable)):
        ni = nodes[connectable[i]]
        for j in range(i + 1, len(connectable)):
            nj = nodes[connectable[j]]
            if ni["layer"] != nj["layer"]:
                continue
            if _distance(ni["pos"], nj["pos"]) <= tolerance:
                uf.union(connectable[i], connectable[j])

    # Connect pads to segment endpoints, segment bodies, and vias.
    for pad_node in pad_nodes:
        pad = nodes[pad_node]
        pad_bbox = pad["ref"]["bbox"]
        pad_pos = pad["pos"]
        pad_w = max(0.0, pad_bbox[2] - pad_bbox[0])
        pad_h = max(0.0, pad_bbox[3] - pad_bbox[1])
        pad_radius = max(pad_w, pad_h) / 2.0

        for other_node in endpoint_nodes + via_nodes:
            other = nodes[other_node]
            if pad["layer"] != other["layer"]:
                continue
            if _point_in_bbox(other["pos"], pad_bbox, tolerance):
                uf.union(pad_node, other_node)
            elif _distance(pad_pos, other["pos"]) <= pad_radius + tolerance:
                uf.union(pad_node, other_node)

        for a_node, b_node, seg in segment_nodes:
            if pad["layer"] != seg["layer"]:
                continue
            clearance = max(tolerance, seg["width"] / 2.0 + tolerance)
            if _point_segment_distance(pad_pos, seg["start"], seg["end"]) <= pad_radius + clearance:
                uf.union(pad_node, a_node)

    pad_roots = {uf.find(n) for n in pad_nodes} if pad_nodes else set()
    connected_pad_count = 0
    if pad_nodes:
        root_counts = defaultdict(int)
        for n in pad_nodes:
            root_counts[uf.find(n)] += 1
        connected_pad_count = max(root_counts.values()) if root_counts else 0

    is_fully_routed = len(pad_nodes) <= 1 or len(pad_roots) == 1

    wirelength = 0.0
    for seg in net_segments:
        wirelength += _distance(seg["start"], seg["end"])

    return {
        "net_id": net_id,
        "pad_count": len(pad_nodes),
        "connected_pad_count": connected_pad_count,
        "component_count": len(pad_roots),
        "is_fully_routed": is_fully_routed,
        "segment_count": len(net_segments),
        "via_count": len(net_vias),
        "wirelength": round(wirelength, 4)
    }


def parse_routed_pcb(pcb_path: str, bench_path: str = None, tolerance: float = DEFAULT_TOLERANCE) -> dict:
    """
    解析布线后的 KiCad PCB 文件，提取布线质量指标。

    Args:
        pcb_path: 布线后的 .kicad_pcb 文件路径
        bench_path: benchmark JSON 路径（可选，用于获取期望 net/pad 列表）
        tolerance: 几何连通容差（mm）

    Returns:
        dict: 布线质量指标
    """
    board = Board().from_file(pcb_path)
    bench = None
    if bench_path:
        with open(bench_path, 'r', encoding='utf-8') as f:
            bench = json.load(f)

    segments, vias = _collect_trace_items(board)

    total_wirelength = sum(_distance(s["start"], s["end"]) for s in segments)

    layer_wirelength = defaultdict(float)
    layer_counts = defaultdict(int)
    for seg in segments:
        layer_wirelength[seg["layer"]] += _distance(seg["start"], seg["end"])
        layer_counts[seg["layer"]] += 1

    per_net = {}
    unrouted_nets = []
    expected_nets = []
    if bench:
        expected_nets = _expected_nets_from_bench(bench)
        pads_for_net = _pads_by_net(bench)
        signal_layers = _signal_layers(board, bench)
        for nid in expected_nets:
            net_result = _analyze_net_connectivity(
                net_id=nid,
                bench=bench,
                pads=pads_for_net.get(nid, []),
                segments=segments,
                vias=vias,
                signal_layers=signal_layers,
                tolerance=tolerance
            )
            per_net[str(nid)] = net_result
            if not net_result["is_fully_routed"]:
                unrouted_nets.append(nid)

    result = {
        "total_segments": len(segments),
        "total_vias": len(vias),
        "total_wirelength": round(total_wirelength, 4),
        "layer_wirelength": {k: round(v, 4) for k, v in sorted(layer_wirelength.items())},
        "layer_segment_counts": dict(sorted(layer_counts.items())),
        "unrouted_nets": unrouted_nets,
        "unrouted_count": len(unrouted_nets),
        "per_net": per_net
    }

    if bench:
        result["expected_net_count"] = len(expected_nets)
        result["routed_net_count"] = len(expected_nets) - len(unrouted_nets)

    return result


def compare_routed_results(results: dict):
    """打印不同方案的布线结果对比。"""
    print(f"\n{'Method':<20} {'Wirelength':>12} {'Vias':>6} {'Unrouted':>9} {'Segments':>10}")
    print("-" * 62)

    for name, r in sorted(results.items(), key=lambda x: x[1].get("unrouted_count", 0)):
        print(f"{name:<20} {r['total_wirelength']:>12.1f} "
              f"{r['total_vias']:>6} {r['unrouted_count']:>9} "
              f"{r['total_segments']:>10}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse routed KiCad PCB for quality metrics")
    parser.add_argument("--pcb", type=str, required=True,
                        help="Routed KiCad PCB file")
    parser.add_argument("--bench", type=str, default=None,
                        help="Benchmark JSON (for unrouted detection)")
    parser.add_argument("--output", type=str, default=None,
                        help="Save metrics to JSON")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="Connectivity tolerance in mm")
    args = parser.parse_args()

    result = parse_routed_pcb(args.pcb, args.bench, args.tolerance)

    print(f"Routed PCB Analysis: {os.path.basename(args.pcb)}")
    print(f"  Segments: {result['total_segments']}")
    print(f"  Vias: {result['total_vias']}")
    print(f"  Total wirelength: {result['total_wirelength']:.1f} mm")
    if result["unrouted_count"] > 0:
        print(f"  Unrouted nets: {result['unrouted_count']} ({result['unrouted_nets']})")
    else:
        print("  All expected nets routed!")

    print("\n  Layer wirelength distribution:")
    for layer, wl in result["layer_wirelength"].items():
        print(f"    {layer}: {wl:.1f} mm")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nSaved to {args.output}")
