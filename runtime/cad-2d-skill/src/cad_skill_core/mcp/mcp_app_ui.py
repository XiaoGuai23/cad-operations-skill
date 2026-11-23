"""Self-contained MCP App HTML UI (ui://cad-operations/viewer.html).

Complies with SEP-1865 / Antigravity MCP Apps specification:
- Delivered with mimeType 'text/html;profile=mcp-app'
- Zero external network dependencies (100% compliant with sandbox CSP default-src 'none')
- Implements JSON-RPC 2.0 postMessage bridge (ui/initialize, tool-result notifications,
  app-only cad_app_load_scene calls, ui/update-model-context, and ui/message)
- Features white-paper GB/T blueprint mode + blue dashed marquee region selection +
  floating rounded prompt pill ('圈哪问哪 / 圈哪改图') + 3D crease-edge viewer.
"""

from __future__ import annotations

MCP_APP_RESOURCE_URI = "ui://cad-operations/viewer.html"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"


def get_mcp_app_viewer_html() -> str:
    """Return the single-file sandboxed MCP App HTML document."""
    return _MCP_APP_HTML


_MCP_APP_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CAD 交互审图台 · MCP App</title>
  <style>
    :root {
      --bg: #f1f5f9;
      --bar-bg: #ffffff;
      --border: #e2e8f0;
      --text: #0f172a;
      --muted: #64748b;
      --blue: #1668dc;
      --blue-fill: rgba(22, 104, 220, 0.09);
      --pill-bg: #ffffff;
      --pill-shadow: 0 10px 30px rgba(15, 23, 42, 0.15), 0 2px 6px rgba(15, 23, 42, 0.06);
    }
    body.dark-ui {
      --bg: #0b1120;
      --bar-bg: #111827;
      --border: #1e293b;
      --text: #f1f5f9;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      width: 100%;
      height: 100%;
      min-height: 460px;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, "Microsoft YaHei", "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
    }
    .topbar {
      height: 42px;
      background: var(--bar-bg);
      border-bottom: 1px solid var(--border);
      padding: 0 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      flex-shrink: 0;
      z-index: 10;
    }
    .row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .title-chip {
      font-size: 12.5px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .badge {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--border);
      color: var(--muted);
      background: rgba(148, 163, 184, 0.1);
    }
    .badge.ok { color: #059669; border-color: rgba(5, 150, 105, 0.35); background: rgba(16, 185, 129, 0.1); }
    button.tool-btn {
      font-family: inherit;
      font-size: 11.5px;
      padding: 4px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bar-bg);
      color: var(--text);
      cursor: pointer;
      transition: all 0.14s;
    }
    button.tool-btn:hover { border-color: var(--blue); color: var(--blue); }
    button.tool-btn.active {
      background: rgba(22, 104, 220, 0.11);
      border-color: var(--blue);
      color: var(--blue);
      font-weight: 600;
    }
    .stage {
      flex: 1;
      position: relative;
      display: grid;
      grid-template-columns: 1fr;
      min-height: 0;
    }
    .stage.split { grid-template-columns: 1.35fr 1fr; }
    .vp {
      position: relative;
      width: 100%;
      height: 100%;
      overflow: hidden;
      border-right: 1px solid var(--border);
    }
    canvas { width: 100%; height: 100%; display: block; }
    canvas.marquee-cursor { cursor: crosshair; }
    canvas.pan-cursor { cursor: grab; }
    canvas.pan-cursor:active { cursor: grabbing; }

    /* Image 1 Style Floating White Rounded Question / Revision Pill */
    .prompt-pill {
      position: absolute;
      display: none;
      align-items: center;
      gap: 8px;
      min-width: 310px;
      max-width: min(460px, calc(100% - 24px));
      height: 50px;
      padding: 0 8px 0 18px;
      background: var(--pill-bg);
      border-radius: 999px;
      box-shadow: var(--pill-shadow);
      border: 1px solid rgba(15, 23, 42, 0.08);
      z-index: 25;
    }
    .prompt-pill input {
      flex: 1;
      border: none;
      outline: none;
      background: transparent;
      font-family: inherit;
      font-size: 14px;
      color: #1e293b;
      min-width: 140px;
    }
    .prompt-pill input::placeholder { color: #64748b; }
    .prompt-pill .hit-tag {
      font-size: 11px;
      color: #2563eb;
      background: rgba(37, 99, 235, 0.09);
      padding: 2px 7px;
      border-radius: 999px;
      white-space: nowrap;
    }
    .pill-submit {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      border: none;
      background: #18181b;
      color: #ffffff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      flex-shrink: 0;
      transition: transform 0.12s, background 0.12s;
    }
    .pill-submit:hover { background: #1668dc; transform: scale(1.05); }
    .pill-cancel {
      width: 24px;
      height: 24px;
      border-radius: 50%;
      border: none;
      background: transparent;
      color: #94a3b8;
      cursor: pointer;
      font-size: 13px;
      flex-shrink: 0;
    }
    .pill-cancel:hover { color: #ef4444; }

    .footbar {
      height: 28px;
      background: var(--bar-bg);
      border-top: 1px solid var(--border);
      padding: 0 12px;
      font-size: 11px;
      color: var(--muted);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
  </style>
</head>
<body>
  <div class="topbar">
    <div class="row">
      <span class="title-chip">📐 CAD 交互审图台</span>
      <span id="statusBadge" class="badge">等待图纸载入...</span>
    </div>
    <div class="row">
      <button id="btnMarquee" class="tool-btn active" title="在图纸上拉出蓝色虚线框，直接针对框选局部提问或改图">🟦 框选提问/改图</button>
      <button id="btnPanMode" class="tool-btn" title="拖拽平移图纸（滚轮随时缩放）">✋ 平移视图</button>
      <button id="btnFit" class="tool-btn">⛶ 适应图纸</button>
      <button id="btnPaperTheme" class="tool-btn">☀️ 白底蓝图</button>
    </div>
  </div>

  <div class="stage" id="stage">
    <div class="vp" id="vp2d">
      <canvas id="cv2d" class="marquee-cursor"></canvas>
      <!-- Floating Rounded Input Pill (matches Reference Image 1) -->
      <div class="prompt-pill" id="promptPill">
        <input id="pillInput" type="text" placeholder="问题一" autocomplete="off" />
        <span class="hit-tag" id="pillHitCount">0 图元</span>
        <button class="pill-cancel" id="pillDelete" style="display:none;color:#dc2626;" title="删除该条批注">🗑️</button>
        <button class="pill-cancel" id="pillCancel" title="取消选框">✕</button>
        <button class="pill-submit" id="pillSubmit" title="发送框选区域与指令给 AI（支持二次修改）">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </button>
      </div>
    </div>
    <div class="vp" id="vp3d" style="display:none; border-right:none;">
      <canvas id="cv3d"></canvas>
    </div>
  </div>

  <div class="footbar">
    <span id="coordInfo">提示：直接拖拽蓝色虚线框圈出图纸任意局部，在弹出胶囊中输入问题或改图要求</span>
    <span id="statsInfo">滚轮缩放 · 按住空格或切“平移”拖动</span>
  </div>

  <script>
    // ===================== MCP App JSON-RPC 2.0 PostMessage Bridge =====================
    let rpcNextId = 1;
    const pendingRpc = new Map();
    let mcpInitialized = false;

    function sendRpcRequest(method, params) {
      return new Promise((resolve, reject) => {
        const id = rpcNextId++;
        pendingRpc.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        setTimeout(() => {
          if (pendingRpc.has(id)) {
            pendingRpc.delete(id);
            reject(new Error("RPC timeout: " + method));
          }
        }, 15000);
      });
    }

    function sendRpcNotification(method, params) {
      window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
    }

    window.addEventListener("message", async (ev) => {
      const msg = ev.data;
      if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") return;

      if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
        const waiter = pendingRpc.get(msg.id);
        if (waiter) {
          pendingRpc.delete(msg.id);
          if (msg.error) waiter.reject(msg.error);
          else waiter.resolve(msg.result);
        }
        return;
      }

      const method = msg.method || "";
      const params = msg.params || {};
      if (method === "ui/notifications/host-context-changed") {
        applyHostContext(params);
      } else if (method === "ui/notifications/tool-result") {
        await handleToolResult(params);
      } else if (method === "ui/notifications/tool-input") {
        if (params.arguments && (params.arguments.task_id || params.arguments.dxf_path)) {
          await loadSceneFromServer(params.arguments.task_id, params.arguments.dxf_path);
        }
      }
    });

    function applyHostContext(ctx) {
      if (!ctx) return;
      if (ctx.theme === "dark") document.body.classList.add("dark-ui");
      else if (ctx.theme === "light") document.body.classList.remove("dark-ui");
    }

    async function initMcpBridge() {
      try {
        const res = await sendRpcRequest("ui/initialize", {
          protocolVersion: "2026-01-26",
          appInfo: { name: "cad-operations-viewer", version: "2.1.0" },
          appCapabilities: {}
        });
        mcpInitialized = true;
        if (res && res.hostContext) applyHostContext(res.hostContext);
        sendRpcNotification("ui/notifications/initialized", {});
      } catch (_) {
        // Standalone preview fallback
      }
    }

    // ===================== Scene & Marquee Annotation State =====================
    let currentTaskId = null;
    let currentDxfPath = null;
    let cadScene = null;
    let mesh3d = null;
    let paperBlueprintMode = true; // Default: clean white GB/T blueprint like Reference Image 1!
    let interactionMode = "marquee"; // "marquee" | "pan"
    let spacePressed = false;

    let camScale = 1.8, camPanX = 40, camPanY = 380;
    let isPanning = false, panOriginX = 0, panOriginY = 0;

    // Active blue dashed marquee selection & saved numbered pins
    let isSelecting = false;
    let selStartScreen = null;
    let activeSelection = null; // { sx0, sy0, sx1, sy1, wx0, wy0, wx1, wy1, handles: [], types: [] }
    let savedPins = []; // [{ idx, wx0, wy0, wx1, wy1, text, handles }]

    // 3D state
    let rotX = -0.58, rotY = 0.68, zoom3d = 1.0, drag3D = false, last3DX = 0, last3DY = 0;

    async function handleToolResult(resultPayload) {
      const sc = resultPayload?.structuredContent || resultPayload;
      if (!sc || typeof sc !== "object") return;
      const tid = sc.task_id || sc.sheet_2d?.task_id || null;
      const dpath = sc.output_dxf || sc.dxf_path || null;
      if (tid || dpath) {
        await loadSceneFromServer(tid, dpath);
      }
    }

    async function loadSceneFromServer(taskId, dxfPath) {
      try {
        const badge = document.getElementById("statusBadge");
        badge.textContent = "正在加载矢量图纸...";
        const res = await sendRpcRequest("tools/call", {
          name: "cad_app_load_scene",
          arguments: { task_id: taskId || null, dxf_path: dxfPath || null }
        });
        const data = res?.structuredContent;
        if (!data) return;
        currentTaskId = data.task_id || taskId;
        currentDxfPath = data.dxf_path || dxfPath;
        savedPins = [];
        closePromptPill();

        if (data.cad_scene) {
          cadScene = data.cad_scene;
          document.getElementById("vp2d").style.display = "block";
        }
        const stage = document.getElementById("stage");
        const vp3d = document.getElementById("vp3d");
        if (data.obj_text) {
          mesh3d = parseOBJ(data.obj_text);
          vp3d.style.display = "block";
          if (cadScene) stage.classList.add("split");
          else {
            document.getElementById("vp2d").style.display = "none";
            stage.classList.remove("split");
          }
        } else {
          mesh3d = null;
          vp3d.style.display = "none";
          stage.classList.remove("split");
        }
        badge.className = "badge ok";
        badge.textContent = `${currentTaskId || "已载入"} · ${cadScene ? cadScene.primitive_count + " 图元" : "3D 实体"}`;
        resizeAll();
        fitCamera();
      } catch (err) {
        document.getElementById("statusBadge").textContent = "图纸载入失败";
      }
    }

    // ===================== 2D Blueprint Vector Renderer + Blue Dashed Marquee =====================
    function resizeAll() {
      const dpr = window.devicePixelRatio || 1;
      for (const id of ["cv2d", "cv3d"]) {
        const c = document.getElementById(id);
        if (!c || c.parentElement.style.display === "none") continue;
        const r = c.parentElement.getBoundingClientRect();
        c.width = Math.max(1, Math.floor(r.width * dpr));
        c.height = Math.max(1, Math.floor(r.height * dpr));
      }
      draw2D();
      draw3D();
    }
    window.addEventListener("resize", resizeAll);

    function fitCamera() {
      if (!cadScene) { draw2D(); return; }
      const c = document.getElementById("cv2d");
      const dpr = window.devicePixelRatio || 1;
      const W = c.width / dpr, H = c.height / dpr;
      const [x0, y0, x1, y1] = cadScene.bounds_mm;
      const bw = Math.max(x1 - x0, 1), bh = Math.max(y1 - y0, 1);
      camScale = Math.min((W - 48) / bw, (H - 48) / bh);
      camPanX = (W - bw * camScale) / 2 - x0 * camScale;
      camPanY = H - (H - bh * camScale) / 2 + y0 * camScale;
      draw2D();
    }

    function worldToScreen(wx, wy) {
      return [camPanX + wx * camScale, camPanY - wy * camScale];
    }
    function screenToWorld(sx, sy) {
      return [(sx - camPanX) / camScale, (camPanY - sy) / camScale];
    }

    function drawBlueSpeechBadge(ctx, x, y, labelText) {
      const r = 13;
      ctx.save();
      ctx.fillStyle = "#0d6efd";
      ctx.shadowColor = "rgba(13, 110, 253, 0.32)";
      ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      // Speech droplet tail at bottom-left (matches Reference Image 1!)
      ctx.beginPath();
      ctx.moveTo(x - r, y);
      ctx.lineTo(x - r, y + r);
      ctx.lineTo(x, y + r);
      ctx.closePath();
      ctx.fill();
      ctx.shadowBlur = 0;
      if (labelText) {
        ctx.fillStyle = "#ffffff";
        ctx.font = "bold 11px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(String(labelText), x, y + 0.5);
      }
      ctx.restore();
    }

    function draw2D() {
      const c = document.getElementById("cv2d");
      if (!c || c.parentElement.style.display === "none") return;
      const ctx = c.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const W = c.width / dpr, H = c.height / dpr;
      ctx.save();
      ctx.scale(dpr, dpr);

      ctx.fillStyle = paperBlueprintMode ? "#eef2f6" : "#070d19";
      ctx.fillRect(0, 0, W, H);

      if (!cadScene) {
        ctx.fillStyle = "#64748b";
        ctx.font = "13px 'Microsoft YaHei', sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("生成或打开 CAD 图纸后，可在此直接拉出蓝色虚线框提问或改图", W / 2, H / 2);
        ctx.restore();
        return;
      }

      // Draw crisp white engineering paper sheet card in blueprint mode
      const [bx0, by0, bx1, by1] = cadScene.bounds_mm;
      if (paperBlueprintMode) {
        const [sx0, sy1] = worldToScreen(bx0 - 8, by0 - 8);
        const [sx1, sy0] = worldToScreen(bx1 + 8, by1 + 8);
        ctx.save();
        ctx.fillStyle = "#ffffff";
        ctx.shadowColor = "rgba(15, 23, 42, 0.09)";
        ctx.shadowBlur = 16;
        ctx.fillRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.restore();
      }

      const lyrMap = {};
      for (const l of cadScene.layers) lyrMap[l.name] = l;

      for (const p of cadScene.primitives) {
        const lInfo = lyrMap[p.l];
        let stroke;
        if (paperBlueprintMode) {
          stroke = p.l === "CENTER" ? "#b91c1c" : "#111827";
        } else {
          stroke = lInfo?.hex_dark || "#f8fafc";
        }
        ctx.strokeStyle = stroke;
        ctx.fillStyle = stroke;
        ctx.lineWidth = (p.l === "OUTLINE" || p.l === "FRAME" || p.l === "BUS") ? 1.45 : 0.85;

        const lt = lInfo?.linetype || "CONTINUOUS";
        if (lt.includes("CENTER")) ctx.setLineDash([10, 4, 2, 4]);
        else if (lt.includes("HIDDEN")) ctx.setLineDash([6, 4]);
        else ctx.setLineDash([]);

        if (p.k === "seg") {
          const [x1, y1, x2, y2] = p.d;
          ctx.beginPath();
          ctx.moveTo(camPanX + x1 * camScale, camPanY - y1 * camScale);
          ctx.lineTo(camPanX + x2 * camScale, camPanY - y2 * camScale);
          ctx.stroke();
        } else if (p.k === "circle") {
          const [cx, cy, r] = p.d;
          ctx.beginPath();
          ctx.arc(camPanX + cx * camScale, camPanY - cy * camScale, Math.max(r * camScale, 0.5), 0, Math.PI * 2);
          ctx.stroke();
        } else if (p.k === "arc") {
          const [cx, cy, r, sa, ea] = p.d;
          ctx.beginPath();
          ctx.arc(camPanX + cx * camScale, camPanY - cy * camScale, Math.max(r * camScale, 0.5), (-sa * Math.PI) / 180, (-ea * Math.PI) / 180, true);
          ctx.stroke();
        } else if (p.k === "poly" || p.k === "solid") {
          ctx.beginPath();
          p.pts.forEach(([x, y], i) => {
            const sx = camPanX + x * camScale, sy = camPanY - y * camScale;
            if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
          });
          if (p.c || p.k === "solid") ctx.closePath();
          if (p.k === "solid") ctx.fill(); else ctx.stroke();
        } else if (p.k === "text") {
          const pxSize = Math.max(p.s * camScale, 1.0);
          if (pxSize >= 3.5) {
            ctx.save();
            ctx.translate(camPanX + p.x * camScale, camPanY - p.y * camScale);
            if (p.r) ctx.rotate((-p.r * Math.PI) / 180);
            ctx.font = `${Math.min(pxSize, 46)}px 'Microsoft YaHei', Arial, sans-serif`;
            if (p.a === "mc") {
              ctx.textAlign = "center";
              ctx.textBaseline = "middle";
            } else if (p.a === "c") {
              ctx.textAlign = "center";
              ctx.textBaseline = "alphabetic";
            }
            ctx.fillText(p.t, 0, 0);
            ctx.restore();
          }
        }
      }

      // Draw saved comment region boxes & numbered pins
      for (const pin of savedPins) {
        const [sx0, sy1] = worldToScreen(pin.wx0, pin.wy0);
        const [sx1, sy0] = worldToScreen(pin.wx1, pin.wy1);
        ctx.save();
        ctx.fillStyle = "rgba(22, 104, 220, 0.05)";
        ctx.strokeStyle = "rgba(22, 104, 220, 0.55)";
        ctx.lineWidth = 1.4;
        ctx.setLineDash([5, 4]);
        ctx.fillRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.strokeRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.restore();
        drawBlueSpeechBadge(ctx, sx1, sy0, pin.idx);
      }

      // Draw active blue dashed marquee rectangle + top-right speech badge (exact Image 1 look!)
      if (activeSelection) {
        const [sx0, sy1] = worldToScreen(activeSelection.wx0, activeSelection.wy0);
        const [sx1, sy0] = worldToScreen(activeSelection.wx1, activeSelection.wy1);
        const rw = sx1 - sx0, rh = sy1 - sy0;
        ctx.save();
        ctx.fillStyle = "rgba(22, 104, 220, 0.09)";
        ctx.fillRect(sx0, sy0, rw, rh);
        ctx.strokeStyle = "#1668dc";
        ctx.lineWidth = 2.0;
        ctx.setLineDash([6, 4]);
        ctx.strokeRect(sx0, sy0, rw, rh);
        ctx.restore();
        if (!isSelecting && Math.abs(rw) > 8 && Math.abs(rh) > 8) {
          drawBlueSpeechBadge(ctx, sx1, sy0, "");
        }
      }

      ctx.restore();
      updatePillPosition();
    }

    // ===================== Spatial Hit-Testing Inside Blue Dashed Box =====================
    function hitTestRegion(wx0, wy0, wx1, wy1) {
      if (!cadScene) return { handles: [], summary: [] };
      const handleMap = new Map();
      const inBox = (x, y) => x >= wx0 && x <= wx1 && y >= wy0 && y <= wy1;
      const boxOverlap = (ax0, ay0, ax1, ay1) => !(ax1 < wx0 || ax0 > wx1 || ay1 < wy0 || ay0 > wy1);

      for (const p of cadScene.primitives) {
        if (!p.h || p.l === "FRAME") continue;
        let hit = false;
        if (p.k === "seg") {
          const [x1, y1, x2, y2] = p.d;
          hit = boxOverlap(Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2));
        } else if (p.k === "circle" || p.k === "arc") {
          const [cx, cy, r] = p.d;
          hit = boxOverlap(cx - r, cy - r, cx + r, cy + r);
        } else if (p.k === "poly" || p.k === "solid") {
          hit = p.pts.some(([x, y]) => inBox(x, y));
        } else if (p.k === "text") {
          hit = inBox(p.x, p.y);
        }
        if (hit && !handleMap.has(p.h)) {
          handleMap.set(p.h, { handle: p.h, layer: p.l, kind: p.k, text: p.t || "" });
        }
      }
      const list = Array.from(handleMap.values());
      return {
        handles: list.map(i => i.handle),
        summary: list.slice(0, 25)
      };
    }

    let editingPinIdx = null;

    function findClickedPin(mx, my) {
      for (let i = savedPins.length - 1; i >= 0; i--) {
        const p = savedPins[i];
        const [sx1, sy0] = worldToScreen(p.wx1, p.wy1);
        if (Math.hypot(mx - (sx1 + 11), my - (sy0 - 11)) <= 18) return i;
      }
      return -1;
    }

    function openPromptPill(prefill = null) {
      if (!activeSelection) return;
      const pill = document.getElementById("promptPill");
      const input = document.getElementById("pillInput");
      const delBtn = document.getElementById("pillDelete");
      const cnNums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
      const nextIdx = editingPinIdx !== null ? editingPinIdx : savedPins.length;
      const label = "问题" + (cnNums[nextIdx] || (nextIdx + 1));
      input.placeholder = `${label}（输入提问或局部改图指令...）`;
      input.value = prefill !== null ? prefill : "";
      if (delBtn) delBtn.style.display = editingPinIdx !== null ? "inline-flex" : "none";
      document.getElementById("pillHitCount").textContent = `${activeSelection.handles.length} 图元`;
      pill.style.display = "flex";
      updatePillPosition();
      setTimeout(() => { input.focus(); if (prefill !== null) input.select(); }, 20);
    }

    function updatePillPosition() {
      const pill = document.getElementById("promptPill");
      if (!activeSelection || pill.style.display === "none") return;
      const vp = document.getElementById("vp2d").getBoundingClientRect();
      const [sx0, sy1] = worldToScreen(activeSelection.wx0, activeSelection.wy0);
      const [sx1, sy0] = worldToScreen(activeSelection.wx1, activeSelection.wy1);
      const cx = (sx0 + sx1) / 2;
      const cy = (sy0 + sy1) / 2;
      const pw = Math.min(380, vp.width - 24);
      const left = Math.max(12, Math.min(vp.width - pw - 12, cx - pw * 0.25));
      const top = Math.max(12, Math.min(vp.height - 62, cy - 10));
      pill.style.left = left + "px";
      pill.style.top = top + "px";
    }

    function closePromptPill() {
      activeSelection = null;
      editingPinIdx = null;
      document.getElementById("promptPill").style.display = "none";
      document.getElementById("pillDelete").style.display = "none";
      draw2D();
    }

    document.getElementById("pillDelete").onclick = () => {
      if (editingPinIdx !== null && editingPinIdx >= 0 && editingPinIdx < savedPins.length) {
        savedPins.splice(editingPinIdx, 1);
        savedPins.forEach((p, idx) => { p.idx = idx + 1; });
      }
      closePromptPill();
    };

    async function submitRegionPrompt() {
      if (!activeSelection) return;
      const input = document.getElementById("pillInput");
      const text = input.value.trim() || input.placeholder.split("（")[0];
      const regionMm = [
        +activeSelection.wx0.toFixed(2),
        +activeSelection.wy0.toFixed(2),
        +activeSelection.wx1.toFixed(2),
        +activeSelection.wy1.toFixed(2)
      ];
      let pinIdx;
      if (editingPinIdx !== null && editingPinIdx >= 0 && editingPinIdx < savedPins.length) {
        savedPins[editingPinIdx].text = text;
        savedPins[editingPinIdx].handles = activeSelection.handles;
        pinIdx = savedPins[editingPinIdx].idx;
      } else {
        pinIdx = savedPins.length + 1;
        savedPins.push({
          idx: pinIdx,
          wx0: activeSelection.wx0,
          wy0: activeSelection.wy0,
          wx1: activeSelection.wx1,
          wy1: activeSelection.wy1,
          text,
          handles: activeSelection.handles
        });
      }

      const contextText = [
        `[CAD 图纸框选批注 #${pinIdx}]`,
        `task_id: ${currentTaskId || "N/A"}`,
        `dxf_path: ${currentDxfPath || "N/A"}`,
        `框选毫米范围 [xmin, ymin, xmax, ymax]: [${regionMm.join(", ")}]`,
        `框内命中 DXF 图元 Handles (${activeSelection.handles.length} 个): ${activeSelection.handles.slice(0, 30).join(", ") || "无"}`,
        `用户指令/问题: ${text}`
      ].join("\n");

      const handlesSnap = [...activeSelection.handles];
      closePromptPill();

      if (mcpInitialized && (currentDxfPath || currentTaskId)) {
        try {
          const toolRes = await sendRpcRequest("tools/call", {
            name: "cad_revise_by_nl",
            arguments: {
              dxf_path: currentDxfPath || null,
              task_id: currentDxfPath ? null : currentTaskId,
              prompt_text: text,
              region_box: regionMm,
              candidate_handles: handlesSnap
            }
          });
          const sc = toolRes?.structuredContent;
          if (sc && sc.mode === "revision" && sc.scene) {
            savedPins.pop();
            cadScene = sc.scene;
            currentDxfPath = sc.output_path || currentDxfPath;
            currentTaskId = sc.task_id || currentTaskId;
            draw2D();
            document.getElementById("coordInfo").textContent = `✅ ${sc.summary_zh || "框选改图已生效"}`;
            return;
          }
        } catch (_) {}
      }

      if (mcpInitialized) {
        try {
          await sendRpcRequest("ui/update-model-context", {
            content: [{ type: "text", text: contextText }]
          });
        } catch (_) {}
        try {
          await sendRpcRequest("ui/message", {
            role: "user",
            content: [{ type: "text", text: `${text}\n\n（已框选 CAD 区域 [${regionMm.join(", ")}] mm，命中图元 Handles: ${savedPins[savedPins.length - 1].handles.slice(0, 20).join(", ") || "自动检索区域"}）` }]
          });
        } catch (_) {}
      }
      document.getElementById("coordInfo").textContent = `已提交批注 #${pinIdx}：“${text}” · 区域 [${regionMm.join(", ")}] mm`;
    }

    document.getElementById("pillSubmit").onclick = submitRegionPrompt;
    document.getElementById("pillCancel").onclick = closePromptPill;
    document.getElementById("pillInput").addEventListener("keydown", e => {
      if (e.key === "Enter") submitRegionPrompt();
      else if (e.key === "Escape") closePromptPill();
    });

    // ===================== Canvas Pointer Controls =====================
    const cv2d = document.getElementById("cv2d");
    window.addEventListener("keydown", e => { if (e.code === "Space") spacePressed = true; });
    window.addEventListener("keyup", e => { if (e.code === "Space") spacePressed = false; });

    cv2d.addEventListener("wheel", e => {
      e.preventDefault();
      const rect = cv2d.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const [wx, wy] = screenToWorld(mx, my);
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      camScale = Math.max(0.05, Math.min(80, camScale * factor));
      camPanX = mx - wx * camScale;
      camPanY = my + wy * camScale;
      draw2D();
    }, { passive: false });

    cv2d.addEventListener("mousedown", e => {
      const rect = cv2d.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (e.button === 0 && !spacePressed) {
        const hp = findClickedPin(mx, my);
        if (hp >= 0) {
          const pin = savedPins[hp];
          editingPinIdx = hp;
          const hit = hitTestRegion(pin.wx0, pin.wy0, pin.wx1, pin.wy1);
          activeSelection = { wx0: pin.wx0, wy0: pin.wy0, wx1: pin.wx1, wy1: pin.wy1, handles: hit.handles, summary: hit.summary };
          draw2D();
          openPromptPill(pin.text);
          return;
        }
      }
      if (interactionMode === "pan" || spacePressed || e.button === 1) {
        isPanning = true;
        panOriginX = e.clientX - camPanX;
        panOriginY = e.clientY - camPanY;
        return;
      }
      editingPinIdx = null;
      isSelecting = true;
      document.getElementById("promptPill").style.display = "none";
      selStartScreen = [mx, my];
      const [wx, wy] = screenToWorld(mx, my);
      activeSelection = { wx0: wx, wy0: wy, wx1: wx, wy1: wy, handles: [], summary: [] };
      draw2D();
    });

    cv2d.addEventListener("mousemove", e => {
      const rect = cv2d.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const [wx, wy] = screenToWorld(mx, my);
      if (isPanning) {
        camPanX = e.clientX - panOriginX;
        camPanY = e.clientY - panOriginY;
        draw2D();
        return;
      }
      if (isSelecting && selStartScreen) {
        const [wStartX, wStartY] = screenToWorld(selStartScreen[0], selStartScreen[1]);
        activeSelection = {
          wx0: Math.min(wStartX, wx),
          wy0: Math.min(wStartY, wy),
          wx1: Math.max(wStartX, wx),
          wy1: Math.max(wStartY, wy),
          handles: [],
          summary: []
        };
        draw2D();
      }
    });

    window.addEventListener("mouseup", () => {
      if (isPanning) isPanning = false;
      if (drag3D) drag3D = false;
      if (isSelecting) {
        isSelecting = false;
        if (activeSelection) {
          const [sx0, sy1] = worldToScreen(activeSelection.wx0, activeSelection.wy0);
          const [sx1, sy0] = worldToScreen(activeSelection.wx1, activeSelection.wy1);
          if (Math.abs(sx1 - sx0) < 10 || Math.abs(sy1 - sy0) < 10) {
            closePromptPill();
          } else {
            const hits = hitTestRegion(activeSelection.wx0, activeSelection.wy0, activeSelection.wx1, activeSelection.wy1);
            activeSelection.handles = hits.handles;
            activeSelection.summary = hits.summary;
            draw2D();
            openPromptPill();
          }
        }
      }
    });

    document.getElementById("btnMarquee").onclick = () => {
      interactionMode = "marquee";
      document.getElementById("btnMarquee").classList.add("active");
      document.getElementById("btnPanMode").classList.remove("active");
      cv2d.className = "marquee-cursor";
    };
    document.getElementById("btnPanMode").onclick = () => {
      interactionMode = "pan";
      document.getElementById("btnPanMode").classList.add("active");
      document.getElementById("btnMarquee").classList.remove("active");
      cv2d.className = "pan-cursor";
    };
    document.getElementById("btnFit").onclick = fitCamera;
    document.getElementById("btnPaperTheme").onclick = e => {
      paperBlueprintMode = !paperBlueprintMode;
      e.target.textContent = paperBlueprintMode ? "☀️ 白底蓝图" : "🌙 深色 CAD";
      draw2D();
    };

    // ===================== Lightweight 3D Crease-Edge Viewport =====================
    function parseOBJ(txt) {
      const rawV = [], faces = [];
      for (const line of txt.split("\n")) {
        const p = line.trim().split(/\s+/);
        if (p[0] === "v") rawV.push([+p[1], +p[2], +p[3]]);
        else if (p[0] === "f") faces.push(p.slice(1, 4).map(t => parseInt(t.split("/")[0], 10) - 1));
      }
      if (!rawV.length) return null;
      let minX=1e9,minY=1e9,minZ=1e9,maxX=-1e9,maxY=-1e9,maxZ=-1e9;
      for (const [x,y,z] of rawV) {
        if(x<minX)minX=x; if(y<minY)minY=y; if(z<minZ)minZ=z;
        if(x>maxX)maxX=x; if(y>maxY)maxY=y; if(z>maxZ)maxZ=z;
      }
      const cx=(minX+maxX)/2, cy=(minY+maxY)/2, cz=(minZ+maxZ)/2;
      const span = Math.max(maxX-minX, maxY-minY, maxZ-minZ, 1);
      const verts = rawV.map(([x,y,z]) => [(x-cx)/span, (y-cy)/span, (z-cz)/span]);
      const fNormals = [], edgeAdj = new Map();
      faces.forEach(([a,b,c], fi) => {
        const va=verts[a], vb=verts[b], vc=verts[c];
        const ux=vb[0]-va[0], uy=vb[1]-va[1], uz=vb[2]-va[2];
        const vx=vc[0]-va[0], vy=vc[1]-va[1], vz=vc[2]-va[2];
        let nx=uy*vz-uz*vy, ny=uz*vx-ux*vz, nz=ux*vy-uy*vx;
        const nl=Math.hypot(nx,ny,nz)||1;
        fNormals.push([nx/nl, ny/nl, nz/nl]);
        for (const [u,v] of [[a,b],[b,c],[c,a]]) {
          const k = u<v ? `${u}_${v}` : `${v}_${u}`;
          if (!edgeAdj.has(k)) edgeAdj.set(k, {u:Math.min(u,v), v:Math.max(u,v), faces:[]});
          edgeAdj.get(k).faces.push(fi);
        }
      });
      const crease = [];
      for (const item of edgeAdj.values()) {
        if (item.faces.length === 1) crease.push([item.u, item.v]);
        else if (item.faces.length >= 2) {
          const n1=fNormals[item.faces[0]], n2=fNormals[item.faces[1]];
          if (n1[0]*n2[0]+n1[1]*n2[1]+n1[2]*n2[2] < 0.898) crease.push([item.u, item.v]);
        }
      }
      return { verts, faces, crease };
    }

    function rot3([x,y,z]) {
      const cz=Math.cos(rotY), sz=Math.sin(rotY);
      const x1=x*cz-y*sz, y1=x*sz+y*cz;
      const cx=Math.cos(rotX), sx=Math.sin(rotX);
      return [x1, y1*sx+z*cx, y1*cx-z*sx];
    }

    function draw3D() {
      const cv = document.getElementById("cv3d");
      if (!cv || cv.parentElement.style.display === "none") return;
      const ctx = cv.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const W = cv.width / dpr, H = cv.height / dpr;
      ctx.save(); ctx.scale(dpr, dpr);
      ctx.fillStyle = "#0f172a"; ctx.fillRect(0, 0, W, H);
      if (!mesh3d) { ctx.restore(); return; }
      const cx = W/2, cy = H/2, sc = Math.min(W, H) * 0.68 * zoom3d;
      const proj = mesh3d.verts.map(v => { const [rx,ry,rz]=rot3(v); return [cx+rx*sc, cy-ry*sc, rz, rx, ry]; });
      const tris = mesh3d.faces.map(([a,b,c]) => {
        const pa=proj[a], pb=proj[b], pc=proj[c];
        const ux=pb[3]-pa[3], uy=pb[4]-pa[4], uz=pb[2]-pa[2];
        const vx=pc[3]-pa[3], vy=pc[4]-pa[4], vz=pc[2]-pa[2];
        let nx=uy*vz-uz*vy, ny=uz*vx-ux*vz, nz=ux*vy-uy*vx;
        const nl=Math.hypot(nx,ny,nz)||1;
        const diff=Math.max(0, Math.abs((nx*0.35+ny*0.52+nz*0.78)/nl));
        return { pa, pb, pc, d:(pa[2]+pb[2]+pc[2])/3, lum:Math.min(1, 0.26+diff*0.68) };
      }).sort((m,n) => m.d - n.d);
      for (const t of tris) {
        ctx.fillStyle = `rgb(${Math.round(55+t.lum*135)},${Math.round(96+t.lum*140)},${Math.round(140+t.lum*110)})`;
        ctx.beginPath(); ctx.moveTo(t.pa[0],t.pa[1]); ctx.lineTo(t.pb[0],t.pb[1]); ctx.lineTo(t.pc[0],t.pc[1]); ctx.closePath(); ctx.fill();
      }
      ctx.strokeStyle = "#09101d"; ctx.lineWidth = 1.1; ctx.beginPath();
      for (const [u,v] of mesh3d.crease) { ctx.moveTo(proj[u][0],proj[u][1]); ctx.lineTo(proj[v][0],proj[v][1]); }
      ctx.stroke();
      ctx.restore();
    }

    const cv3d = document.getElementById("cv3d");
    cv3d.addEventListener("mousedown", e => { drag3D = true; last3DX = e.clientX; last3DY = e.clientY; });
    cv3d.addEventListener("mousemove", e => {
      if (!drag3D) return;
      rotY += (e.clientX - last3DX) * 0.011;
      rotX = Math.max(-3.0, Math.min(0.4, rotX + (e.clientY - last3DY) * 0.011));
      last3DX = e.clientX; last3DY = e.clientY; draw3D();
    });
    cv3d.addEventListener("wheel", e => {
      e.preventDefault();
      zoom3d = Math.max(0.35, Math.min(3.5, zoom3d * (e.deltaY < 0 ? 1.12 : 0.89)));
      draw3D();
    }, { passive: false });

    initMcpBridge();
    resizeAll();
  </script>
</body>
</html>
"""
