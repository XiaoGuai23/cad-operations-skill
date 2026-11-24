# CAD Operations Skill — MCP Server 与自动出图 Web 网关指南

本文档说明如何启动和接入 `cad_skill_core.mcp` 提供的标准 **MCP（Model Context Protocol）Server** 与 **自动出图网站 FastAPI HTTP 网关**。

---

## 1. 本地 AI 客户端接入（`stdio` 模式）

支持 Claude Desktop、Cursor、Codex、Antigravity 等所有兼容 MCP `2024-11-05` / `2025-03-26` JSON-RPC `stdio` 协议的客户端。

### `mcpServers` 配置示例

```json
{
  "mcpServers": {
    "cad-operations-skill": {
      "command": "D:\\Python312\\python.exe",
      "args": [
        "-B",
        "-m",
        "cad_skill_core.cli",
        "mcp-stdio",
        "--workspace",
        "D:\\CAD-Workspace\\cad-operations-skill\\output\\mcp_workspace"
      ],
      "env": {
        "PYTHONPATH": "D:\\CAD-Workspace\\cad-operations-skill\\runtime\\cad-2d-skill\\src"
      }
    }
  }
}
```

---

## 2. 自动出图网站与远程 Agent 接入（HTTP 模式）

启动命令：

```powershell
$env:PYTHONPATH="D:\CAD-Workspace\cad-operations-skill\runtime\cad-2d-skill\src"
D:\Python312\python.exe -B -m cad_skill_core.cli mcp-http --host 127.0.0.1 --port 8765
```

### 核心 HTTP 端点

| 端点 | 方法 | 用途 |
| :--- | :--- | :--- |
| `/` 或 `/studio` | `GET` | 内置中文 **CAD 智能自动出图工作台（Web Studio）**（支持二维预览 + 三维拖拽交互检视） |
| `/mcp` | `POST` | 标准 MCP Streamable HTTP JSON-RPC 2.0 接口 |
| `/api/v1/health` | `GET` | 服务状态、协议版本、已注册工具数、鉴权开启状态与沙箱路径 |
| `/api/v1/drivers` | `GET` | 本机 AutoCAD（2021–2026）与国产 CAD（中望/浩辰/CAXA/ODA）实时探测矩阵 |
| `/api/v1/standards` | `GET` | 列出内置 4 套多行业制图标准预设包（机械精加工 / 重工夹具 / 市政土建 / ISO 国际蓝图） |
| `/api/v1/tasks/compile` | `POST` | 从项目书 Markdown / 参数表编译 `DrawingSpec`（含齿轮/轴承透盖/带筋支座等 7 类零件族） |
| `/api/v1/tasks/generate` | `POST` | 同步生成二维/三维图纸（支持 `standard_profile` 标准切换并自动返回 `rubric_score`） |
| `/api/v1/tasks/revise` | `POST` | 基于图元 Handle 与 SHA-256 指纹执行原子局部改图 |
| `/api/v1/tasks/revise-nl` | `POST` | 基于蓝色虚线框选坐标（或图元 Handle）执行口语化自然语言直接改图并联动刷新画布 |
| `/api/v1/tasks/score` | `POST` | 对任务图纸或任意 DXF 执行 100 分制五维工程审图打分（兼容 `cad-1000-hours` `rubrics.json`） |
| `/api/v1/benchmark/cad1k` | `POST` | 运行免视频 `cad-1000-hours` 离线工程基准评测套件并返回成绩单 |
| `/api/v1/tasks/{task_id}` | `GET` | 查询任务状态、审计摘要与产物清单 |
| `/api/v1/tasks/{task_id}/artifacts/{filename}` | `GET` | 安全下载 `.dxf` / `.svg` / `.png` / `.pdf` / `.step` / `.stl` / `.obj` / `.glb` / `audit.json` |
| `/api/v1/jobs` | `POST` / `GET` | 提交或列出 **独立子进程隔离的异步出图任务**（`queued` → `running` → `completed`） |
| `/api/v1/jobs/{job_id}` | `GET` | 查询异步任务实时状态、耗时与结果 |
| `/api/v1/jobs/{job_id}/cancel` | `POST` | 安全取消排队中或运行中的异步任务 |

### 生产部署安全环境变量（可选）

- `CAD_API_KEY`：设置后自动对 `/mcp` 与 `/api/v1/*` 启用常量时间 `Bearer <token>` / `X-API-Key` 鉴权（未设置时保持本地开发免密模式）。
- `CAD_CORS_ORIGINS`：逗号分隔的允许跨域前端源列表（如 `http://localhost:3000`）。

---

## 3. 已暴露的 26 个 MCP Tools（二维 + 三维 + 统一改图核验 + 项目偏好记忆 + 非标特征组合 + 回归飞轮全闭环）

1. `cad_doctor`：诊断 Python 渲染栈、本机中文字体覆盖率、三维内核能力与多厂商 CAD 驱动状态。
2. `cad_compile_spec`：解析项目书/手册/CSV/DOCX/PDF，编译 `DrawingSpec`（自动融合已确认的项目制图偏好记忆）并拦截缺失尺寸与冲突参数。
3. `cad_validate_spec`：严格校验 `DrawingSpec` 结构、尺寸锚点与溯源完整性。
4. `cad_learn_standard`：从参考样图 DXF 自动提取图层、线宽、字高、`DIMSTYLE`、图幅标准以及制图表达习惯（孔阵列合并标法、引线方位、粗糙度习惯）。
5. `cad_generate_mechanical_2d`：生成国标/ISO 三视图零件与装配图（支持 `standard_profile` 标准预设切换、自动避让修复与实时 `rubric_score`）。
6. `cad_generate_electrical_sld`：生成单母/分段/双母电气主接线图（DXF/SVG/PDF/PNG + 交叉审计）。
7. `cad_generate_pavement`：生成道路路面结构设计图（原生 `HATCH` + `DIMENSION` 审计级 DXF + PNG）。
8. `cad_inventory_dxf`：列出 DXF 可编辑图元句柄（Handle）、坐标与当前 SHA-256。
9. `cad_revise_dxf`：执行带 `DocumentCheckpoint` 回滚保护的局部原子改图（支持 `dry_run`）。
10. `cad_revise_by_nl`：基于框选区域或图元句柄执行多子句自然语言直接改图，写盘后自动重读 DXF 逐项核对物理尺寸（`PostConditionVerifier`）。
11. `cad_preview_and_diff`：渲染局部放大 PNG 区域图或改图前后对比图。
12. `cad_generate_3d`：生成并独立回测水密流形三维模型（`model.step` AP214 / `.stl` / `.obj` / `.glb` / `model3d.dxf` / `iso.png` / `audit-3d.json`）。
13. `cad_project_3d_to_2d`：将三维规约 `Model3DSpec` 正交/剖视投影为多视图二维 `DrawingSpec`。
14. `cad_list_drivers`：只读探测本机安装的 AutoCAD（2021–2026）、中望 CAD（ZWCAD）、浩辰 CAD（GstarCAD）、CAXA 电子图板及 ODA 转换器矩阵。
15. `cad_verify_roundtrip`：核验 DWG ↔ DXF 双向往返前后的图层、块、布局、样式、图元数与总包围盒结构无损性。
16. `cad_compile_3d_spec`：将中英文口语化描述或项目书文档编译为三维 `Model3DSpec`。
17. `cad_open_viewer`：打开 MCP App 常驻交互式蓝图与三维审图台。
18. `cad_app_load_scene`：供 MCP App 画布内部加载二维矢量图元与三维棱线网格。
19. `cad_score_drawing`：执行 100 分制五维工程审图打分（评定 `S/A/B/C/D` 级，兼容 `cad-1000-hours` `rubrics.json`）。
20. `cad_run_cad1k_eval`：运行免视频 `cad-1000-hours` 离线工程基准评测套件。
21. `cad_audit_legacy_drawing`：对任意外部/历史存量 DXF 执行六维体检与确定性自动清洗（按 `$INSUNITS` 换算接缝容差并保留三维高程几何）。
22. `cad_apply_revision_ir`：执行统一结构化改图中间表示（`cad-revision-ir/v2.3`，覆盖 10 种几何增删改特征原语 + 事后重开 DXF 逐项物理核验）。
23. `cad_vector_diff`：计算改图前后句柄级与图块引用级红绿矢量叠加差异。
24. `cad_manage_preferences`：查询、口语教学、从样图提取、确认或停用项目级制图偏好记忆（`.cad-memory/project-preferences.json`）。
25. `cad_generate_feature_graph`：基于自由轮廓 + 可组合特征图谱（`cad-feature-graph/v1`：多边形/T 型板/圆角倒角/阵列孔/腰形槽/矩形方槽）生成非标异形件国标三视图。
26. `cad_manage_regressions`：将错题或审图修正案例（基线 DXF + 指令 + 物理验收断言）沉淀为永久回归测试集并一键回放核验。

## 4. MCP Resources 与 Prompts

- **Resources**：
  - `ui://cad-operations/viewer.html`
  - `cad://standards/gb-mechanical`
  - `cad://standards/presets`
  - `cad://schemas/drawing-spec`
  - `cad://schemas/model-3d-spec`
  - `cad://revision/operations`
  - `cad://tasks/{task_id}/audit`
- **Prompts**：
  - `draft-from-brief`
  - `safe-dxf-revision`
