# 土木与桥梁工程结构计算、Reopen-DXF 实测验图及全套施工图指南（`civil` 套件）

本模块（`cad_skill_core.civil`）实现了**零商业软件依赖**（仅使用 Python 3.12 标准数学栈与 `ezdxf`）的公路桥梁结构计算、极限状态验算、可追溯配筋账本、重开 DXF 几何实测核验及全套 **9 大类 ≥15 张 A3 施工图**生成，遵循《公路桥涵设计通用规范》（`JTG D60-2015`）、《公路钢筋混凝土及预应力混凝土桥涵设计规范》（`JTG 3362-2018`）与《公路桥涵地基与基础设计规范》（`JTG 3363-2019`）。

---

## 1. 核心能力与架构分层

1. **计算与参数联动层（`bridge_calc.py` + `bridge_checks.py`）**：
   - 明确拆分**行车道宽 / 护栏内净宽 / 桥面全宽**以及**标准跨径 / 预制梁长 / 计算跨径**；桥面沥青与调平层厚度直接驱动**主梁二期恒载（`dead_load_breakdown`）与全桥工程量同步联动**；中间桥墩自动区分**左右相邻两跨恒载反力之和 + 双跨满布/单跨偏心活载工况（`pier_reaction_summary`）**；
   - 严格区分开题初拟值与已确认依据：未显式提供桥位水文实测断面、钻孔地勘分层摩阻力（`soil_layers`）及支座中心距梁端定位依据（`calc_span_m` / `bearing_center_offset_m`）时，绝不清空 `unresolved_assumptions`；同时在 `pending_graduation_deliverables` 中单列**完整毕业设计说明书（含中英文摘要、三级目录、计算过程、图表和参考文献）、桥位水文地勘核定及 E1/E2 抗震验算**；
   - 任何构件验算不满足时保留完整计算书供排查，汇总至 `failed_checks`（含失败构件、需求值、容许限值及关联图号），并将失败状态传播至关联图纸与 18 张图总报告 `status = "failed"`；
   - 输出统一**《配筋与钢束账本 `rebar_and_tendon_ledger`》**与**《全桥工程材料数量表 `bill_of_quantities`》**，图上任选一根钢筋或一束钢绞线均可通过 `lookup_bar_or_tendon(report, id)` 反查计算依据与全桥数量；支持 `table-audit` 导入外部有限元结果表并拦截超限误判。
2. **严格物理比例视口与通用制图原语（`symbols.py`）**：
   - `ViewBox` 保证横竖向严格按 `1:S` 出图（如 `1:400` 立面图上 `1m` 高差实测精确为 `2.50mm`），薄铺装层采用 `draw_comb_leader` 梳状外部引出线 + `1:10` 局部放大详图，零压线。
3. **重开 DXF 几何、可见表格与钢筋实体核验器（`bridge_verify.py`）**：
   - 每次出图后自动重开 `.dxf` 实测桥面/盖梁顶缘折线坡度（标 `2%` 则实测几何必为 `2.0%`）、实测标高纸面高差与 `DIMENSION` 跨度、拦截图面伪合格不等式（如 `12.82MPa ≤ 10.0MPa`）；
   - 直接解析图面可见 `TEXT` 钢筋明细表（不盲信附着元数据），核对表内单长×根数算术关系、计算书账本一致性、图面引出标签以及**断面实画钢筋圆点实体（`CIRCLE`）根数**；同时逐项核查 `QL-13` 五组桥面附属详图（防撞护栏、桥面连续、D80型钢伸缩装置、Ø100泄水管、6m桥头搭板）及 `QL-17/18` 下部各构件独立配筋视图与尺寸。
4. **全套 18 张 A3 施工图册与习惯学习闭环（`bridge_drawings.py`）**：
   - 单张或 `full_set` 一键生成 `QL-01 ~ QL-18` 共 18 张 A3 施工图，并通过 `audit_task_book_coverage` 自动逐项核对毕业设计任务书全部 **9 大类图纸**、**≥15 张 A3 图幅**与 **100% 钢筋/钢束编号溯源**；
   - 接入 `preference_memory.py` 与 `regression_flywheel.py`，从参考样图或指令学习字体、图层配色、尺寸链间距等可复用画法（换桥宽/梁距自动沿用画法但采用新尺寸，陌生风格样图不覆盖已确认习惯），并将修正过的尺寸/坡度/比例错误自动沉淀为回归用例。

---

## 2. CLI 快速调用示例

```powershell
$env:PYTHONPATH="runtime/cad-2d-skill/src"

# 1. 运行信阳市文昌中桥（4×25m，单幅9.0m宽）全桥计算、验算、配筋账本与工程量汇总
python -m cad_skill_core.cli bridge-calc --mode wenchang --out output/wenchang_calc.json

# 2. 先单独生成并重开 DXF 实测核验“主梁标准横断面构造图”（7000/8000/9000 + 4×1950+2×600 + 2%真坡度）
python -m cad_skill_core.cli generate-civil t_girder_section --out output/wenchang_cross

# 3. 一键生成全套 18 张 A3 施工图册（覆盖任务书全部 9 类）+ 覆盖率与编号溯源报告
python -m cad_skill_core.cli generate-civil full_set --out output/wenchang_18_sheets
```
