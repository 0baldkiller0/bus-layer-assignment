# BusAllocator — PCB 总线层分配与可布通性反馈优化

面向 PCB 总线规划的层分配与可布通性反馈优化方法研究。从 KiCad PCB 设计出发，提取总线结构、生成层分配方案、以规划侧代理指标和 Freerouting 详细布线指标进行闭环验证。

## 项目结构

```
BA/
├── BusAllocator.py              # 从 KiCad 解析结果中提取 bus
├── GridParameters.py            # 解析 KiCad PCB/project 的板框、层、footprint、pad、netclass
├── benchmark/
│   ├── generator.py             # Synthetic benchmark 生成（random/crossing/dense）
│   ├── evaluator.py             # 层分配方案评价器（conflict/crossing/congestion/via/wirelength/unrouted）
│   ├── solvers.py               # Baseline 层分配求解器（Random/Greedy/GraphColoring/OptimalSearch）
│   ├── feedback_solver.py       # 反馈式层分配优化（failure detection + move/swap + seed-risk）
│   ├── ablation.py              # 消融实验脚本
│   ├── exporter.py              # 从 KiCad 设计导出 benchmark JSON
│   ├── pcb_exporter.py          # 将层分配结果写回 KiCad PCB（stub/guide/corridor）
│   ├── dsn_exporter.py          # KiCad PCB → SPECCTRA DSN 转换
│   ├── freerouting_headless.py  # Freerouting headless 调用封装
│   ├── freerouting_workflow.py  # 完整 KiCad→Freerouting→SES 验证流程
│   ├── ses_parser.py            # SPECCTRA SES 结果解析
│   ├── routed_parser.py         # 布线后 PCB 连通性判断（UnionFind，pad-segment-via 连通图）
│   ├── routed_corridor_score.py # 实际走线与 planner corridor 的 adherence 指标
│   ├── phase1_high_conflict_pcb.py      # 小型高冲突 clean PCB 样本生成
│   ├── phase1_dummy_corridor_test.py    # Dummy corridor / seed 实验主脚本
│   ├── phase1_seed_sweep.py             # Seed-only sweep 批量实验
│   ├── phase1_full_lock_test.py         # Full locked trace 诊断
│   ├── phase1_scatter.py                # Scatter 实验
│   ├── router_corridor_penalty_sweep.py # Freerouting 源码 corridor penalty 批量实验
│   └── dsn_semantic_probe.py           # 最小 DSN 语义验证
├── freerouting_src/             # Freerouting 源码（带 corridor-aware cost penalty 补丁）
├── runs/                        # 实验运行产物（DSN/SES/KiCad PCB/结果 JSON）
└── doc/                         # 阶段总结与周报
```

## Benchmark 格式

Benchmark JSON 描述一个 PCB 总线层分配问题实例：

```json
{
  "meta": {"source": "kicad", "coordinate_system": "board_local", "units": "mm"},
  "board": {"width": 101.6, "height": 53.34, "layers": 4, "layer_names": {...}, "boundary": {...}},
  "buses": [{"id": 0, "net_ids": [1,2,3], "start_pos": [x,y], "end_pos": [x,y], "width": 0.5, ...}],
  "components": [{"id": ..., "dia0": [x,y], "dia1": [x,y], ...}],
  "pads": [{"net_id": 1, "position": [x,y], "type": "thru_hole", ...}],
  "obstacles": [{"dia0": [x,y], "dia1": [x,y], ...}],
  "netclasses": {"Default": {"track_width": 0.25, ...}}
}
```

坐标规范：所有坐标使用相对板框左下角的局部坐标（`coordinate_system: "board_local"`）。Evaluator 内置 `_detect_absolute_coordinates()` 兼容旧格式。

## 评价指标

| 指标 | 含义 |
|---|---|
| `conflict_count` | 同层 bbox/manhattan-corridor 重叠的 bus 对数 |
| `crossing_count` | 同层 bus 中心线交叉对数量 |
| `layer_usage` | 实际使用的层数 |
| `congestion_max/avg` | 栅格化拥塞密度的最大值和均值 |
| `via_estimate` | 基于起止方向变化和 net 跨层数的过孔估计 |
| `wirelength_estimate` | 已分配 bus 的曼哈顿距离之和 |
| `unrouted_estimate` | 冲突数 ≥3 的高风险 bus 数 |
| `cost` | 综合加权代价 |

详细布线指标（通过 Freerouting/SES 获得）：routed/unrouted nets、wirelength、segments、vias、runtime、corridor inside ratio。

## 快速开始

### 从 KiCad 导出 benchmark

```bash
python benchmark/exporter.py --pcb path/to/board.kicad_pcb --pro path/to/board.kicad_pro --output benchmark/my_bench.json
```

### 运行层分配 baseline

```bash
python benchmark/solvers.py benchmark/my_bench.json --method greedy --output assignment.json
```

### 评价层分配方案

```bash
python benchmark/evaluator.py benchmark/my_bench.json assignment.json
```

### Freerouting 闭环验证

```bash
# 1. 生成 PCB 变体（clean / seed / corridor）
python benchmark/phase1_dummy_corridor_test.py \
  --bench benchmark/my_bench.json \
  --pcb path/to/clean.kicad_pcb \
  --output-dir runs/my_experiment \
  --include-solvers --seed-guide stub \
  --passes 20 --threads 4 --timeout 120

# 2. 解析布线后 PCB 连通性
python benchmark/routed_parser.py \
  --pcb runs/my_experiment/greedy/routed.kicad_pcb \
  --bench benchmark/my_bench.json

# 3. 计算 corridor adherence
python benchmark/routed_corridor_score.py \
  --run-dir runs/my_experiment \
  --output runs/my_experiment/adherence.json
```

### Patched Freerouting（corridor-aware cost penalty）

设置 `FREEROUTING_JAR` 环境变量指向 patched jar：

```powershell
$env:FREEROUTING_JAR = 'D:\code\BA\freerouter\freerouting-ba-corridor.jar'
```

构建 patched jar：

```bash
cd freerouting_src
./gradlew executableJar
```

## Baseline 方法

| 方法 | 文件 | 说明 |
|---|---|---|
| Random | `solvers.py:RandomSolver` | 随机分配 bus 到可用层 |
| Greedy | `solvers.py:GreedySolver` | 逐 bus 贪心选择冲突最小的层 |
| GraphColoring | `solvers.py:GraphColoringSolver` | 基于冲突图的着色启发式 |
| OptimalSearch | `solvers.py:OptimalSearchSolver` | 穷举搜索（仅适用于极小规模） |
| FeedbackOpt | `feedback_solver.py:FeedbackSolver` | 失败模式检测 + 候选生成 + 迭代优化 |

## 复现说明

1. 安装依赖：Python 3.11+, `kiutils`, `python-docx`
2. 构建 Freerouting patched jar：`cd freerouting_src && ./gradlew executableJar`
3. 运行实验：参考 `benchmark/phase1_seed_sweep.py` 的命令行示例
4. 结果汇总：实验 JSON 保存在 `runs/` 目录下，包含 config/methods/entries 结构
