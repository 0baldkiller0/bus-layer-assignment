"""
PCB 总线层分配 - 可视化

提供两种视图:
  1. 板面视图: 总线按层着色，显示冲突关系
  2. 指标对比: 柱状图对比不同方案
"""

import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.collections import LineCollection
import matplotlib.colors as mcolors
import numpy as np

_bench_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _bench_dir)
sys.path.insert(0, os.path.dirname(_bench_dir))


# 层颜色方案（最多支持 8 层）
LAYER_COLORS = [
    '#e74c3c',   # L1 红
    '#3498db',   # L2 蓝
    '#2ecc71',   # L3 绿
    '#f39c12',   # L4 橙
    '#9b59b6',   # L5 紫
    '#1abc9c',   # L6 青
    '#e67e22',   # L7 深橙
    '#34495e',   # L8 灰
]

CONFLICT_COLOR = '#e74c3c80'
CROSSING_COLOR = '#f39c1280'
COMPONENT_COLOR = '#bdc3c7'
OBSTACLE_COLOR = '#7f8c8d'


def draw_board_view(
    bench_path: str,
    assignment: dict = None,
    save_path: str = None,
    show_conflicts: bool = True,
    show_crossings: bool = False,
    title: str = None
):
    """
    绘制板面视图。

    Args:
        bench_path: benchmark JSON 路径
        assignment: {bus_id: layer_id}，None 则显示全部总线
        save_path: 保存路径
        show_conflicts: 是否高亮冲突对
        show_crossings: 是否高亮交叉对
    """
    with open(bench_path) as f:
        bench = json.load(f)

    components = bench["components"]
    buses = bench["buses"]
    obstacles = bench.get("obstacles", [])
    board = bench["board"]
    n_layers = board["layers"]

    fig, ax = plt.subplots(1, 1, figsize=(12, 9))

    # 画板边界
    bx0 = board.get("boundary", {}).get("dia0", [0, 0])
    bx1 = board.get("boundary", {}).get("dia1", [board["width"], board["height"]])
    board_rect = Rectangle(
        (bx0[0], bx0[1]), bx1[0] - bx0[0], bx1[1] - bx0[1],
        linewidth=2, edgecolor='#2c3e50', facecolor='white', zorder=0
    )
    ax.add_patch(board_rect)

    # 画组件
    for comp in components:
        x0, y0 = comp["dia0"]
        x1, y1 = comp["dia1"]
        rect = FancyBboxPatch(
            (x0, y0), x1 - x0, y1 - y0,
            boxstyle="round,pad=0.3",
            facecolor=COMPONENT_COLOR, edgecolor='#95a5a6',
            linewidth=1, alpha=0.7, zorder=2
        )
        ax.add_patch(rect)
        # 组件名（小字）
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ax.text(cx, cy, comp.get("name", ""),
                ha='center', va='center', fontsize=5, color='#2c3e50', zorder=3)

    # 画障碍物
    for obs in obstacles:
        x0, y0 = obs["dia0"]
        x1, y1 = obs["dia1"]
        rect = Rectangle(
            (x0, y0), x1 - x0, y1 - y0,
            facecolor=OBSTACLE_COLOR, edgecolor='#7f8c8d',
            linewidth=0.5, alpha=0.4, zorder=2
        )
        ax.add_patch(rect)

    # 收集冲突和交叉信息
    conflict_pairs = []
    crossing_pairs = []
    if show_conflicts or show_crossings:
        from evaluator import Evaluator, LayerAssignment
        if assignment:
            ev = Evaluator(bench_path)
            la = LayerAssignment(assignment=assignment)
            result = ev.evaluate(la)
            # 重新检测冲突对
            bboxes = {}
            for bus in buses:
                sx, sy = bus["start_pos"]
                ex, ey = bus["end_pos"]
                w = bus.get("width", 0.5) / 2 + 0.2
                bboxes[bus["id"]] = (min(sx,ex)-w, min(sy,ey)-w, max(sx,ex)+w, max(sy,ey)+w)
            if show_conflicts:
                for i, ba in enumerate(buses):
                    la_id = assignment.get(ba["id"])
                    if la_id is None:
                        continue
                    bb_a = bboxes[ba["id"]]
                    for j in range(i+1, len(buses)):
                        bb = buses[j]
                        if assignment.get(bb["id"]) != la_id:
                            continue
                        bb_b = bboxes[bb["id"]]
                        if not (bb_a[2]<bb_b[0] or bb_b[2]<bb_a[0] or
                                bb_a[3]<bb_b[1] or bb_b[3]<bb_a[1]):
                            conflict_pairs.append((ba["id"], bb["id"]))

    # 画总线
    for bus in buses:
        bid = bus["id"]
        layer_id = assignment.get(bid) if assignment else 0
        color = LAYER_COLORS[layer_id % len(LAYER_COLORS)]

        sx, sy = bus["start_pos"]
        ex, ey = bus["end_pos"]
        width = bus.get("width", 0.5)

        # 总线主体（粗线）
        ax.plot([sx, ex], [sy, ey],
                color=color, linewidth=max(1.5, width * 2),
                alpha=0.8, solid_capstyle='round', zorder=4)

        # 起止点标记
        ax.plot(sx, sy, 'o', color=color, markersize=4, zorder=5)
        ax.plot(ex, ey, 's', color=color, markersize=3, zorder=5)

        # 总线 ID 标注
        mx, my = (sx + ex) / 2, (sy + ey) / 2
        ax.text(mx, my, f"B{bid}", fontsize=6, ha='center', va='center',
                color='white', fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.15', facecolor=color, alpha=0.8),
                zorder=6)

    # 画冲突区域
    for bid_a, bid_b in conflict_pairs:
        bus_a = buses[bid_a]
        bus_b = buses[bid_b]
        # 画冲突区域（两个包围盒的交集近似）
        sx_a, sy_a = bus_a["start_pos"]
        ex_a, ey_a = bus_a["end_pos"]
        sx_b, sy_b = bus_b["start_pos"]
        ex_b, ey_b = bus_b["end_pos"]
        # 中点连线
        mx_a, my_a = (sx_a + ex_a) / 2, (sy_a + ey_a) / 2
        mx_b, my_b = (sx_b + ex_b) / 2, (sy_b + ey_b) / 2
        ax.plot([mx_a, mx_b], [my_a, my_b],
                '--', color=CONFLICT_COLOR, linewidth=1.5, alpha=0.6, zorder=3)

    # 图例
    legend_elements = []
    used_layers = set()
    if assignment:
        used_layers = set(assignment.values())
    else:
        used_layers = set(range(n_layers))

    for lid in sorted(used_layers):
        from matplotlib.lines import Line2D
        layer_name = board.get("layer_names", {}).get(str(lid), f"L{lid+1}")
        legend_elements.append(
            Line2D([0], [0], color=LAYER_COLORS[lid % len(LAYER_COLORS)],
                   linewidth=3, label=layer_name)
        )

    if conflict_pairs:
        legend_elements.append(
            Line2D([0], [0], linestyle='--', color=CONFLICT_COLOR, linewidth=2,
                   label=f'Conflict ({len(conflict_pairs)})')
        )

    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)

    ax.set_xlim(bx0[0] - 2, bx1[0] + 2)
    ax.set_ylim(bx0[1] - 2, bx1[1] + 2)
    ax.set_aspect('equal')
    ax.set_xlabel('X (mm)', fontsize=10)
    ax.set_ylabel('Y (mm)', fontsize=10)
    ax.set_title(title or f"Layer Assignment - {os.path.basename(bench_path)}",
                 fontsize=12, fontweight='bold')

    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {save_path}")
    else:
        plt.show()

    plt.close(fig)


def draw_comparison(
    results: dict,
    save_path: str = None,
    title: str = "Solver Comparison"
):
    """
    柱状图对比不同方案的指标。

    Args:
        results: {method_name: {"metrics": {...}, "time": ...}}
        save_path: 保存路径
    """
    methods = sorted(results.keys(), key=lambda x: results[x]["metrics"]["cost"])
    metrics_names = ["conflict_count", "crossing_count", "via_estimate"]
    metrics_labels = ["Conflicts", "Crossings", "Vias"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # 1. Conflict / Crossing / Via 对比
    ax = axes[0, 0]
    x = np.arange(len(methods))
    width = 0.25
    for i, (mname, mlabel) in enumerate(zip(metrics_names, metrics_labels)):
        vals = [results[m]["metrics"].get(mname, 0) for m in methods]
        ax.bar(x + i * width, vals, width, label=mlabel)
    ax.set_xticks(x + width)
    ax.set_xticklabels(methods, rotation=30, ha='right', fontsize=8)
    ax.set_title("Conflict / Crossing / Via")
    ax.legend(fontsize=8)
    ax.grid(axis='y', alpha=0.3)

    # 2. Cost 对比
    ax = axes[0, 1]
    costs = [results[m]["metrics"]["cost"] for m in methods]
    colors = ['#2ecc71' if c == min(costs) else '#3498db' for c in costs]
    bars = ax.bar(methods, costs, color=colors)
    ax.set_xticks(range(len(methods)))
    ax.set_xticklabels(methods, rotation=30, ha='right', fontsize=8)
    ax.set_title("Total Cost")
    ax.grid(axis='y', alpha=0.3)
    for bar, cost in zip(bars, costs):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f'{cost:.1f}', ha='center', va='bottom', fontsize=7)

    # 3. Time 对比
    ax = axes[1, 0]
    times = [results[m].get("time", 0) for m in methods]
    ax.barh(methods, times, color='#9b59b6')
    ax.set_yticks(range(len(methods)))
    ax.set_yticklabels(methods, fontsize=8)
    ax.set_xlabel("Time (s)")
    ax.set_title("Solve Time")
    ax.grid(axis='x', alpha=0.3)

    # 4. Wirelength + Layer usage
    ax = axes[1, 1]
    wirelengths = [results[m]["metrics"].get("wirelength_estimate", 0) for m in methods]
    layer_usage = [results[m]["metrics"].get("layer_usage", 0) for m in methods]
    x = np.arange(len(methods))
    ax.bar(x, wirelengths, 0.4, label='Wirelength', color='#e74c3c', alpha=0.7)
    ax2 = ax.twinx()
    ax2.plot(x, layer_usage, 'o-', color='#3498db', linewidth=2, markersize=6, label='Layers')
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=30, ha='right', fontsize=8)
    ax.set_ylabel('Wirelength', color='#e74c3c')
    ax2.set_ylabel('Layers Used', color='#3498db')
    ax.set_title("Wirelength & Layer Usage")
    ax.grid(axis='y', alpha=0.3)

    # 合并图例
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc='upper right')

    fig.suptitle(title, fontsize=14, fontweight='bold')
    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {save_path}")
    else:
        plt.show()

    plt.close(fig)


def draw_layer_detail(
    bench_path: str,
    assignment: dict,
    save_path: str = None
):
    """
    分层视图：每个子图显示一个层上的总线。

    Args:
        bench_path: benchmark JSON 路径
        assignment: {bus_id: layer_id}
        save_path: 保存路径
    """
    with open(bench_path) as f:
        bench = json.load(f)

    components = bench["components"]
    buses = bench["buses"]
    board = bench["board"]
    n_layers = board["layers"]
    bx0 = board.get("boundary", {}).get("dia0", [0, 0])
    bx1 = board.get("boundary", {}).get("dia1", [board["width"], board["height"]])

    cols = min(4, n_layers)
    rows = (n_layers + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4.5 * rows))
    if n_layers == 1:
        axes = [axes]
    else:
        axes = axes.flatten() if hasattr(axes, 'flatten') else [axes]

    for lid in range(n_layers):
        ax = axes[lid]
        color = LAYER_COLORS[lid % len(LAYER_COLORS)]
        layer_name = board.get("layer_names", {}).get(str(lid), f"L{lid+1}")

        # 板边界
        rect = Rectangle(
            (bx0[0], bx0[1]), bx1[0] - bx0[0], bx1[1] - bx0[1],
            linewidth=1.5, edgecolor='#2c3e50', facecolor='white', zorder=0
        )
        ax.add_patch(rect)

        # 组件（半透明）
        for comp in components:
            x0, y0 = comp["dia0"]
            x1, y1 = comp["dia1"]
            rect = FancyBboxPatch(
                (x0, y0), x1 - x0, y1 - y0,
                boxstyle="round,pad=0.3",
                facecolor=COMPONENT_COLOR, edgecolor='#95a5a6',
                linewidth=0.5, alpha=0.4, zorder=1
            )
            ax.add_patch(rect)

        # 该层的总线
        buses_on_layer = [b for b in buses if assignment.get(b["id"]) == lid]
        for bus in buses_on_layer:
            sx, sy = bus["start_pos"]
            ex, ey = bus["end_pos"]
            w = bus.get("width", 0.5)
            ax.plot([sx, ex], [sy, ey],
                    color=color, linewidth=max(2, w * 2.5),
                    alpha=0.85, solid_capstyle='round', zorder=3)
            ax.plot(sx, sy, 'o', color=color, markersize=4, zorder=4)
            ax.plot(ex, ey, 's', color=color, markersize=3, zorder=4)
            mx, my = (sx + ex) / 2, (sy + ey) / 2
            ax.text(mx, my, f"B{bus['id']}", fontsize=6, ha='center', va='center',
                    color='white', fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.15', facecolor=color, alpha=0.8),
                    zorder=5)

        ax.set_xlim(bx0[0] - 2, bx1[0] + 2)
        ax.set_ylim(bx0[1] - 2, bx1[1] + 2)
        ax.set_aspect('equal')
        ax.set_title(f"{layer_name} ({len(buses_on_layer)} buses)",
                     fontsize=10, fontweight='bold', color=color)
        ax.set_xlabel('X (mm)', fontsize=8)
        ax.set_ylabel('Y (mm)', fontsize=8)

    # 隐藏多余子图
    for i in range(n_layers, len(axes)):
        axes[i].set_visible(False)

    fig.suptitle(f"Layer Detail - {os.path.basename(bench_path)}",
                 fontsize=13, fontweight='bold')
    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {save_path}")
    else:
        plt.show()

    plt.close(fig)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Visualize layer assignment')
    sub = parser.add_subparsers(dest='cmd')

    # board 视图
    p_board = sub.add_parser('board', help='Board view with bus layers')
    p_board.add_argument('--bench', required=True)
    p_board.add_argument('--assignment', default=None, help='Assignment JSON')
    p_board.add_argument('--output', default=None)
    p_board.add_argument('--conflicts', action='store_true')

    # 对比视图
    p_cmp = sub.add_parser('compare', help='Compare solver results')
    p_cmp.add_argument('--results', required=True, help='Results JSON')
    p_cmp.add_argument('--output', default=None)

    # 分层视图
    p_layer = sub.add_parser('layers', help='Per-layer detail view')
    p_layer.add_argument('--bench', required=True)
    p_layer.add_argument('--assignment', required=True)
    p_layer.add_argument('--output', default=None)

    args = parser.parse_args()

    if args.cmd == 'board':
        assignment = None
        if args.assignment:
            with open(args.assignment) as f:
                data = json.load(f)
            if "assignment" in data:
                assignment = {int(k): int(v) for k, v in data["assignment"].items()}
            else:
                assignment = {int(k): int(v) for k, v in data.items()}
        draw_board_view(args.bench, assignment, args.output, show_conflicts=args.conflicts)

    elif args.cmd == 'compare':
        with open(args.results) as f:
            results = json.load(f)
        draw_comparison(results, args.output)

    elif args.cmd == 'layers':
        with open(args.assignment) as f:
            data = json.load(f)
        if "assignment" in data:
            assignment = {int(k): int(v) for k, v in data["assignment"].items()}
        else:
            assignment = {int(k): int(v) for k, v in data.items()}
        draw_layer_detail(args.bench, assignment, args.output)

    else:
        parser.print_help()
