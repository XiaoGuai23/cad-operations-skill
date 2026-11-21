"""Newline-delimited JSON-RPC 2.0 stdio transport for local MCP clients."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
from typing import TextIO

from .protocol import MCPDispatcher
from .sandbox import MCPWorkspaceSandbox


def serve_stdio(
    *,
    workspace_root: Path | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run MCP stdio server loop, keeping stdout strictly pure JSON-RPC frames."""

    in_stream = stdin if stdin is not None else io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    out_stream = stdout if stdout is not None else io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    err_stream = stderr if stderr is not None else sys.stderr

    sandbox = MCPWorkspaceSandbox(workspace_root)
    dispatcher = MCPDispatcher(sandbox)

    for raw_line in in_stream:
        stripped = raw_line.strip()
        if not stripped:
            continue
        with contextlib.redirect_stdout(err_stream):
            response_line = dispatcher.handle_raw_json(stripped)
        if response_line is not None:
            out_stream.write(response_line + "\n")
            out_stream.flush()
    return 0
