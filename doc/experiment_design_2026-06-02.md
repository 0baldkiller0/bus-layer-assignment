# 实验设计方案

## 核心研究问题

> **面向 PCB 总线规划的层分配与可布通性反馈优化方法研究**

三个待验证的假设：

1. **H1**: 层分配方案影响真实布线质量（wirelength、via count、routed nets）
2. **H2**: 现有 proxy evaluator（bbox 冲突计数）与真实 Freerouting 布线结果之间存在差距，这导致 FeedbackSolver 无法超越 Greedy
3. **H3**: 用真实 Freerouting 布线结果作为反馈信号，可以显著改善层分配质量

---

## 当前状态总览

### 已有资产

| 类型 | 数量 | 状态 |
|---|---|---|
| 合成 benchmark | 25 个 (5/10/20/50 buses × 2/4 layers × 3 scenarios) | 可运行 |
| 真实 PCB benchmark | 4 个 (bench1/2/4 + ulx3s) | DSN→Freerouting→SES 管线已通 |
| Baseline 求解器 | Random / Greedy / GraphColoring / OptimalSearch | 可运行 |
| Feedback 求解器 | 基于 proxy evaluator 的迭代优化 | 可运行，但效果≈Greedy |
| SES 指标解析 | ses_parser.py（scale 已修正） | 可运行 |
| SES→PCB 回写 | ses_to_pcb.py（Y轴/scale 已修正） | 可运行 |
| Headless Freerouting | freerouting_headless.py | 可运行 |

### 关键发现（诊断阶段）

**发现 1：合成 benchmark 上 Feedback ≈ Greedy**

| 改善幅度 | benchmark 数 | 占比 |
|---|---|---|
| 0%（完全相同） | 11/25 | 44% |
| 1–7% | 8/25 | 32% |
| 8–19% | 6/25 | 24% |

在 44% 的 benchmark 上 Feedback 与 Greedy 完全一致；大部分改善也只有个位数百分比。

**发现 2：真实 PCB 上 Feedback = Greedy（完全一致）**

| 基准板 | 方法 | Wirelength | Segments | Vias | Routed Nets |
|---|---|---|---|---|---|
| bench1_v2 | clean | 4276mm | 1196 | 94 | 97 |
| bench1_v2 | greedy_stub | 4108mm | 1083 | 120 | 99 |
| bench1_v2 | feedback_stub | 4108mm | 1083 | 120 | 99 |
| bench2_v2 | 全部三种 | 366mm | 137 | 7 | 15 |
| bench4_v2 | clean | 810mm | 408 | 23 | 35 |
| bench4_v2 | greedy_stub | 747mm | 396 | 29 | 35 |
| bench4_v2 | feedback_stub | 747mm | 396 | 29 | 35 |
| ulx3s_v1 | greedy_stub | 3020mm | 1805 | 127 | 126 |
| ulx3s_v1 | feedback_stub | 3020mm | 1805 | 127 | 126 |

Feedback 和 Greedy 在所有真实 PCB 上产生**完全相同的**布线结果。

---

## 实验设计

### Phase 1：诊断实验 — Proxy 与真实布线的相关性分析

**目标**：证明现有 proxy evaluator 无法准确预测真实布线质量，需要真实布线反馈。

**实验 1.1：Proxy cost vs 真实布线指标的散点图**

对每个 benchmark，生成 100 个随机层分配方案，计算：
- X 轴：evaluator proxy cost（conflict_count × 10 + crossing_count × 5 + ...）
- Y 轴：Freerouting 真实结果（wirelength、vias、unrouted nets）

**预期**：如果相关性低（R² < 0.5），说明 proxy 不能替代真实布线反馈。

**执行方式**：
```
for each bench in [bench1_v2, bench4_v2, ulx3s_v1]:
    生成 100 个随机 LayerAssignment
    对每个 assignment:
        export stub-guided PCB → Freerouting headless → parse SES
    绘制 scatter plot
```

**数据量**：3 bench × 100 assignments = 300 次 Freerouting 运行（可并行/批量）

---

**实验 1.2：按 proxy cost 排名 vs 真实布线排名的一致性**

将 100 个随机方案按 proxy cost 排序，再按真实 unrouted_net_count 排序，计算 Spearman 秩相关系数。

**预期**：秩相关系数 < 0.6，说明 proxy 排序与真实排序不一致。

---

**实验 1.3：Greedy 在 proxy space 和 real space 的最优性**

- 在 proxy cost 维度：Greedy 是否接近全局最优？（在 5-bus 实例上与 OptimalSearch 对比）
- 在真实布线维度：是否存在 proxy cost 更高但真实布线更好的方案？

**执行方式**：对 bus ≤ 5 的实例穷举搜索，对比 proxy-optimal 和 routing-optimal。

**关键问题**：如果 Greedy 在 proxy space 已经接近最优，但真实布线不是——这就是"反馈驱动优化"的立论基础。

---

### Phase 2：核心实验 — 真实布线反馈优化

**目标**：实现 RealRoutingFeedbackSolver，证明真实布线反馈可以改善层分配。

**实验 2.1：RealRoutingFeedbackSolver vs Greedy（核心对比）**

算法流程：
```
Step 1: Greedy 初始分配 → 导出 DSN → Freerouting → 解析 SES → 记录 baseline 指标
Step 2: 识别真实布线失败的 net（从 SES 中找出 unrouted nets）
Step 3: 对每个失败 net 所属的 bus，尝试换层
Step 4: 重新导出 DSN → Freerouting → 解析 SES
Step 5: 如果 unrouted 减少，接受新方案；否则回滚
Step 6: 重复 Step 2–5，直到不可布通数收敛或达到迭代上限
```

**对比指标**：

| 指标 | 说明 |
|---|---|
| Unrouted net count | 真实不可布通网络数（主要指标） |
| Total wirelength | 总布线长度 |
| Total vias | 总过孔数 |
| Iterations | 反馈迭代轮数 |
| Runtime | 总耗时（含 Freerouting 时间） |

**测试集**：bench1_v2, bench4_v2, ulx3s_v1（bench2 太简单，三维方法完全一致，跳过）

---

**实验 2.2：反馈迭代的过程可视化**

对 bench4_v2，展示 feedback 迭代过程：

```
Iteration 0 (Greedy): unrouted=5, wirelen=747mm, vias=29
    → 3 nets in high-conflict zone on Layer 0
Iteration 1: move Bus_X to Layer 1 → unrouted=3, wirelen=730mm
    → 2 nets still failing near component Y
Iteration 2: swap Bus_Y ↔ Bus_Z → unrouted=1
    → converge
```

这种图是论文中的核心案例。

---

### Phase 3：消融与补充实验

**实验 3.1：消融 — 反馈信号的贡献分解**

| 变体 | 反馈信号 | 说明 |
|---|---|---|
| No Feedback | 无 | 等于 Greedy |
| Proxy Only | evaluator.cost | 当前 FeedbackSolver（无效） |
| Via Feedback | SES vias | 只最小化过孔数 |
| Wirelen Feedback | SES wirelength | 只最小化线长 |
| Unrouted Feedback | SES unrouted | 只最小化不可布通数 |
| Full Feedback | 全部组合 | RealRoutingFeedbackSolver |

---

**实验 3.2：不同初始方案对反馈优化的影响**

| 初始方案 | 说明 |
|---|---|
| Random (best of 5) | 随机 → 反馈 |
| Greedy | 贪心 → 反馈 |
| GraphColoring | 图着色 → 反馈 |
| Perturbed Greedy (20% shuffle) | 加扰动 → 反馈 |

验证：好的初始方案是否需要更少的反馈迭代？

---

**实验 3.3：复杂场景下的优势放大**

在 benchmark 生成器中，调节参数生成"hard"场景：

| 参数 | 简单 | 困难 |
|---|---|---|
| bus 数量 | 5 | 50 |
| 层数 | 4 | 2 |
| 场景 | parallel | crossing / dense |
| 障碍物密度 | 0 | 0.3 |

**假设**：简单场景下 Greedy 已经够好，feedback 的优势在困难场景下才明显。如果这个假设成立，就可以解释为什么在现有 benchmark 上拉不开差距。

---

### Phase 4：真实 PCB 多board 验证

**实验 4.1：external_samples 上的可扩展性**

对 `external_samples/` 中的 5 个开源 PCB（CO60、open_covg_daq_pcb、openlst-hw、ulx3s、Zynq-SoM），运行完整管线：

```
KiCad PCB → 总线识别 → 层分配（Greedy vs Feedback）→ DSN → Freerouting → SES → 对比
```

**重点**：在复杂 PCB 上，Feedback 是否比 Greedy 有更大的改善？

**数据量**：5 个 PCB，每个 2 种方法 = 10 次管线运行。

---

### 实验矩阵总览

| Phase | 实验 | benchmark 数 | 方法数 | Freerouting 次数 | 预计耗时 |
|---|---|---|---|---|---|
| 1.1 | Proxy-Reality scatter | 3 | 100 random | 300 | ~2h |
| 1.2 | Rank correlation | 3 | 100 random | 0（复用 1.1） | 0 |
| 1.3 | Optimality gap | 6 (bus≤8) | OptimalSearch | 0 | <1min |
| 2.1 | Real feedback vs Greedy | 3 | 2 | ~30 (每个 5 iter × 3 bench × 2) | ~30min |
| 2.2 | Iteration visualization | 1 | 1 | 5 | ~5min |
| 3.1 | Ablation | 3 | 6 variants | ~60 | ~1h |
| 3.2 | Initial solution | 3 | 4 variants | ~40 | ~40min |
| 3.3 | Hard scenarios | 6 (generated) | 2-3 | ~30 | ~30min |
| 4.1 | External PCB | 5 | 2 | 10 | ~30min |
| **合计** | | | | **~475** | **~5h** |

> 注：每次 Freerouting headless 约 10–60 秒（取决于 PCB 复杂度），时间估算比较保守。可并行化。

---

### 执行顺序

```
Step 1 (立即): 修复残留技术债
  ├── 重跑 ses_metrics.json（已完成 ✓）
  └── 更新所有 routed_comparison.json

Step 2: Phase 1 诊断实验
  └── 如果没有 proxy-reality gap → 重新审视问题定义
  └── 如果有显著 gap → 进入 Phase 2

Step 3: Phase 2 核心实验
  ├── 如果反馈有效 → 进入 Phase 3 + 4
  └── 如果无效 → 分析根因，调整策略

Step 4: Phase 3 + 4 消融 + 扩展
  └── 收集所有实验数据，准备论文图表
```

---

### 论文可用的图表规划

| 图 | 类型 | 内容 |
|---|---|---|
| Fig 1 | 示意图 | 问题定义：PCB 总线、层、冲突 |
| Fig 2 | 流程图 | RealRoutingFeedbackSolver 算法 |
| Fig 3 | 散点图 | Proxy cost vs Real routability（Phase 1.1） |
| Fig 4 | 柱状图 | Greedy vs Feedback 在真实布线指标上（Phase 2.1） |
| Fig 5 | 迭代曲线 | 反馈优化的收敛过程（Phase 2.2） |
| Fig 6 | 消融柱状图 | 各反馈信号的贡献（Phase 3.1） |
| Fig 7 | 复杂度曲线 | 困难场景下的优势放大（Phase 3.3） |
| Tab 1 | 表 | 所有方法在所有 benchmark 上的指标汇总 |
| Tab 2 | 表 | Ablation results |

---

### 风险预案

| 风险 | 可能性 | 应对 |
|---|---|---|
| Proxy-Reality 相关性很高（R² > 0.7）| 低 | 说明 proxy 够用，focus 放在改进 proxy 精度上 |
| 真实反馈也改善不了 Greedy | 中 | 说明 2-layer 问题已近最优，转向 4-layer / 多约束场景 |
| Freerouting 运行太慢 | 低 | 缩短 timeout、并行运行、用更小的 benchmark |
| 所有方法在真实 PCB 上都一样 | 中 | 缩小 benchmark、增大难度（减少层数，增加障碍物） |
