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
| `/api/v1/tasks/score` | `POST` | 对任务图纸或任意 DXF 执行 100 分制五维工程审图打分（兼容 `cad-1000-hours` `rubrics.json`） |
| `/api/v1/benchmark/cad1k` | `POST` | 运行免视频 `cad-1000-hours` 离线工程基准评测套件并返回成绩单 |
| `/api/v1/tasks/{task_id}` | `GET` | 查询任务状态、审计摘要与产物清单 |
| `/api/v1/tasks/{task_id}/artifacts/{filename}` | `GET` | 安全下载 `.dxf` / `.svg` / `.png` / `.pdf` / `.stl` / `.obj` / `.glb` / `audit.json` |
| `/api/v1/jobs` | `POST` / `GET` | 提交或列出 **独立子进程隔离的异步出图任务**（`queued` → `running` → `completed`） |
| `/api/v1/jobs/{job_id}` | `GET` | 查询异步任务实时状态、耗时与结果 |
| `/api/v1/jobs/{job_id}/cancel` | `POST` | 安全取消排队中或运行中的异步任务 |

### 生产部署安全环境变量（可选）

- `CAD_API_KEY`：设置后自动对 `/mcp` 与 `/api/v1/*` 启用常量时间 `Bearer <token>` / `X-API-Key` 鉴权（未设置时保持本地开发免密模式）。
- `CAD_CORS_ORIGINS`：逗号分隔的允许跨域前端源列表（如 `http://localhost:3000`）。

---

## 3. 已暴露的 19 个 MCP Tools（二维 + 三维 + 审图打分 + CAD-1K 评测 + 国产 CAD 驱动全闭环）

1. `cad_doctor`：诊断 Python 渲染栈、本机中文字体覆盖率、三维内核能力与多厂商 CAD 驱动状态。
2. `cad_compile_spec`：解析项目书/手册/CSV/DOCX/PDF，编译 `DrawingSpec` 并拦截缺失尺寸与冲突参数。
3. `cad_validate_spec`：严格校验 `DrawingSpec` 结构、尺寸锚点与溯源完整性。
4. `cad_learn_standard`：从参考样图 DXF 自动提取图层、线宽、字高、`DIMSTYLE` 与图幅标准。
5. `cad_generate_mechanical_2d`：生成国标/ISO 三视图零件与装配图（支持 `standard_profile` 标准预设切换、自动避让修复与实时 `rubric_score`）。
6. `cad_generate_electrical_sld`：生成单母/分段/双母电气主接线图（DXF/SVG/PDF/PNG + 交叉审计）。
7. `cad_generate_pavement`：生成道路路面结构设计图（原生 `HATCH` + `DIMENSION` 审计级 DXF + PNG）。
8. `cad_inventory_dxf`：列出 DXF 可编辑图元句柄（Handle）、坐标与当前 SHA-256。
9. `cad_revise_dxf`：执行带 `DocumentCheckpoint` 回滚保护的局部原子改图（支持 `dry_run`）。
10. `cad_preview_and_diff`：渲染局部放大 PNG 区域图或改图前后对比图。
11. `cad_generate_3d`：生成并独立回测水密流形三维模型（`.stl` / `.obj` / `.glb` / `model3d.dxf` / `iso.png` / `audit-3d.json`），支持 `with_2d_sheet=true` 联动生成二维工程图，以及 `run_native_cad=true` 调用本地 AutoCAD/中望/浩辰生成原生 `3DSOLID` `.dwg`。
12. `cad_project_3d_to_2d`：将三维规约 `Model3DSpec` 正交/剖视投影为多视图二维 `DrawingSpec`。
13. `cad_list_drivers`：只读探测本机安装的 AutoCAD（2021–2026）、中望 CAD（ZWCAD）、浩辰 CAD（GstarCAD）、CAXA 电子图板及 ODA 转换器矩阵。
14. `cad_verify_roundtrip`：核验 DWG ↔ DXF 双向往返前后的图层、块、布局、样式、图元数与总包围盒结构无损性。
15. `cad_compile_3d_spec`：将中英文口语化描述或项目书文档（`.md`/`.docx`/`.pdf`/`.csv`）编译为三维 `Model3DSpec`，并自动提示缺失或冲突参数。
16. `cad_open_viewer`：打开 MCP App 常驻交互式蓝图与三维审图台（支持蓝色虚线框选局部提问与改图）。
17. `cad_app_load_scene`：供 MCP App 画布内部加载二维矢量图元与三维棱线网格。
18. `cad_score_drawing`：执行 100 分制五维工程审图打分（评定 `S/A/B/C/D` 级，兼容 `cad-1000-hours` `rubrics.json`）。
19. `cad_run_cad1k_eval`：运行免视频 `cad-1000-hours` 离线工程基准评测套件（齿轮/轴承透盖/带筋支座/DXF 局部改图）。

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
