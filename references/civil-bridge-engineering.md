# 土木与桥梁工程结构计算、Word 说明书组装、独立电算复核、计算图及全套施工图指南（`civil` 套件 `v2.7`）

本模块（`cad_skill_core.civil`）实现了**零商业软件依赖**的公路桥梁结构计算、独立桥台与桩基验算、Word/PPT 高清计算图生成、学院模板正式毕业设计说明书（`.docx`）组装、手算与独立有限元电算答辩复核表、一次设计变更全成果影响追踪、重开 DXF 实测核验及全套 **9 大类 18 张 A3 施工图（`QL-01 ~ QL-18`）**输出，严格遵循 `JTG B01-2014`、`JTG D60-2015`、`JTG 3362-2018`、`JTG 3363-2019`、`JTG/T 2231-01-2020` 与 `JT/T 4-2019`。

---

## 1. 核心能力与毕业设计全链路闭环（`v2.7.0`）

### 1.1 开题报告三桥型方案与双口径参考文献严格统一（`bridge_calc.py` + `bridge_calc_book.py`）
- **统一三套比选方案（`bridge_schemes`）**：默认读取与开题报告完全一致的三个同桥位方案——**方案一（推荐）：`4×25 m` 装配式预应力混凝土简支 T 梁桥（桥面连续）**、**方案二：`4×25 m` 预应力混凝土连续箱梁桥**、**方案三：`4×25 m` 装配式预应力混凝土简支箱梁桥**；支持在参数中直接挂载开题报告已有的六幅立面/横断面图（`elevation_image_path` / `section_image_path`），或由系统自动按项目跨径与桥宽绘制 6 幅方案对比图（`fig0_scheme_comparison.png/.svg`）。
- **任务书与开题报告双口径参考文献（`references` + `audit_references_compliance`）**：内置并自动核验 **12 篇 GB/T 7714 核心文献（中文 ≥ 10 篇 + 外文 ≥ 2 篇）**，同时满足任务书“总数不少于 10 篇、外文不少于 1 篇”与开题报告“中文不少于 10 篇、外文不少于 1 篇”，且全部 `[1] ~ [12]` 在计算书与正式说明书正文对应段落逐一实引（自动拦截悬空未引用文献）。

### 1.2 独立有限元软件结果导入与答辩现场解释对照表（`ingest_independent_fem_results` + `build_defense_verification_table`）
- 支持直接导入 **桥梁博士 Dr.Bridge / Midas Civil** 导出的 JSON、CSV、TSV 或 Markdown 表格（`--fem-results`），自动生成**建模边界记录卡**（跨度、单元离散、支座约束、截面特性、荷载工况与组合）与**答辩现场并排复核表**；
- 优先对比四大核心控制指标：**代表性 T 梁跨中弯矩 $M_d$、支点剪力 $V_d$、消除自重活载挠度 $f_{\text{live}}$、中间双柱墩墩顶双跨总反力 $\sum R_{u,\text{pier}}$**，自动列出手算值、本程序杆系值、外部软件值、相对差异与物理/力学原因解释（未导入外部软件结果时严禁伪造数值，清晰标注 `待导入`）。

### 1.3 直接生成能进 Word 与答辩 PPT 的 300 DPI 计算图（`bridge_figures.py`）
每次从实时计算数据自动生成带单位、工况与控制极值标注的 `300 DPI PNG + 矢量 SVG`（位于 `<output_dir>/figures/`）：
1. `fig0_scheme_comparison`：**开题三桥型方案立面与标准横断面比选图（6 幅子图）**；
2. `fig1_structural_model`：**结构计算简图**（1# 简支 T 梁 10 单元计算跨径/支座约束/车道荷载简图 + 2# 中间双柱墩盖梁受荷简图）；
3. `fig2_moment_shear_envelope`：**主梁弯矩与剪力组合包络及抗力对比图**；
4. `fig3_tendon_and_prestress`：**预应力钢束抛物线线形及有效预应力 $\sigma_{pe}(x)$ 沿梁长变化图**；
5. `fig4_pile_soil_profile`：**中间双柱墩与桩柱式桥台钻孔灌注桩分层土层及桩端持力层位置对比图**。

### 1.4 按学院模板组装正式 Word 毕业设计说明书（`bridge_thesis_docx.py`）
- 自动将现有 `QL-01 ~ QL-18` 18 张施工图、开题三方案比选、5 幅高清计算图、代表性 T 梁/双柱墩/独立桩柱式桥台完整代入过程、独立电算复核对照表与 12 篇中英文参考文献组装成标准中文高校排版的 **`graduation_thesis.docx`**（含封面、中英文摘要、双状态核查声明、11 章正文、学术三线表、内嵌高清图、参考文献、致谢与工程量附录）。

### 1.5 一次设计变更查出所有受影响成果（`bridge_change_impact.py`）
- 每次重跑出图自动与上一版基线（或 `--baseline`）比对，生成 `change_impact_report.json` 并直接打印**“本次改了什么、涉及哪些计算书/说明书章节、涉及哪些施工图图号（含磁盘 DXF SHA-256 实测变更核验）”**：
  - **桥台桩长改动后**：计算书第 4/5/6 章、正式说明书第 9/10 章与附录、`fig4_pile_soil_profile`、`QL-02`（水下 C30 方量与钢筋总重）、`QL-03`、`QL-15`（立面桩长标注）、`QL-18`（`QT3` 单根长与 `QT4` 螺旋箍圈数）同步更新；
  - **钢束数量改动后**：主梁抗弯/抗剪/抗裂/挠度验算、`fig2`/`fig3`、`QL-08`（断面管道、抛物线束与张拉表）、`QL-09`（选筋依据）、`QL-02`（钢绞线总重）同步一致。

---

## 2. Windows / 跨平台免配置一键复现入口

```powershell
# 1. 一键生成 18 张 A3 施工图 + 5 幅 Word/PPT 计算图 + 导师复核计算书(MD) + 学院模板正式说明书(DOCX)
python scripts/run_bridge_suite.py --out output/wenchang_suite

# 2. 导入桥梁博士 / Midas Civil 独立电算结果并生成答辩现场复核对照表 + 自定义学院封面信息
python scripts/run_bridge_suite.py --out output/wenchang_suite --fem-results fem_drbridge.json --student-info student.json

# 3. 修改设计参数（如改桥台桩长或钢束数）重跑，自动列出本次改了什么、涉及哪些章节与图号
python scripts/run_bridge_suite.py --out output/wenchang_suite --params rev_params.json

# 4. 任点图面一个钢筋/钢束编号（如 N1、GJ1、DZ1、QT3），一键查询 6 维选筋溯源信息
python scripts/run_bridge_suite.py --lookup-bar QT3
```
