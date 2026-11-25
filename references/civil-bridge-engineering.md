# 土木与桥梁工程结构计算、导师逐步复核计算书、Reopen-DXF 验图及全套施工图指南（`civil` 套件 `v2.6`）

本模块（`cad_skill_core.civil`）实现了**零商业软件依赖**（仅使用 Python 3.12 标准数学栈与 `ezdxf`）的公路桥梁结构计算、独立桥台与地基基础验算、手算与电算交叉校核、全链路钢筋溯源计算书、重开 DXF 实测核验及全套 **9 大类 18 张 A3 施工图（`QL-01 ~ QL-18`）**生成，遵循《公路桥涵设计通用规范》（`JTG D60-2015`）、《公路钢筋混凝土及预应力混凝土桥涵设计规范》（`JTG 3362-2018`）、《公路桥涵地基与基础设计规范》（`JTG 3363-2019`）与《公路桥梁抗震设计规范》（`JTG/T 2231-01-2020`）。

---

## 1. 核心架构与四项关键闭环

### 1.1 桥墩与桥台各自独立受力计算 + 实测水文地勘资料接入（`bridge_calc.py` + `bridge_checks.py`）
- **独立的桩柱式桥台计算（`check_pile_bent_abutment`）**：不再采用“桥墩桩长减 2m”经验估算，而是由本桥台**边跨单跨支座恒活载反力 + 6m 桥头搭板半重及活载 + 台帽/背墙牛腿/耳墙自重 + 朗肯主动土压力（$K_a$）与车辆换算等代土层厚（$h_{eq}$）**独立完成台帽抗弯抗剪与裂缝验算（`QT1`）、背墙根部抗弯与 `35d` 锚固验算（`QT2`）、支座垫石局压验算（`DS1`），并按 `JTG 3363-2019 §5.3.3` 分层侧摩阻力与持力层嵌固深度独立求解桥台设计桩长（`QT3`）。
- **结构化实测水文与钻孔地勘接入（`ingest_bridge_survey_data` / `--survey`）**：支持直接输入实测河床断面坐标点列 `[(x_m, z_m), ...]` 与设计洪水位（精确积分过水面积 $A$、湿周 $\chi$ 与水面宽 $B_w$）、路线纵断面设计高程、墩台钻孔分层物理力学指标（`top_elev_m/bot_elev_m` 或 `thickness_m`、`qik_kpa`、`fa0_kpa`）及支座中心距梁端定位距离，自动解除对应初拟假定项并同步更新全套计算书、配筋表与工程量表。

### 1.2 导师逐步复核计算书 + 独立手算对算校核 + 任点钢筋编号 6 维溯源（`bridge_calc_book.py`）
- **代表性 T 梁 + 中间双柱墩 + 桩柱式桥台完整计算书（`generate_bridge_calculation_book_markdown`）**：自动输出六章结构化 Markdown 计算说明书（含工程概况与三桥型方案比选表、1#边梁逐步代入过程、中间双柱墩盖梁/墩柱/系梁/桩基逐步代入过程、桩柱式桥台及土压力/桩长独立推导、手算与电算对算表、全图册钢筋溯源矩阵及 GB/T 7714 参考文献；说明书篇幅按开题报告“正文约一万字”及学校模板与导师确认要求提示）。
- **独立解析手算对算（`compute_independent_hand_cross_check`）**：用闭合解析公式对**1#代表性 T 梁跨中弯矩 $M_d$、支点剪力 $V_d$（含 `[0, L/4]` 横向分布系数梯形积分对比）、两侧相邻跨传给中墩总反力 $\sum R_{u,\text{pier}}$、双柱墩柱底轴力 $N_d$、桥墩桩顶荷载 $R_{\text{pier,sls}}$ 与桥台桩顶荷载 $R_{\text{abut,sls}}$** 逐项对算校核。
- **任点钢筋/钢束全链路反查（`lookup_bar_or_tendon`）**：从 `QL-08 ~ QL-18` 图上任选一个编号（`N1 ~ DS1`），一键返回**统计口径与数量（`quantity_scope`）、选筋规范依据（`selection_basis`）、控制内力需求与抗力（`governing_forces`）、荷载工况与组合公式（`load_case_and_combination`）及原始参数确认/假定出处（`parameter_provenance`）**。

### 1.3 “结构计算通过”与“毕设资料齐全”双状态分离显示
- 顶层报告、图册清单（`drawing_set_manifest.json`）、`QL-01` 设计总说明与计算书首页均独立展示：
  - `calculation_status`（`passed` / `failed`）：当前输入参数下全部结构极限状态与几何一致性验算结论；
  - `graduation_readiness_status`（`ready` / `pending_confirmation` / `failed`）：外业水文地勘定位依据与毕设交付物是否全部确认齐套；
  - `assumption_dependent_conclusions`：明确列出当前哪些跨径净空、内力配束或墩台桩长结论仍基于初拟假定；
  - **严格 E1/E2 抗震完成核验**：仅传入 `seismic_checks: {"status": "pending"}` 或缺少 E1/E2 数值型需求/能力结果与复核记录（`checked_by` / `review_record`）时，严格保持 `pending`，绝不误判为 `completed`。

### 1.4 经得起“从图上量、到表里查”的 Reopen-DXF 核验器（`bridge_verify.py`）
- **`QL-09`**：`1:80` 半跨梁肋立面实画竖向箍筋间距严格等于 `@100`（`1.25mm` 纸面间距，21 根）与 `@150`（`1.875mm` 纸面间距，69 根），半跨实画根数 `×2` 严格等于钢筋表 `GJ1` 总根数；
- **`QL-17` & `QL-18`**：10 列构件分组钢筋明细表与图面附注同步写明按**单根墩柱 / 单道系梁 / 单根钻孔桩 / 单个垫石**统计及按**整座双柱墩 / 整座桥台**汇总；
- **`QL-18`**：平面定位图与正立面配筋图的 `A-A` 剖切线均精确穿过所画 `2#支座垫石` 中心，重开 DXF 自动校验剖切线与垫石几何交叠关系。

---

## 2. Windows / 跨平台免配置一键复现入口

无需手动设置 `PYTHONPATH` 或 `PYTHONUTF8`，在中文 Windows（`cp936/GBK`）或 macOS/Linux 终端直接运行同一条命令即可复现全套 18 张 A3 施工图、结构验算 JSON 与导师逐步复核计算书：

```powershell
# 1. 一键生成全套 18 张 A3 施工图 (QL-01~QL-18) + 结构计算报告 JSON + 导师逐步复核计算书 Markdown
python scripts/run_bridge_suite.py --out output/wenchang_18_sheets

# 2. 接入实测河床断面与钻孔地勘 JSON，并同时导出 1:1 A3 黑白打印 PDF 与高清 PNG 预览
python scripts/run_bridge_suite.py --out output/wenchang_surveyed --survey survey.json --with-previews

# 3. 从图上任点一个钢筋或钢束编号（如 GJ1、GL1、DZ1、QT3），一键查询数量口径、选筋依据、控制内力、荷载组合与参数出处
python scripts/run_bridge_suite.py --lookup-bar QT3
```

也可通过标准 CLI 调用各细分模式：

```powershell
$env:PYTHONPATH="runtime/cad-2d-skill/src"
python -m cad_skill_core.cli bridge-calc --mode calc-book --out output/bridge_calculation_book.md
python -m cad_skill_core.cli bridge-calc --mode cross-check
python -m cad_skill_core.cli bridge-calc --mode lookup-bar --bar-id GJ1
```
