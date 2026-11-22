# CAD Operations Skill

面向 AI Agent（Codex / Claude Code / Cursor / Antigravity）与工程自动化的 **二维 + 三维全闭环 CAD 智能制图、局部修订、质量核验与自动出图 Web 平台引擎**。

---

## 核心能力全景

| 能力域 | 核心模块与工程行为 |
| --- | --- |
| **工程文档智能语义编译** | 自动读取 `.md` / `.txt` / `.csv` / `.tsv` / `.docx`（含合并单元格去重）/ `.pdf` 项目书与参数表，编译 `DrawingSpec`，自动拦截缺失尺寸（`missing`）与冲突参数（`conflicts`） |
| **参考样图标准学习** | 自动扫描参考样图 DXF 的图层、颜色、线宽、字高、`DIMSTYLE` 与图幅，导出可复用的 `DrawingStandard` YAML |
| **国标二维自动制图、7 大经典零件族与工厂级符号** | 覆盖**直齿圆柱齿轮（GB/T 4459.2 轮齿不剖+分度圆）**、**减速器轴承透盖**、**带加强筋 L 型直角支座**、连接法兰、带颈法兰、多阶梯轴、腰形槽支架等全系平放/正交“主、俯、左”三视图，原生绘制 **60° 表面粗糙度 `Ra` 三角勾、基准符号 `[A]` 与形位公差三格框**、标准分格国标标题栏、自下而上 4 列国标明细表与真实字形防压线避让 |
| **4 套多行业标准预设一键切换** | 内置 `gb-mechanical`（机械精加工）、`gb-heavy-machinery`（重工夹具总装）、`gb-civil-construction`（市政土建）与 `iso-international`（国际英文蓝图）四套标准，支持一键切换线宽序列与标题栏风格 |
| **Rubric 100 分制审图打分与 CAD-1K 评测器** | 五维量化审图引擎（几何拓扑、图层线宽、三视图对齐、尺寸链完整度、图框字体，自动评定 `S/A/B/C/D` 级），兼容 `cad-1000-hours` 的 `rubrics.json`，内置免视频轻量离线基准评测器 `benchmark-cad1k` |
| **蓝色虚线框选“指哪改哪”与局部原子改图** | 绑定原图 SHA-256，支持在蓝图上**拉出蓝色虚线框直接输入口语指令（如“把孔改成 Φ18”、“删掉虚线”、“材料改成 40Cr”）原地热改图**，也支持结构化线段/圆弧/复合轮廓编辑、`DocumentCheckpoint` 异常回滚与修改前后分屏对比 |
| **三维参数化建模、工业 `.step` 导出与 3D→2D 联动** | 零额外商业软件依赖构建水密流形（Watertight Manifold）三维实体（**三维直齿圆柱齿轮、轴承透盖、带加强筋支座**、组合体 `compound_3d`、法兰、阶梯轴、腰形槽板等），导出 **ISO 10303-21 工业标准 `.step`** / `.stl` / `.obj` / `.glb` / `model3d.dxf` / `iso.png`，一键正交/剖视投影生成二维国标工程图，并独立重开回测输出 `audit-3d.json` |
| **跨版本 AutoCAD 与国产 CAD 驱动层** | 支持 **AutoCAD 2021 (`R24.0`) – 2026 (`R25.1`)**、**中望 CAD（ZWCAD / `ZWCoConsole`）**、**浩辰 CAD（GstarCAD）**、**CAXA 电子图板** 与 **ODA File Converter** 的 Authenticode 签名校验发现、国标字体融合、受控脚本执行与 **DWG ↔ DXF 双向结构无损核验** |
| **电气与道路行业专项出图** | 单母/分段/双母线变电站电气主接线图（`DXF`/`SVG`/`PDF`/`PNG` + 交叉审计）与道路沥青/水稳路面结构图（原生 `HATCH` + `DIMENSION`） |
| **标准 MCP App 与自动出图 Web 工作台** | 原生支持 MCP `stdio`、Streamable HTTP 与 **MCP App 常驻分屏审图台（`ui://cad-operations/viewer.html`）**，暴露 20 个强校验 Tools + 6 个 Resources，支持白底蓝图 **“蓝色虚线框选 + 悬浮胶囊一句话原地改图”**、实时 `🏆 图纸评分` 徽章、三维特征棱线交互检视与内置 **Web Studio 2.3（`/studio`）** |

---

## 安装

运行工具要求 **Python 3.12**。下列命令以 Windows PowerShell 为例：

```powershell
$skillPath = Join-Path $env:USERPROFILE '.codex/skills/cad-operations'
git clone https://github.com/XiaoGuai23/cad-operations-skill.git $skillPath
Set-Location -LiteralPath $skillPath
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -e './runtime/cad-2d-skill[dev]'
```

保留 `runtime/cad-2d-skill` 的完整目录，其中的 `knowledge/` 和 `templates/` 参与运行。根目录是主 skill；`companion-skills/cad-2d-geometry/` 是可选的机械几何专用入口。

---

## 快速启动：内置自动出图网站工作台（Web Studio）与 MCP Server

### 1. 启动可视化自动出图网站（支持二维预览 + 三维鼠标拖拽旋转检视）

```powershell
$env:PYTHONPATH="runtime\cad-2d-skill\src"
python -B -m cad_skill_core.cli mcp-http --host 127.0.0.1 --port 8765 --workspace output/mcp_workspace
```

启动后保持终端运行，用浏览器打开 **http://127.0.0.1:8765/studio** 即可在线解析项目书、一键生成国标二维机械图 / 三维模型（`.glb`/`.stl`/`.dxf`）+ 二维工程图 / 电气主接线图 / 路面结构图并下载产物。详细接口说明见 [MCP Server 与自动出图 Web 网关指南](references/mcp-server-guide.md)。

### 2. 命令行核心工作流示例

```powershell
# 1) 从项目书编译二维规约 & 从样图学习图层/标注标准
python -m cad_skill_core compile-spec examples/project_brief.md --out output/drawing-spec.json --json
python -m cad_skill_core learn-standard sample.dxf --out output/custom-standard.yaml --json

# 2) 三维水密建模 (.stl/.obj/.glb/model3d.dxf/iso.png) + 3D->2D 国标多视图工程图一键联动
python -m cad_skill_core generate-3d model3d-spec.json --out output/part3d --with-2d-sheet --json

# 3) 探测本机 AutoCAD (2021-2026) 与国产 CAD (中望/浩辰/CAXA/ODA) 驱动 & DWG<->DXF 往返无损校验
python -m cad_skill_core drivers --json
python -m cad_skill_core verify-roundtrip before.dxf after.dxf --json

# 4) 现有 DXF 增量原子修订（先 dry-run 预演，再正式落盘）
python -m cad_skill_core operations --json
python examples/revision-tools/make_example.py output/revision-demo
python -m cad_skill_core inventory output/revision-demo/baseline.dxf --json
python -m cad_skill_core revise output/revision-demo/baseline.dxf output/revision-demo/plan.json --out output/revision-demo/revised --dry-run --json
python -m cad_skill_core revise output/revision-demo/baseline.dxf output/revision-demo/plan.json --out output/revision-demo/revised --json

# 5) Rubric 100分制审图体检 & 免视频 cad-1000-hours 离线基准评测
python -m cad_skill_core score-drawing output/revision-demo/revised/revised.dxf --json
python -m cad_skill_core benchmark-cad1k --out output/cad1k-eval --json

# 6) 行业专项图纸与环境诊断
python -m cad_skill_core doctor --cache output/cad-environment.json --json
python scripts/make_main_sld.py examples/electrical_sld_spec.json --outdir output/sld --stem demo_sld
python scripts/generate_pavement_cad_ezdxf.py examples/pavement_structure_spec.json --out output/pavement
```

执行修订前可参阅[修订工具说明](references/revision-tools.md)和[字段/API 合约](references/revision-tools-api.md)。

---

## 验证

```powershell
python tests/smoke_test.py
Push-Location runtime/cad-2d-skill
python -m pytest tests -q -m 'not autocad'
Pop-Location
```

运行工具测试使用合成输入（970+ 自动化单测与端到端集成测试）。中文整图测试需要字体覆盖中文及 `⌀` 等工程符号；字体策略位于 `runtime/cad-2d-skill/knowledge/drawing-rules/font-policy.yaml`。GitHub Actions 在临时运行环境中下载并校验 [GNU Unifont 16.0.04](https://unifoundry.com/pub/unifont/unifont-16.0.04/font-builds/)，不会修改仓库默认策略或本机字体。

---

## 目录结构

- `SKILL.md`：按任务路由所需说明的顶层入口。
- `references/`：MCP 与 Web 网关指南、修订设计方法、标注、分段、批注台账、预览和本地 CAD 指引。
- `runtime/cad-2d-skill/src/cad_skill_core/`：
  - `documents/` & `standards/`：多格式工程文档编译器与样图标准学习器
  - `geometry/`、`layout/`、`fonts/`、`rendering/`、`inspection/`：二维参数化几何、真实字宽防碰撞布局、确定性渲染与自动避让修复
  - `revision/`：哈希绑定局部原子修订与前后对比审阅引擎
  - `modeling3d/`：三维参数化水密网格内核、多格式导出器（STL/OBJ/GLB/3D-DXF）、3D→2D 投影器与三维质量验证器
  - `cad_drivers/` & `autocad/`：AutoCAD 2021–2026 及国产 CAD（中望/浩辰/CAXA/ODA）安全驱动层与 DWG↔DXF 往返核验器
  - `mcp/`：标准 MCP（`stdio` + HTTP）服务端、子进程隔离异步任务队列、安全鉴权与内置 Web Studio 工作台
- `scripts/`、`examples/`、`tests/`：电气主接线图/路面结构图生成器及全链路烟雾测试。

---

## 支持范围与许可证

修订与生成内核直接产出审计级 DXF/SVG/PNG/PDF/STL/OBJ/GLB；原生 DWG 转换、打印出图和原生实体建模需要本机安装受信任签名的 AutoCAD（2021–2026）、中望 CAD、浩辰 CAD 或 ODA 转换器，仓库本身不包含商业 CAD 软件或商业字体文件。

原始 CAD Operations 技能来自 [2836048681/cad-operations-skill](https://github.com/2836048681/cad-operations-skill)，本仓库保留原有 [MIT 许可证](LICENSE)。运行工具中改编自 ezdxf 的代码及版权说明见 [THIRD_PARTY_NOTICES.md](runtime/cad-2d-skill/THIRD_PARTY_NOTICES.md)。
