"""Web/MCP security middleware: constant-time API key auth, CORS, and anti-DXF-bomb guards."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .sandbox import MCPSandboxError

DEFAULT_MAX_DXF_ENTITIES = 100_000
DEFAULT_MAX_ABS_COORD = 1.0e8


class DXFBombError(MCPSandboxError):
    """Raised when an input DXF exceeds entity-count or coordinate magnitude safety bounds."""


def resolve_configured_api_key(explicit_api_key: str | None = None) -> str | None:
    raw = explicit_api_key if explicit_api_key is not None else os.environ.get("CAD_API_KEY")
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


def check_request_api_key(request: Request, expected_key: str | None) -> bool:
    """Constant-time check of Authorization: Bearer <key> or X-API-Key header."""

    if not expected_key:
        return True
    candidate = ""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        candidate = auth_header[7:].strip()
    elif "x-api-key" in request.headers:
        candidate = request.headers.get("x-api-key", "").strip()
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected_key.encode("utf-8"))


def install_security_middlewares(
    app: FastAPI,
    *,
    api_key: str | None = None,
    cors_origins: Sequence[str] | None = None,
) -> None:
    """Attach optional CORS policy and constant-time API key guard to FastAPI app."""

    env_origins_raw = os.environ.get("CAD_CORS_ORIGINS", "").strip()
    resolved_origins = (
        list(cors_origins)
        if cors_origins is not None
        else ([o.strip() for o in env_origins_raw.split(",") if o.strip()] if env_origins_raw else [])
    )
    if resolved_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        )

    expected_key = resolve_configured_api_key(api_key)
    app.state.api_key_required = expected_key is not None

    @app.middleware("http")
    async def _api_key_guard(request: Request, call_next):
        path = request.url.path
        # Public endpoints: health probe and Web Studio HTML shell
        if request.method == "OPTIONS" or path in {"/", "/studio", "/api/v1/health"}:
            return await call_next(request)
        if not check_request_api_key(request, expected_key):
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "error": "unauthorized: valid Bearer token or X-API-Key required",
                },
            )
        return await call_next(request)


def inspect_dxf_safety_limits(
    dxf_path: Path,
    *,
    max_entities: int = DEFAULT_MAX_DXF_ENTITIES,
    max_abs_coord: float = DEFAULT_MAX_ABS_COORD,
) -> dict[str, int | float]:
    """Pre-scan an ASCII/UTF-8 DXF for excessive entity count or astronomical coordinates."""

    resolved = Path(dxf_path).resolve(strict=True)
    entity_markers = 0
    max_seen_coord = 0.0
    in_entities_or_blocks = False

    # Fast line-pair scan before full DOM materialization
    with resolved.open("r", encoding="utf-8", errors="replace") as stream:
        prev_was_section_name_code = False
        while True:
            code_line = stream.readline()
            if not code_line:
                break
            val_line = stream.readline()
            if not val_line:
                break
            code = code_line.strip()
            val = val_line.strip()

            if code == "2" and prev_was_section_name_code:
                in_entities_or_blocks = val.upper() in {"ENTITIES", "BLOCKS"}
                prev_was_section_name_code = False
                continue
            prev_was_section_name_code = False

            if code == "0":
                upper_val = val.upper()
                if upper_val == "SECTION":
                    prev_was_section_name_code = True
                elif upper_val == "ENDSEC":
                    in_entities_or_blocks = False
                elif in_entities_or_blocks and upper_val not in {"BLOCK", "ENDBLK", "VERTEX", "SEQEND"}:
                    entity_markers += 1
                    if entity_markers > max_entities:
                        raise DXFBombError(
                            f"DXF safety guard blocked {resolved.name}: entity count exceeds limit ({max_entities})"
                        )
            elif in_entities_or_blocks and code in {"10", "20", "30", "11", "21", "31", "40"}:
                try:
                    num = abs(float(val))
                except ValueError:
                    continue
                if num > max_abs_coord:
                    raise DXFBombError(
                        f"DXF safety guard blocked {resolved.name}: coordinate/radius magnitude {num:.3e} exceeds {max_abs_coord:.1e}"
                    )
                if num > max_seen_coord:
                    max_seen_coord = num

    return {"entity_markers": entity_markers, "max_abs_coord": max_seen_coord}
