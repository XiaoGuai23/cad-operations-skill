"""Standards-compliant MCP JSON-RPC 2.0 protocol dispatcher."""

from __future__ import annotations

import json
from typing import Any

from .registry import (
    call_mcp_tool,
    get_mcp_prompt,
    list_mcp_prompts,
    list_mcp_resources,
    list_mcp_tools,
    read_mcp_resource,
)
from .sandbox import MCPWorkspaceSandbox

SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "cad-operations-skill-mcp", "version": "2.1.0"}


class MCPDispatcher:
    """Stateless/sandbox-bound JSON-RPC 2.0 dispatcher for MCP requests."""

    def __init__(self, sandbox: MCPWorkspaceSandbox | None = None) -> None:
        self.sandbox = sandbox or MCPWorkspaceSandbox()

    def handle_raw_json(self, raw_line: str) -> str | None:
        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            return json.dumps(
                self._error(None, -32700, f"Parse error: {exc}"),
                ensure_ascii=False,
            )
        response = self.handle_payload(parsed)
        if response is None:
            return None
        return json.dumps(response, ensure_ascii=False)

    def handle_payload(self, payload: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        if isinstance(payload, list):
            if not payload:
                return self._error(None, -32600, "Invalid Request: empty batch")
            responses = [
                resp
                for item in payload
                if (resp := self._handle_single(item)) is not None
            ]
            return responses if responses else None
        return self._handle_single(payload)

    def _handle_single(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            req_id = message.get("id") if isinstance(message, dict) else None
            return self._error(req_id, -32600, "Invalid Request: expected JSON-RPC 2.0 object")

        method = message.get("method")
        if not isinstance(method, str) or not method:
            return self._error(message.get("id"), -32600, "Invalid Request: missing method")

        is_notification = "id" not in message
        req_id = message.get("id")
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if is_notification:
                return None
            return self._error(req_id, -32602, "Invalid params: expected object")

        if is_notification or method.startswith("notifications/"):
            return None

        try:
            result = self._dispatch_method(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except KeyError as exc:
            code = -32601 if method not in {
                "tools/call",
                "resources/read",
                "prompts/get",
            } else -32602
            return self._error(req_id, code, str(exc))
        except (ValueError, TypeError) as exc:
            return self._error(req_id, -32602, f"Invalid params: {exc}")
        except Exception as exc:
            return self._error(req_id, -32603, f"Internal error: {type(exc).__name__}: {exc}")

    def _dispatch_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            requested_version = str(params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION))
            negotiated = (
                requested_version
                if requested_version in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION
            )
            return {
                "protocolVersion": negotiated,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                    "extensions": {
                        "io.modelcontextprotocol/ui": {
                            "mimeTypes": ["text/html;profile=mcp-app"]
                        }
                    },
                },
                "serverInfo": SERVER_INFO,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": list_mcp_tools()}
        if method == "tools/call":
            tool_name = params.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                raise ValueError("tools/call requires string 'name'")
            tool_args = params.get("arguments", {})
            if tool_args is not None and not isinstance(tool_args, dict):
                raise ValueError("tools/call 'arguments' must be an object")
            if tool_name not in {t["name"] for t in list_mcp_tools()}:
                raise KeyError(f"Unknown tool: {tool_name}")
            return call_mcp_tool(self.sandbox, tool_name, tool_args)
        if method == "resources/list":
            return {"resources": list_mcp_resources()}
        if method == "resources/templates/list":
            return {
                "resourceTemplates": [
                    {
                        "uriTemplate": "cad://tasks/{task_id}/audit",
                        "name": "Task Audit Report",
                        "description": "Audit JSON generated for a specific MCP task_id.",
                        "mimeType": "application/json",
                    }
                ]
            }
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise ValueError("resources/read requires string 'uri'")
            return read_mcp_resource(self.sandbox, uri)
        if method == "prompts/list":
            return {"prompts": list_mcp_prompts()}
        if method == "prompts/get":
            pname = params.get("name")
            if not isinstance(pname, str) or not pname:
                raise ValueError("prompts/get requires string 'name'")
            pargs = params.get("arguments", {})
            if pargs is not None and not isinstance(pargs, dict):
                raise ValueError("prompts/get 'arguments' must be an object")
            return get_mcp_prompt(pname, pargs)
        raise KeyError(f"Method not found: {method}")

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
