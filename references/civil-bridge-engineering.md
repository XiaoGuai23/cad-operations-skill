# 土木与桥梁工程结构计算、Reopen-DXF 实测验图及全套施工图指南（`civil` 套件）

本模块（`cad_skill_core.civil`）实现了**零商业软件依赖**（仅使用 Python 3.12 标准数学栈与 `ezdxf`）的公路桥梁结构计算、极限状态验算、可追溯配筋账本、重开 DXF 几何实测核验及全套 **9 大类 ≥15 张 A3 施工图**生成，遵循《公路桥涵设计通用规范》（`JTG D60-2015`）、《公路钢筋混凝土及预应力混凝土桥涵设计规范》（`JTG 3362-2018`）与《公路桥涵地基与基础设计规范》（`JTG 3363-2019`）。

---

## 1. 核心能力与架构分层

1. **计算与参数联动层（`bridge_calc.py` + `bridge_checks.py`）**：
   - 明确拆分**行车道宽 / 护栏内净宽 / 桥面全宽**以及**标准跨径 / 预制梁长 / 计算跨径**，自动列出待现场实测确认项（`unresolved_assumptions`：如设计洪水位、桩长、支座距梁端距离）；
   - 一键完成曼宁水文孔径、T 梁净截面/换算截面特性、杠杆法与刚接梁抗扭修正偏心压力法、10 单元恒活载组合包络、预应力损失、代表性主梁**抗弯/抗剪/正斜截面抗裂/短暂应力/挠度验算**、行车道板与横隔梁验算、`JT/T 4-2019` 板式橡胶支座验算、双柱悬臂盖梁配筋与裂缝宽度（$\le 0.20\text{mm}$）验算、双柱墩偏压与多层地质钻孔摩擦桩桩长计算；
   - 输出统一**《配筋与钢束账本 `rebar_and_tendon_ledger`》**与**《全桥工程材料数量表 `bill_of_quantities`》**，图上任选一根钢筋或一束钢绞线均可通过 `lookup_bar_or_tendon(report, id)` 反查计算依据与全桥数量；
   - 支持 `table-audit` 导入外部有限元（Midas Civil / 桥梁博士）结果表并拦截超限误判。
2. **严格物理比例视口与通用制图原语（`symbols.py`）**：
   - `ViewBox` 保证横竖向严格按 `1:S` 出图（如 `1:400` 立面图上 `1m` 高差实测精确为 `2.50mm`），薄铺装层采用 `draw_comb_leader` 梳状外部引出线 + `1:10` 局部放大详图，零压线。
3. **重开 DXF 几何与算稿核验器（`bridge_verify.py`）**：
   - 每次出图后自动重开 `.dxf` 实测桥面/盖梁顶缘折线坡度（标 `2%` 则实测几何必为 `2.0%`）、实测标高纸面高差与 `DIMENSION` 跨度、核对 `7000 / 8000 / 9000` 道路链与主梁链，并比对钢束图有效预应力及钢筋表根数与计算书一致性，任何差值直接定位到具体图号与字段。
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
