"""FastAPI Streamable HTTP MCP endpoint + Auto-Drafting Web Platform & Studio UI."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from cad_skill_core.cad_drivers import probe_all_cad_drivers

from .protocol import MCPDispatcher, SERVER_INFO, SUPPORTED_PROTOCOL_VERSIONS
from .registry import call_mcp_tool, list_mcp_tools
from .sandbox import MCPSandboxError, MCPWorkspaceSandbox
from .security import install_security_middlewares
from .studio_ui import render_studio_html
from .task_queue import AsyncCADJobManager

_MEDIA_TYPES = {
    ".dxf": "application/dxf",
    ".dwg": "application/acad",
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
    run_native_cad: bool = False
    driver_preference: Literal["auto", "autocad", "zwcad", "gstarcad"] = "auto"
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


def _map_generate_to_tool(req: WebGenerateRequest) -> tuple[str, dict[str, Any]]:
    if req.domain == "mechanical_2d":
        return (
            "cad_generate_mechanical_2d",
            {
                "spec": req.spec,
                "markdown_brief": req.markdown_brief,
                "max_repair_iterations": req.max_repair_iterations,
            },
        )
    if req.domain == "electrical_sld":
        if req.spec is None:
            raise HTTPException(status_code=400, detail="electrical_sld requires 'spec'")
        return ("cad_generate_electrical_sld", {"spec": req.spec})
    if req.domain == "model_3d":
        if req.spec is None:
            raise HTTPException(status_code=400, detail="model_3d requires 'spec'")
        return (
            "cad_generate_3d",
            {
                "spec": req.spec,
                "with_2d_sheet": req.with_2d_sheet,
                "run_native_cad": req.run_native_cad,
                "driver_preference": req.driver_preference,
            },
        )
    if req.spec is None:
        raise HTTPException(status_code=400, detail="pavement requires 'spec'")
    return ("cad_generate_pavement", {"spec": req.spec})


def create_mcp_http_app(
    workspace_root: Path | None = None,
    *,
    allowed_read_roots: Sequence[Path] | None = None,
    api_key: str | None = None,
    cors_origins: Sequence[str] | None = None,
    max_workers: int = 2,
    max_dxf_entities: int = 100_000,
) -> FastAPI:
    """Create FastAPI app serving `/studio`, `/mcp` JSON-RPC, `/api/v1/tasks/*`, and `/api/v1/jobs/*`."""

    sandbox = MCPWorkspaceSandbox(
        workspace_root,
        allowed_read_roots=allowed_read_roots,
        max_dxf_entities=max_dxf_entities,
    )
    dispatcher = MCPDispatcher(sandbox)
    job_manager = AsyncCADJobManager(sandbox, max_workers=max_workers)

    app = FastAPI(
        title="CAD Operations Skill MCP & Web Auto-Drafting Gateway",
        version=SERVER_INFO["version"],
    )
    app.state.sandbox = sandbox
    app.state.dispatcher = dispatcher
    app.state.job_manager = job_manager

    install_security_middlewares(app, api_key=api_key, cors_origins=cors_origins)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/studio", response_class=HTMLResponse)
    def web_studio() -> HTMLResponse:
        return HTMLResponse(content=render_studio_html())

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "server": SERVER_INFO,
            "supported_mcp_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "tool_count": len(list_mcp_tools()),
            "auth_required": bool(app.state.api_key_required),
            "studio_url": "/studio",
            "workspace_root": str(sandbox.workspace_root),
        }

    @app.get("/api/v1/drivers")
    def get_drivers() -> dict[str, Any]:
        return probe_all_cad_drivers()

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
        tool_name, tool_args = _map_generate_to_tool(req)
        res = call_mcp_tool(sandbox, tool_name, tool_args)
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

    # Phase 5 Subprocess-Isolated Async Job Endpoints
    @app.post("/api/v1/jobs", status_code=202)
    def submit_async_job(req: WebGenerateRequest) -> dict[str, Any]:
        tool_name, tool_args = _map_generate_to_tool(req)
        return job_manager.submit_job(
            tool_name=tool_name,
            arguments=tool_args,
            domain=req.domain,
        )

    @app.get("/api/v1/jobs")
    def list_async_jobs(limit: int = 50) -> dict[str, Any]:
        return {"jobs": job_manager.list_jobs(limit=limit)}

    @app.get("/api/v1/jobs/{job_id}")
    def get_async_job(job_id: str) -> dict[str, Any]:
        try:
            return job_manager.get_job(job_id)
        except MCPSandboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_async_job(job_id: str) -> dict[str, Any]:
        try:
            return job_manager.cancel_job(job_id)
        except MCPSandboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
