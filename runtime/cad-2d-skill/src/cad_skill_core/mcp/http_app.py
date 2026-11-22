"""FastAPI Streamable HTTP MCP endpoint + Auto-Drafting Web Platform REST API."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .protocol import MCPDispatcher, SERVER_INFO, SUPPORTED_PROTOCOL_VERSIONS
from .registry import call_mcp_tool, list_mcp_tools
from .sandbox import MCPSandboxError, MCPWorkspaceSandbox

_MEDIA_TYPES = {
    ".dxf": "application/dxf",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".pdf": "application/pdf",
    ".stl": "model/stl",
    ".obj": "model/obj",
    ".glb": "model/gltf-binary",
    ".scr": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".md": "text/markdown; charset=utf-8",
    ".yaml": "application/x-yaml",
}


class WebCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown_text: str | None = None
    input_paths: list[str] = Field(default_factory=list)
    part_family: Literal["auto", "flange", "stepped-shaft", "slotted-bracket", "rounded-rect"] = "auto"
    language: Literal["auto", "en", "zh"] = "auto"


class WebGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["mechanical_2d", "electrical_sld", "pavement", "model_3d"] = "mechanical_2d"
    spec: dict[str, Any] | None = None
    markdown_brief: str | None = None
    with_2d_sheet: bool = False
    max_repair_iterations: int = Field(default=3, ge=0, le=3)


class WebReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    dxf_path: str | None = None
    artifact_name: str = "drawing.dxf"
    plan: dict[str, Any]
    dry_run: bool = False


def _attach_download_urls(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = payload.get("task_id")
    if not task_id or "artifacts" not in payload:
        return payload
    enriched = dict(payload)
    enriched["artifacts"] = [
        {
            **art,
            "download_url": f"/api/v1/tasks/{task_id}/artifacts/{art['name']}",
        }
        for art in payload.get("artifacts", [])
    ]
    return enriched


def create_mcp_http_app(
    workspace_root: Path | None = None,
    *,
    allowed_read_roots: Sequence[Path] | None = None,
) -> FastAPI:
    """Create FastAPI app serving both `/mcp` JSON-RPC and `/api/v1/tasks/*` REST endpoints."""

    sandbox = MCPWorkspaceSandbox(workspace_root, allowed_read_roots=allowed_read_roots)
    dispatcher = MCPDispatcher(sandbox)

    app = FastAPI(
        title="CAD Operations Skill MCP & Web Drafting Gateway",
        version=SERVER_INFO["version"],
    )
    app.state.sandbox = sandbox
    app.state.dispatcher = dispatcher

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "server": SERVER_INFO,
            "supported_mcp_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "tool_count": len(list_mcp_tools()),
            "workspace_root": str(sandbox.workspace_root),
        }

    @app.post("/mcp")
    async def mcp_endpoint(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception as exc:
            err = MCPDispatcher._error(None, -32700, f"Parse error: {exc}")
            return JSONResponse(status_code=400, content=err)

        result = dispatcher.handle_payload(body)
        if result is None:
            return Response(status_code=202)
        return JSONResponse(status_code=200, content=result)

    @app.post("/api/v1/tasks/compile")
    def web_compile(req: WebCompileRequest) -> JSONResponse:
        res = call_mcp_tool(sandbox, "cad_compile_spec", req.model_dump())
        status_code = 400 if res["isError"] else 200
        return JSONResponse(
            status_code=status_code,
            content=_attach_download_urls(res["structuredContent"]),
        )

    @app.post("/api/v1/tasks/generate")
    def web_generate(req: WebGenerateRequest) -> JSONResponse:
        if req.domain == "mechanical_2d":
            res = call_mcp_tool(
                sandbox,
                "cad_generate_mechanical_2d",
                {
                    "spec": req.spec,
                    "markdown_brief": req.markdown_brief,
                    "max_repair_iterations": req.max_repair_iterations,
                },
            )
        elif req.domain == "electrical_sld":
            if req.spec is None:
                raise HTTPException(status_code=400, detail="electrical_sld requires 'spec'")
            res = call_mcp_tool(sandbox, "cad_generate_electrical_sld", {"spec": req.spec})
        elif req.domain == "model_3d":
            if req.spec is None:
                raise HTTPException(status_code=400, detail="model_3d requires 'spec'")
            res = call_mcp_tool(
                sandbox,
                "cad_generate_3d",
                {"spec": req.spec, "with_2d_sheet": req.with_2d_sheet},
            )
        else:
            if req.spec is None:
                raise HTTPException(status_code=400, detail="pavement requires 'spec'")
            res = call_mcp_tool(sandbox, "cad_generate_pavement", {"spec": req.spec})

        structured = _attach_download_urls(res["structuredContent"])
        status_code = 422 if res["isError"] else 200
        return JSONResponse(status_code=status_code, content=structured)

    @app.post("/api/v1/tasks/revise")
    def web_revise(req: WebReviseRequest) -> JSONResponse:
        res = call_mcp_tool(sandbox, "cad_revise_dxf", req.model_dump())
        structured = _attach_download_urls(res["structuredContent"])
        status_code = 400 if res["isError"] else 200
        return JSONResponse(status_code=status_code, content=structured)

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        try:
            meta = sandbox.read_task_metadata(task_id)
        except MCPSandboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _attach_download_urls(meta)

    @app.get("/api/v1/tasks/{task_id}/artifacts/{filename}")
    def download_artifact(task_id: str, filename: str) -> FileResponse:
        try:
            artifact_path = sandbox.resolve_task_artifact(task_id, filename)
        except MCPSandboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        media_type = _MEDIA_TYPES.get(artifact_path.suffix.lower(), "application/octet-stream")
        return FileResponse(path=artifact_path, filename=artifact_path.name, media_type=media_type)

    return app
