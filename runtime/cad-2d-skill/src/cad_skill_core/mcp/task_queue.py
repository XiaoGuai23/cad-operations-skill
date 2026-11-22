"""Subprocess-isolated concurrent async job queue for the Web Auto-Drafting Platform."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Literal

from .sandbox import MCPSandboxError, MCPWorkspaceSandbox

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _attach_urls(payload: dict[str, Any]) -> dict[str, Any]:
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


class AsyncCADJobManager:
    """Executes CAD generation/revision jobs in isolated Python worker subprocesses."""

    def __init__(
        self,
        sandbox: MCPWorkspaceSandbox,
        *,
        max_workers: int = 2,
        job_timeout_seconds: float = 180.0,
    ) -> None:
        self.sandbox = sandbox
        self.jobs_root = sandbox.workspace_root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.max_workers = max(1, int(max_workers))
        self.job_timeout_seconds = float(job_timeout_seconds)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="cad-job")
        self._futures: dict[str, Future[None]] = {}
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._cancelled: set[str] = set()

    def _job_file(self, job_id: str) -> Path:
        if not job_id or any(ch in job_id for ch in ("/", "\\", "..")):
            raise MCPSandboxError(f"invalid job_id: {job_id!r}")
        p = (self.jobs_root / f"{job_id}.json").resolve()
        self.sandbox._assert_within(p, self.jobs_root)
        return p

    def _write_record(self, job_id: str, record: dict[str, Any]) -> dict[str, Any]:
        target = self._job_file(job_id)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return record

    def _read_record(self, job_id: str) -> dict[str, Any]:
        target = self._job_file(job_id)
        if not target.is_file():
            raise MCPSandboxError(f"unknown job_id: {job_id}")
        return json.loads(target.read_text(encoding="utf-8"))

    def submit_job(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        domain: str = "custom",
    ) -> dict[str, Any]:
        stamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
        job_id = f"job-{stamp}-{uuid.uuid4().hex[:6]}"
        record: dict[str, Any] = {
            "job_id": job_id,
            "domain": domain,
            "tool_name": tool_name,
            "status": "queued",
            "created_at": _utc_now(),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "task_id": None,
            "result": None,
            "error": None,
            "status_url": f"/api/v1/jobs/{job_id}",
        }
        with self._lock:
            self._write_record(job_id, record)
            fut = self._executor.submit(self._run_worker_subprocess, job_id, tool_name, arguments)
            self._futures[job_id] = fut
        return record

    def _run_worker_subprocess(
        self,
        job_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        with self._lock:
            if job_id in self._cancelled:
                return
            rec = self._read_record(job_id)
            rec["status"] = "running"
            rec["started_at"] = _utc_now()
            self._write_record(job_id, rec)

        started = time.monotonic()
        src_root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = src_root + os.pathsep + env.get("PYTHONPATH", "")

        worker_payload = json.dumps(
            {
                "workspace_root": str(self.sandbox.workspace_root),
                "allowed_read_roots": [str(r) for r in self.sandbox.allowed_read_roots],
                "tool_name": tool_name,
                "arguments": arguments,
            },
            ensure_ascii=False,
        )

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "cad_skill_core.mcp.task_queue", "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            creationflags=creationflags,
        )
        with self._lock:
            self._procs[job_id] = proc
            if job_id in self._cancelled:
                proc.kill()

        try:
            stdout, stderr = proc.communicate(input=worker_payload, timeout=self.job_timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            with self._lock:
                self._procs.pop(job_id, None)
                rec = self._read_record(job_id)
                rec["status"] = "failed"
                rec["finished_at"] = _utc_now()
                rec["duration_seconds"] = round(time.monotonic() - started, 3)
                rec["error"] = f"Job timed out after {self.job_timeout_seconds}s"
                self._write_record(job_id, rec)
            return

        with self._lock:
            self._procs.pop(job_id, None)
            if job_id in self._cancelled:
                rec = self._read_record(job_id)
                rec["status"] = "cancelled"
                rec["finished_at"] = _utc_now()
                rec["duration_seconds"] = round(time.monotonic() - started, 3)
                self._write_record(job_id, rec)
                return

            rec = self._read_record(job_id)
            rec["finished_at"] = _utc_now()
            rec["duration_seconds"] = round(time.monotonic() - started, 3)

            if proc.returncode != 0:
                rec["status"] = "failed"
                rec["error"] = (stderr or stdout or f"Worker exited {proc.returncode}").strip()[:1000]
                self._write_record(job_id, rec)
                return

            try:
                tool_res = json.loads(stdout.strip().splitlines()[-1])
            except Exception as exc:
                rec["status"] = "failed"
                rec["error"] = f"Malformed worker response: {exc}"
                self._write_record(job_id, rec)
                return

            structured = _attach_urls(tool_res.get("structuredContent") or {})
            rec["result"] = structured
            rec["task_id"] = structured.get("task_id")
            if tool_res.get("isError"):
                rec["status"] = "failed"
                rec["error"] = structured.get("error") or f"Tool {tool_name} reported failure"
            else:
                rec["status"] = "completed"
            self._write_record(job_id, rec)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read_record(job_id)

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 200))
        files = sorted(self.jobs_root.glob("job-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        items: list[dict[str, Any]] = []
        for f in files[:capped]:
            try:
                items.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return items

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            rec = self._read_record(job_id)
            if rec["status"] in {"completed", "failed", "cancelled"}:
                return rec
            self._cancelled.add(job_id)
            fut = self._futures.get(job_id)
            if fut is not None:
                fut.cancel()
            proc = self._procs.get(job_id)
            if proc is not None:
                try:
                    proc.kill()
                except OSError:
                    pass
            rec["status"] = "cancelled"
            rec["finished_at"] = _utc_now()
            return self._write_record(job_id, rec)

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


def _worker_main() -> int:
    from .registry import call_mcp_tool

    raw = sys.stdin.read()
    req = json.loads(raw)
    sb = MCPWorkspaceSandbox(
        Path(req["workspace_root"]),
        allowed_read_roots=[Path(p) for p in req.get("allowed_read_roots", [])],
    )
    res = call_mcp_tool(sb, req["tool_name"], req.get("arguments") or {})
    # Omit base64 image blobs in IPC output; artifacts are already persisted in task_dir
    out = {
        "isError": bool(res.get("isError")),
        "structuredContent": res.get("structuredContent") or {},
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if "--worker" in sys.argv:
        raise SystemExit(_worker_main())
