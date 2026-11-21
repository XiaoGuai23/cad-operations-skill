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
| `/mcp` | `POST` | 标准 MCP Streamable HTTP JSON-RPC 2.0 接口 |
| `/api/v1/health` | `GET` | 服务状态、协议版本、已注册工具数与沙箱路径 |
| `/api/v1/tasks/compile` | `POST` | 从项目书 Markdown / 参数表编译 `DrawingSpec` 并返回缺失与冲突项 |
| `/api/v1/tasks/generate` | `POST` | 生成二维图纸（`mechanical_2d` / `electrical_sld` / `pavement`） |
| `/api/v1/tasks/revise` | `POST` | 基于图元 Handle 与 SHA-256 指纹执行原子局部改图 |
| `/api/v1/tasks/{task_id}` | `GET` | 查询任务状态、审计摘要与产物清单 |
| `/api/v1/tasks/{task_id}/artifacts/{filename}` | `GET` | 安全下载 `.dxf` / `.svg` / `.png` / `.pdf` / `audit.json` |

---

## 3. 已暴露的 10 个 MCP Tools（v1 二维生产集）

1. `cad_doctor`：诊断 Python 渲染栈、本机中文字体覆盖率与本地 AutoCAD 状态。
2. `cad_compile_spec`：解析项目书/手册/CSV/DOCX/PDF，编译 `DrawingSpec` 并拦截缺失尺寸与冲突参数。
3. `cad_validate_spec`：严格校验 `DrawingSpec` 结构、尺寸锚点与溯源完整性。
4. `cad_learn_standard`：从参考样图 DXF 自动提取图层、线宽、字高、`DIMSTYLE` 与图幅标准。
5. `cad_generate_mechanical_2d`：生成国标零件/装配图（含自动避让修复、中文图框与 `ImageContent` 预览回传）。
6. `cad_generate_electrical_sld`：生成单母/分段/双母电气主接线图（DXF/SVG/PDF/PNG + 交叉审计）。
7. `cad_generate_pavement`：生成道路路面结构设计图（原生 `HATCH` + `DIMENSION` 审计级 DXF + PNG）。
8. `cad_inventory_dxf`：列出 DXF 可编辑图元句柄（Handle）、坐标与当前 SHA-256。
9. `cad_revise_dxf`：执行带 `DocumentCheckpoint` 回滚保护的局部原子改图（支持 `dry_run`）。
10. `cad_preview_and_diff`：渲染局部放大 PNG 区域图或改图前后对比图。

## 4. MCP Resources 与 Prompts

- **Resources**：
  - `cad://standards/gb-mechanical`
  - `cad://schemas/drawing-spec`
  - `cad://revision/operations`
  - `cad://tasks/{task_id}/audit`
- **Prompts**：
  - `draft-from-brief`
  - `safe-dxf-revision`
