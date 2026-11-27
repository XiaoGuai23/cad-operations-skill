# 土木与桥梁工程结构计算、Word 说明书组装、独立电算逐项复核、三维钢束/支座联动校核及全套 22 张施工图指南（`civil` 套件 `v2.8`）

本模块（`cad_skill_core.civil`）实现了**零商业软件依赖**的公路桥梁结构计算、独立桥台与桩基验算、Word/PPT 高清计算图生成、学院模板正式毕业设计说明书（`.docx`）组装、手算与独立有限元电算答辩逐项复核表、三维预应力钢束越界与支承尺寸链联动校核、DXF 语义内容哈希变更追踪、重开 DXF 实测核验及全套 **22 张 A3 施工图（`01 ~ 21`，含 `09` 束心坐标、`18` 盖梁弯起筋及挡块、`19-1` 系梁节点、`21` 耳背墙展开，并兼容 `QL-01 ~ QL-18` 18 张方案）**输出，严格遵循 `JTG B01-2014`、`JTG D60-2015`、`JTG 3362-2018`、`JTG 3363-2019`、`JTG/T 2231-01-2020`、`JT/T 4-2019` 及 `GB 1499.1/2-2024`。

---

## 1. 核心能力与毕业设计全链路闭环（`v2.8.0`）

### 1.1 开题报告三桥型方案与双口径参考文献严格统一（`bridge_calc.py` + `bridge_calc_book.py`）
- **统一三套比选方案（`bridge_schemes`）**：默认读取与开题报告完全一致的三个同桥位方案——**方案一（推荐）：`4×25 m` 装配式预应力混凝土简支 T 梁桥（桥面连续）**、**方案二：`4×25 m` 预应力混凝土连续箱梁桥**、**方案三：`4×25 m` 装配式预应力混凝土简支箱梁桥**；支持在参数中直接挂载开题报告已有的六幅立面/横断面图（`elevation_image_path` / `section_image_path`），或由系统自动按项目跨径与桥宽绘制 6 幅方案对比图（`fig0_scheme_comparison.png/.svg`）。
- **任务书与开题报告双口径参考文献（`references` + `audit_references_compliance`）**：内置并自动核验 **12 篇 GB/T 7714 核心文献（中文 ≥ 10 篇 + 外文 ≥ 2 篇）**，同时满足任务书“总数不少于 10 篇、外文不少于 1 篇”与开题报告“中文不少于 10 篇、外文不少于 1 篇”，且全部 `[1] ~ [12]` 在计算书与正式说明书正文对应段落逐一实引（自动拦截悬空未引用文献）。

### 1.2 独立有限元软件逐项验收与下部平面刚架复核（`ingest_independent_fem_results` + `solve_independent_frame_fem`）
- 支持导入 **桥梁博士 Dr.Bridge（含上部分册与下部分册）/ Midas Civil** 导出的 JSON、CSV、TSV 或 Markdown 表格（`--fem-results`、`--substructure-fem`）；
- **按必核项逐项核查构件、控制截面、荷载组合（严防“运营标准值”混入“基本组合”）、单位与来源**：缺项自动标注 **`partially_verified`（部分核对）**，生成的导入模板默认采用 `null` 空值与 `is_template: true`（原样导回绝不误判通过）；
- 内置复核求解器升级为**真实二维平面刚架刚度矩阵 `[K]{u}={F}`**（盖梁 + 双柱 + 桩顶系梁 + 桩顶等效弹簧），诚实标注复核范围（`builtin_simplified_frame_fem`），并在导入桥梁博士下部分册真实结果后优先采用实算值。

### 1.3 三维钢束轮廓越界审计与共用支承尺寸链（`bridge_tendon_and_support.py`）
- 支持直接读取桥梁博士导出的 `new.txt` 或钢束/截面 JSON（`--drbridge-tendon-model`，兼容 `N1-1 ~ N5-1` 命名），按里程逐断面同时核查**束心、波纹管外缘、最小保护层与变截面腹板轮廓**（自动拦截如 `N4` 在距梁端 `2980 mm` 处越出 `200 mm` 腹板 `10 mm` 的工程隐患并阻断出图）；图 `08`、`09` 与图 `02` 波纹管工程量共用同一套三维几何对象；
- 图 `06`、`13`、`15` 共用同一份 `support_layout` 支承坐标生成并校验 **`480 + 40 + 480 = 1000 mm`** 纵向排距链与 **`20 + 74 + 150 = 244 mm`** 竖向支承高差链。

### 1.4 统一 22 张图册清单与去敏感化 Word 毕业设计说明书（`bridge_thesis_docx.py`）
- **单一图册清单（`sheet_manifest`）共读**：DXF 图表目录（`01`）、22 页合并 A3 打印图册（含 PDF 书签大纲）、计算书第 6 章及正式说明书 **`graduation_thesis.docx`**（三级书签超链接目录、正文图号引用、附录表 1 图册清单与书签锚点）共用同一份图册清单和项目资料——改图名或增删图纸时四处同步更新；
- Word 封面默认留白下划线栏位（不残留任何参考样本学生/导师个人信息），仅在显式传入 `--student-info` / `student_metadata` 时填入。

### 1.5 按 DXF 几何/尺寸/文字/材料表做语义变更比对（`bridge_change_impact.py`）
- 采用 `compute_dxf_semantic_digest` 对归一化几何图元、原生 `DIMENSION`、文字与材料表 XDATA 计算确定性内容指纹（忽略 `$TDUPDATE` 等文件头时间戳）：**参数不变原样重跑严格报告 0 张图纸变化**；修改桥台桩长或钢束数时，精准列出真正发生内容变化的图号与计算书/说明书章节。

---

## 2. Windows / 跨平台免配置一键复现入口

```powershell
# 1. 一键生成 22 张 A3 施工图 + 合并 PDF 图册 + 6 幅 300DPI 计算图 + 计算书(MD) + 正式说明书(DOCX)
python scripts/run_bridge_suite.py --out output/wenchang_suite

# 2. 导入桥梁博士上下部实算结果 + 钢束三维坐标 new.txt + 自定义封面信息
python scripts/run_bridge_suite.py --out output/wenchang_suite --fem-results fem_super.json --substructure-fem fem_sub.json --drbridge-tendon-model new.txt --student-info student.json

# 3. 兼容旧版 18 张 QL-01~QL-18 图册编号方案
python scripts/run_bridge_suite.py --out output/wenchang_ql18 --sheet-scheme ql_18

# 4. 任点图面一个钢筋/钢束编号（如 N4、DW1、GL3、EB1、QT3），一键查询 6 维选筋溯源信息
python scripts/run_bridge_suite.py --lookup-bar EB1
```
