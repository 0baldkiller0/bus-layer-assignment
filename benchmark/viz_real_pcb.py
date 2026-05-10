"""
真实 PCB 布局可视化 — 焊盘级别精度
"""

import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, FancyBboxPatch
from matplotlib.lines import Line2D
import numpy as np

LAYER_COLORS = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c']


def draw_real_pcb(bench_path, assignment=None, save_path=None, title=None):
    """用真实焊盘位置绘制 PCB 布局"""
    with open(bench_path) as f:
        bench = json.load(f)

    components = bench["components"]
    pads = bench["pads"]
    buses = bench["buses"]
    nets = bench["nets"]
    board = bench["board"]
    bx0 = board["boundary"]["dia0"]
    bx1 = board["boundary"]["dia1"]

    fig, ax = plt.subplots(1, 1, figsize=(14, 10))

    # 板边界
    rect = Rectangle((bx0[0], bx0[1]), bx1[0]-bx0[0], bx1[1]-bx0[1],
                     linewidth=2, edgecolor='#2c3e50', facecolor='#fafafa', zorder=0)
    ax.add_patch(rect)

    # 画元件（细框，标注名称）
    for comp in components:
        x0, y0 = comp["dia0"]
        x1, y1 = comp["dia1"]
        rect = FancyBboxPatch(
            (x0, y0), x1-x0, y1-y0,
            boxstyle="round,pad=0.2",
            facecolor='#ecf0f1', edgecolor='#bdc3c7',
            linewidth=0.5, alpha=0.6, zorder=1
        )
        ax.add_patch(rect)
        cx, cy = (x0+x1)/2, (y0+y1)/2
        # 只标注非通用元件
        name = comp["name"]
        if name not in ("0603", "0402") and comp["pad_count"] > 3:
            ax.text(cx, cy, name, ha='center', va='center',
                    fontsize=6, color='#2c3e50', fontweight='bold', zorder=2)

    # 画所有焊盘（用 dia0/dia1 实际边界）
    net_colors = {}
    color_palette = plt.cm.Set3(np.linspace(0, 1, 12))
    color_idx = 0

    for pad in pads:
        dx0, dy0 = pad["dia0"]
        dx1, dy1 = pad["dia1"]
        net_id = pad["net_id"]

        # 跳过无网络焊盘
        if net_id is None or net_id == 0:
            color = '#95a5a6'
            alpha = 0.3
        else:
            if net_id not in net_colors:
                net_colors[net_id] = color_palette[color_idx % len(color_palette)]
                color_idx += 1
            color = net_colors[net_id]
            alpha = 0.7

        pw = dx1 - dx0
        ph = dy1 - dy0

        shape = pad["shape"]
        if shape == "rect":
            r = Rectangle((dx0, dy0), pw, ph,
                          facecolor=color, edgecolor='#2c3e50',
                          linewidth=0.3, alpha=alpha, zorder=3)
            ax.add_patch(r)
        else:
            cx = (dx0 + dx1) / 2
            cy = (dy0 + dy1) / 2
            radius = max(pw, ph) / 2
            c = Circle((cx, cy), radius,
                      facecolor=color, edgecolor='#2c3e50',
                      linewidth=0.3, alpha=alpha, zorder=3)
            ax.add_patch(c)

    # 画总线（粗线覆盖在焊盘上）
    for bus in buses:
        bid = bus["id"]
        layer_id = assignment.get(bid) if assignment else 0
        color = LAYER_COLORS[layer_id % len(LAYER_COLORS)]

        sx, sy = bus["start_pos"]
        ex, ey = bus["end_pos"]
        w = bus.get("width", 0.5)

        ax.plot([sx, ex], [sy, ey],
                color=color, linewidth=max(2.5, w*3),
                alpha=0.8, solid_capstyle='round', zorder=5)

        # 起止焊盘高亮
        for pid in bus.get("start_pad_ids", []):
            if pid < len(pads):
                px, py = pads[pid]["position"]
                ax.plot(px, py, 'o', color=color, markersize=8, zorder=6)
        for pid in bus.get("end_pad_ids", []):
            if pid < len(pads):
                px, py = pads[pid]["position"]
                ax.plot(px, py, 's', color=color, markersize=7, zorder=6)

        # Bus 标注
        mx, my = (sx+ex)/2, (sy+ey)/2
        ax.text(mx, my, f'B{bid}', fontsize=8, ha='center', va='center',
                color='white', fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.2', facecolor=color, alpha=0.9),
                zorder=7)

    # 图例
    layer_names = board.get("layer_names", {})
    legend_elements = [
        Line2D([0], [0], color=LAYER_COLORS[i], linewidth=3,
               label=layer_names.get(str(i), f'L{i+1}'))
        for i in range(board["layers"])
    ]
    legend_elements.append(
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#3498db',
               markersize=8, label='Bus start pad')
    )
    legend_elements.append(
        Line2D([0], [0], marker='s', color='w', markerfacecolor='#3498db',
               markersize=7, label='Bus end pad')
    )
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)

    ax.set_xlim(bx0[0]-2, bx1[0]+2)
    ax.set_ylim(bx1[1]+2, bx0[1]-2)  # Y 轴翻转，匹配 KiCad 坐标
    ax.set_aspect('equal')
    ax.set_xlabel('X (mm)', fontsize=10)
    ax.set_ylabel('Y (mm)', fontsize=10)
    ax.set_title(title or f"PCB Layout - {os.path.basename(bench_path)}",
                 fontsize=13, fontweight='bold')

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {save_path}")
    plt.close(fig)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--bench', required=True)
    parser.add_argument('--assignment', default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    assignment = None
    if args.assignment:
        with open(args.assignment) as f:
            data = json.load(f)
        if "assignment" in data:
            assignment = {int(k): int(v) for k, v in data["assignment"].items()}
        else:
            assignment = {int(k): int(v) for k, v in data.items()}

    draw_real_pcb(args.bench, assignment, args.output)
