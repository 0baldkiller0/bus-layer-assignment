"""
可视化反馈优化结果

生成三张图:
  1. 对比柱状图: Greedy vs Feedback 各 benchmark 的 cost
  2. 收敛曲线: cost 随迭代轮数变化
  3. 板面视图: 优化前后的层分配对比
"""

import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.lines import Line2D
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LAYER_COLORS = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']
COMPONENT_COLOR = '#bdc3c7'


def draw_comparison_bar(data: dict, save_path: str):
    """Greedy vs Feedback 成本对比柱状图"""
    names = list(data.keys())
    short_names = [n.replace('buses', 'B').replace('layers', 'L').replace('_', ' ') for n in names]

    greedy_costs = [data[n]['greedy']['metrics']['cost'] for n in names]
    feedback_costs = [data[n]['feedback']['metrics']['cost'] for n in names]
    improvements = [(g - f) / g * 100 for g, f in zip(greedy_costs, feedback_costs)]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), height_ratios=[3, 1])

    # 上图：成本对比
    x = np.arange(len(names))
    w = 0.35
    bars1 = ax1.bar(x - w/2, greedy_costs, w, label='Greedy', color='#3498db', alpha=0.85)
    bars2 = ax1.bar(x + w/2, feedback_costs, w, label='FeedbackOpt', color='#e74c3c', alpha=0.85)

    ax1.set_xticks(x)
    ax1.set_xticklabels(short_names, rotation=25, ha='right', fontsize=10)
    ax1.set_ylabel('Total Cost', fontsize=12)
    ax1.set_title('Greedy vs FeedbackOpt: Cost Comparison', fontsize=14, fontweight='bold')
    ax1.legend(fontsize=11)
    ax1.grid(axis='y', alpha=0.3)

    # 标注数值
    for bar, cost in zip(bars1, greedy_costs):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 15,
                f'{cost:.0f}', ha='center', va='bottom', fontsize=8, color='#2c3e50')
    for bar, cost in zip(bars2, feedback_costs):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 15,
                f'{cost:.0f}', ha='center', va='bottom', fontsize=8, color='#c0392b')

    # 下图：改善比例
    colors = ['#2ecc71' if v > 0 else '#95a5a6' for v in improvements]
    bars3 = ax2.bar(x, improvements, 0.5, color=colors)
    ax2.set_xticks(x)
    ax2.set_xticklabels(short_names, rotation=25, ha='right', fontsize=10)
    ax2.set_ylabel('Improvement (%)', fontsize=12)
    ax2.axhline(y=0, color='#2c3e50', linewidth=0.8)
    ax2.grid(axis='y', alpha=0.3)
    ax2.set_title('Improvement over Greedy', fontsize=12)

    for bar, imp in zip(bars3, improvements):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f'{imp:+.1f}%', ha='center', va='bottom', fontsize=9, fontweight='bold')

    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches='tight')
    print(f'Saved: {save_path}')
    plt.close(fig)


def draw_convergence(data: dict, save_path: str):
    """收敛曲线: cost 随迭代轮数变化"""
    n = len(data)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 4.5))
    if n == 1:
        axes = [axes]

    for ax, (name, d) in zip(axes, data.items()):
        iters = d['iterations']
        costs = [it['cost'] for it in iters]
        accepted = [it.get('accepted', True) for it in iters]
        xs = list(range(len(costs)))

        # 画 cost 曲线
        ax.plot(xs, costs, 'o-', color='#e74c3c', markersize=4, linewidth=1.8, label='Cost')

        # 标记接受/拒绝的步骤
        for i, (c, acc) in enumerate(zip(costs, accepted)):
            if i == 0:
                continue
            marker = '^' if acc else 'x'
            color = '#2ecc71' if acc else '#95a5a6'
            ax.plot(i, c, marker, color=color, markersize=6, zorder=5)

        # 标注初始和最终 cost
        ax.annotate(f'Init: {costs[0]:.0f}',
                    xy=(0, costs[0]), xytext=(len(costs)*0.15, costs[0]),
                    fontsize=9, color='#3498db',
                    arrowprops=dict(arrowstyle='->', color='#3498db', lw=1.2))
        ax.annotate(f'Final: {costs[-1]:.0f}',
                    xy=(len(costs)-1, costs[-1]),
                    xytext=(len(costs)*0.6, costs[-1] - (max(costs)-min(costs))*0.15),
                    fontsize=9, color='#e74c3c', fontweight='bold',
                    arrowprops=dict(arrowstyle='->', color='#e74c3c', lw=1.2))

        short = name.replace('buses', 'B').replace('layers', 'L').replace('_', ' ')
        ax.set_title(short, fontsize=11, fontweight='bold')
        ax.set_xlabel('Iteration', fontsize=9)
        ax.set_ylabel('Cost', fontsize=9)
        ax.grid(alpha=0.3)

        # 图例
        legend_elements = [
            Line2D([0], [0], marker='^', color='w', markerfacecolor='#2ecc71', markersize=8, label='Accepted'),
            Line2D([0], [0], marker='x', color='#95a5a6', markersize=8, label='Rejected'),
        ]
        ax.legend(handles=legend_elements, fontsize=8, loc='upper right')

    fig.suptitle('FeedbackOpt Convergence', fontsize=14, fontweight='bold')
    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches='tight')
    print(f'Saved: {save_path}')
    plt.close(fig)


def draw_board_comparison(bench_path: str, greedy_assign: dict, feedback_assign: dict, save_path: str):
    """板面对比: Greedy vs FeedbackOpt 的层分配"""
    with open(bench_path) as f:
        bench = json.load(f)

    components = bench['components']
    buses = bench['buses']
    board = bench['board']
    n_layers = board['layers']
    bx0 = board.get('boundary', {}).get('dia0', [0, 0])
    bx1 = board.get('boundary', {}).get('dia1', [board['width'], board['height']])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    for ax, assign, title in [(ax1, greedy_assign, 'Greedy'), (ax2, feedback_assign, 'FeedbackOpt')]:
        # 板边界
        rect = Rectangle((bx0[0], bx0[1]), bx1[0]-bx0[0], bx1[1]-bx0[1],
                         linewidth=2, edgecolor='#2c3e50', facecolor='white', zorder=0)
        ax.add_patch(rect)

        # 组件
        for comp in components:
            x0, y0 = comp['dia0']
            x1, y1 = comp['dia1']
            rect = FancyBboxPatch((x0, y0), x1-x0, y1-y0,
                                  boxstyle='round,pad=0.3',
                                  facecolor=COMPONENT_COLOR, edgecolor='#95a5a6',
                                  linewidth=0.5, alpha=0.5, zorder=1)
            ax.add_patch(rect)

        # 统计冲突数
        bboxes = {}
        for bus in buses:
            sx, sy = bus['start_pos']
            ex, ey = bus['end_pos']
            w = bus.get('width', 0.5) / 2 + 0.2
            bboxes[bus['id']] = (min(sx,ex)-w, min(sy,ey)-w, max(sx,ex)+w, max(sy,ey)+w)

        conflicts = 0
        conflict_lines = []
        for i, ba in enumerate(buses):
            la = assign.get(str(ba['id'])) or assign.get(ba['id'])
            if la is None: continue
            bb_a = bboxes[ba['id']]
            for j in range(i+1, len(buses)):
                bb = buses[j]
                lb = assign.get(str(bb['id'])) or assign.get(bb['id'])
                if lb is None or lb != la: continue
                bb_b = bboxes[bb['id']]
                if not (bb_a[2]<bb_b[0] or bb_b[2]<bb_a[0] or bb_a[3]<bb_b[1] or bb_b[3]<bb_a[1]):
                    conflicts += 1
                    conflict_lines.append((ba, bb))

        # 画冲突连线
        for ba, bb in conflict_lines:
            mx1 = (ba['start_pos'][0]+ba['end_pos'][0])/2
            my1 = (ba['start_pos'][1]+ba['end_pos'][1])/2
            mx2 = (bb['start_pos'][0]+bb['end_pos'][0])/2
            my2 = (bb['start_pos'][1]+bb['end_pos'][1])/2
            ax.plot([mx1, mx2], [my1, my2], '--', color='#e74c3c80', linewidth=1.2, zorder=3)

        # 画总线
        for bus in buses:
            bid = bus['id']
            lid = assign.get(str(bid)) or assign.get(bid) or 0
            color = LAYER_COLORS[lid % len(LAYER_COLORS)]
            sx, sy = bus['start_pos']
            ex, ey = bus['end_pos']
            w = bus.get('width', 0.5)
            ax.plot([sx, ex], [sy, ey], color=color, linewidth=max(2, w*2.5),
                    alpha=0.85, solid_capstyle='round', zorder=4)
            ax.plot(sx, sy, 'o', color=color, markersize=4, zorder=5)
            ax.plot(ex, ey, 's', color=color, markersize=3, zorder=5)
            mx, my = (sx+ex)/2, (sy+ey)/2
            ax.text(mx, my, f'B{bid}', fontsize=6, ha='center', va='center',
                    color='white', fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.15', facecolor=color, alpha=0.8), zorder=6)

        ax.set_xlim(bx0[0]-2, bx1[0]+2)
        ax.set_ylim(bx0[1]-2, bx1[1]+2)
        ax.set_aspect('equal')
        ax.set_xlabel('X (mm)', fontsize=9)
        ax.set_ylabel('Y (mm)', fontsize=9)
        ax.set_title(f'{title}  (conflicts={conflicts})', fontsize=12, fontweight='bold',
                     color='#2c3e50' if conflicts == 0 else '#e74c3c')

    # 图例
    legend_elements = [Line2D([0], [0], color=LAYER_COLORS[i], linewidth=3,
                              label=board.get('layer_names', {}).get(str(i), f'L{i+1}'))
                       for i in range(n_layers)]
    legend_elements.append(Line2D([0], [0], linestyle='--', color='#e74c3c80',
                                  linewidth=1.5, label='Conflict'))
    fig.legend(handles=legend_elements, loc='lower center', ncol=n_layers+1,
               fontsize=9, bbox_to_anchor=(0.5, -0.02))

    fig.suptitle(f'Layer Assignment Comparison: {os.path.basename(bench_path)}',
                 fontsize=14, fontweight='bold')
    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches='tight')
    print(f'Saved: {save_path}')
    plt.close(fig)


if __name__ == '__main__':
    data_path = 'benchmark/opt_viz_data.json'
    out_dir = 'benchmark/opt_figures'
    os.makedirs(out_dir, exist_ok=True)

    with open(data_path) as f:
        data = json.load(f)

    # 图1: 柱状对比
    draw_comparison_bar(data, f'{out_dir}/1_cost_comparison.png')

    # 图2: 收敛曲线
    draw_convergence(data, f'{out_dir}/2_convergence.png')

    # 图3: 板面对比 (取 buses20_layers2_dense)
    key = 'buses20_layers2_dense'
    if key in data:
        d = data[key]
        draw_board_comparison(
            d['bench_path'],
            d['greedy']['assignment'],
            d['feedback']['assignment'],
            f'{out_dir}/3_board_comparison.png'
        )

    print(f'\nAll figures saved to {out_dir}/')
