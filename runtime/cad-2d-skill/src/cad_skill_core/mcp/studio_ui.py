"""Studio 2.0: Interactive 2D Vector CAD Viewer, Crease-Edge 3D Viewer, Visual Forms & DXF Revision Diff."""

from __future__ import annotations


def render_studio_html() -> str:
    """Return self-contained HTML5/CSS/JS CAD Studio 2.0 application."""

    return r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CAD Operations Skill · 智能自动出图工作台 — CAD Operations Studio 2.0</title>
  <style>
    :root {
      --bg: #090e1a;
      --panel: #11192c;
      --panel-alt: #17223b;
      --border: #263657;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-strong: #0284c7;
      --ok: #10b981;
      --warn: #f59e0b;
      --err: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 9px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-shrink: 0;
    }
    .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 15.5px; }
    .badge {
      font-size: 11.5px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--panel-alt);
      border: 1px solid var(--border);
      color: var(--muted);
      white-space: nowrap;
    }
    .badge.ok { border-color: rgba(16,185,129,.5); color: #34d399; }
    .badge.warn { border-color: rgba(245,158,11,.5); color: #fbbf24; }
    .row { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }
    input.api-key {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 4px 9px;
      border-radius: 6px;
      font-size: 12px;
      width: 165px;
    }
    main {
      flex: 1;
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 12px;
      padding: 12px;
      min-height: 0;
    }
    .sidebar {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      display: flex;
      flex-direction: column;
      min-height: 0;
      overflow: hidden;
    }
    .nav-tabs {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 5px;
      padding: 10px;
      border-bottom: 1px solid var(--border);
      background: #0d1526;
    }
    .tab-btn {
      background: var(--panel-alt);
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 7px 6px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 600;
      text-align: center;
    }
    .tab-btn.active {
      background: rgba(56,189,248,.16);
      border-color: var(--accent);
      color: var(--accent);
    }
    .sidebar-body {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 11px;
    }
    .sidebar-footer {
      padding: 10px 12px;
      border-top: 1px solid var(--border);
      background: #0d1526;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .sec-title { font-size: 12px; font-weight: 700; color: var(--accent); letter-spacing: .3px; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .field { display: flex; flex-direction: column; gap: 3px; }
    .field.full { grid-column: 1 / -1; }
    .field label { font-size: 11.5px; color: var(--muted); }
    .field input, .field select, textarea {
      background: #080d18;
      border: 1px solid var(--border);
      border-radius: 6px;
      color: #f1f5f9;
      padding: 6px 8px;
      font-size: 12.5px;
      font-family: inherit;
    }
    textarea { font-family: Consolas, monospace; min-height: 76px; resize: vertical; line-height: 1.45; }
    .dropzone {
      border: 1.5px dashed #3b5280;
      border-radius: 8px;
      padding: 10px;
      text-align: center;
      font-size: 12px;
      color: var(--muted);
      background: rgba(23,34,59,.45);
      cursor: pointer;
      transition: .15s;
    }
    .dropzone:hover, .dropzone.drag { border-color: var(--accent); color: var(--accent); background: rgba(56,189,248,.08); }
    button.primary, button.secondary, button.ghost {
      padding: 7px 12px;
      border-radius: 6px;
      font-size: 12.5px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid transparent;
    }
    button.primary { background: var(--accent-strong); color: #fff; }
    button.primary:hover { background: #0369a1; }
    button.secondary { background: var(--panel-alt); border-color: var(--border); color: var(--text); }
    button.secondary:hover { border-color: var(--accent); }
    button.ghost { background: transparent; border-color: var(--border); color: var(--muted); padding: 4px 8px; font-size: 11.5px; }
    button.ghost.active, button.ghost:hover { border-color: var(--accent); color: var(--accent); }
    .alert {
      font-size: 12px;
      padding: 8px 10px;
      border-radius: 6px;
      background: rgba(245,158,11,.13);
      border: 1px solid rgba(245,158,11,.4);
      color: #fde68a;
      display: none;
      white-space: pre-wrap;
    }
    .alert.error { background: rgba(239,68,68,.14); border-color: rgba(239,68,68,.45); color: #fecaca; }
    .alert.ok { background: rgba(16,185,129,.13); border-color: rgba(16,185,129,.45); color: #a7f3d0; }
    .layer-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
    .layer-table th, .layer-table td { border-bottom: 1px solid var(--border); padding: 4px 5px; text-align: left; }
    .layer-table input { width: 100%; background: #080d18; border: 1px solid var(--border); color: var(--text); padding: 3px 5px; border-radius: 4px; font-size: 11.5px; }
    .entity-list {
      max-height: 175px;
      overflow-y: auto;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #080d18;
      font-size: 11.5px;
    }
    .ent-row {
      padding: 5px 8px;
      border-bottom: 1px solid #162038;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 6px;
    }
    .ent-row:hover { background: #13203a; }
    .ent-row.selected { background: rgba(56,189,248,.22); color: #7dd3fc; font-weight: 600; }

    /* Right Workspace */
    .workspace {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      display: grid;
      grid-template-rows: auto 1fr auto auto;
      min-height: 0;
      overflow: hidden;
    }
    .ws-toolbar {
      padding: 8px 12px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      background: #0d1526;
      flex-wrap: wrap;
    }
    .stage {
      position: relative;
      display: grid;
      grid-template-columns: 1fr;
      min-height: 0;
      background: #060a12;
      overflow: hidden;
    }
    .stage.split { grid-template-columns: 1fr 1fr; }
    .viewport {
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 0;
      overflow: hidden;
      border-right: 1px solid var(--border);
    }
    .viewport:last-child { border-right: none; }
    canvas.cad-canvas { width: 100%; height: 100%; display: block; }
    .floating-layers {
      position: absolute;
      top: 10px;
      right: 10px;
      background: rgba(15,23,42,.9);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 11.5px;
      max-height: 240px;
      overflow-y: auto;
      min-width: 145px;
      backdrop-filter: blur(6px);
    }
    .lyr-item { display: flex; align-items: center; gap: 6px; padding: 2px 0; cursor: pointer; }
    .color-dot { width: 9px; height: 9px; border-radius: 2px; display: inline-block; border: 1px solid rgba(255,255,255,.3); }
    .floating-3d-bar {
      position: absolute;
      top: 10px;
      left: 10px;
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
      z-index: 4;
    }
    .diff-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      padding: 12px;
      height: 100%;
      overflow: auto;
      align-items: center;
    }
    .diff-card {
      background: #0b1220;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px;
      text-align: center;
    }
    .diff-card img { max-width: 100%; max-height: 430px; background: #fff; border-radius: 4px; margin-top: 6px; }
    .status-strip {
      padding: 5px 12px;
      background: #090f1d;
      border-top: 1px solid var(--border);
      font-family: Consolas, monospace;
      font-size: 11.5px;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
    }
    .artifact-strip {
      padding: 8px 12px;
      border-top: 1px solid var(--border);
      background: #0d1526;
      display: flex;
      align-items: center;
      gap: 7px;
      flex-wrap: wrap;
    }
    .dl-chip {
      font-size: 11.5px;
      padding: 4px 9px;
      border-radius: 5px;
      background: var(--panel-alt);
      border: 1px solid var(--border);
      color: var(--accent);
      cursor: pointer;
    }
    .dl-chip:hover { border-color: var(--accent); background: rgba(56,189,248,.12); }
    details.adv-json summary { font-size: 11.5px; color: var(--muted); cursor: pointer; padding: 4px 0; }
    .cad-canvas.marquee-mode { cursor: crosshair; }
    .prompt-pill {
      position: absolute;
      display: none;
      align-items: center;
      gap: 8px;
      min-width: 320px;
      max-width: min(480px, calc(100% - 28px));
      height: 50px;
      padding: 0 8px 0 18px;
      background: #ffffff;
      border-radius: 999px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.22), 0 2px 6px rgba(15, 23, 42, 0.08);
      border: 1px solid rgba(15, 23, 42, 0.09);
      z-index: 20;
    }
    .prompt-pill input {
      flex: 1;
      border: none !important;
      outline: none !important;
      background: transparent !important;
      font-size: 14px;
      color: #1e293b !important;
      padding: 0 !important;
      min-width: 140px;
    }
    .prompt-pill input::placeholder { color: #64748b; }
    .prompt-pill .hit-tag {
      font-size: 11px;
      color: #1d4ed8;
      background: rgba(37, 99, 235, 0.1);
      padding: 2px 8px;
      border-radius: 999px;
      white-space: nowrap;
    }
    .pill-submit {
      width: 34px;
      height: 34px;
      padding: 0;
      border-radius: 50%;
      border: none;
      background: #18181b;
      color: #ffffff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      flex-shrink: 0;
    }
    .pill-submit:hover { background: #1668dc; transform: scale(1.05); }
    .pill-cancel {
      width: 24px;
      height: 24px;
      padding: 0;
      border-radius: 50%;
      border: none;
      background: transparent;
      color: #94a3b8;
      font-size: 13px;
      cursor: pointer;
      flex-shrink: 0;
    }
    .pill-cancel:hover { color: #ef4444; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span>📐 CAD Operations Studio 2.0</span>
      <span id="healthBadge" class="badge">连接中...</span>
    </div>
    <div class="row">
      <div id="driverBadges" class="row"></div>
      <input id="apiKeyInput" class="api-key" type="password" placeholder="可选 API Key" />
    </div>
  </header>

  <main>
    <!-- Left Parameter & Revision Control Panel -->
    <aside class="sidebar">
      <div class="nav-tabs">
        <button class="tab-btn active" data-tab="mech2d">1. 二维机械图</button>
        <button class="tab-btn" data-tab="model3d">2. 三维+工程图</button>
        <button class="tab-btn" data-tab="sld">3. 电气主接线</button>
        <button class="tab-btn" data-tab="pavement">4. 路面结构图</button>
        <button class="tab-btn" data-tab="revise" style="grid-column: span 2;">5. ✏️ 上传 DXF 局部改图与对比</button>
      </div>

      <div class="sidebar-body" id="sidebarBody">
        <!-- Dynamic form injected per tab -->
      </div>

      <div class="sidebar-footer">
        <div id="alertBox" class="alert"></div>
        <div class="row" id="actionRow">
          <button class="primary" id="btnRunSync" style="flex:1;">⚡ 立即生成并交互预览</button>
          <button class="secondary" id="btnRunAsync" title="在独立隔离的 Python 子进程中排队执行">后台队列</button>
        </div>
      </div>
    </aside>

    <!-- Right Interactive CAD Canvas & 3D Viewport -->
    <section class="workspace">
      <div class="ws-toolbar">
        <div class="row" id="auditBadges">
          <span class="badge">就绪 — 选择左侧参数点击“立即生成”，鼠标滚轮无级缩放图纸</span>
        </div>
        <div class="row">
          <span class="badge" id="versionBadge2D" style="display:none;">v0 原图</span>
          <button class="ghost" id="btnUndo2D" style="display:none;" title="回退到上一版 DXF (Undo)">↶ 撤销</button>
          <button class="ghost" id="btnRedo2D" style="display:none;" title="前进到下一版 DXF (Redo)">↷ 重做</button>
          <button class="ghost" id="btnVectorDiff2D" style="display:none;" title="在矢量画布原地叠加红(旧轮廓/已删)绿(新增/已改)对比">🔴🟢 红绿叠图 Diff</button>
          <button class="ghost" id="btnQuickDiff2D" style="display:none;" title="一键对比框选口语改图前后的分屏差异">🔍 分屏对比</button>
          <button class="ghost" id="btnLegacyAudit2D" title="独立检测篡改尺寸、炸开标注、轮廓微缝断口、重线与压线">🩺 存量体检</button>
          <button class="ghost" id="btnLegacyClean2D" style="display:none;" title="一键自动清理零长退化图元、重叠图元并吸附闭合轮廓微缝">🧹 自动清洗</button>
          <button class="ghost" id="btnSendToRevise" style="display:none;">✏️ 送入局部改图台</button>
          <button class="ghost active" id="btnMarquee2D" title="单击图元直接点选，或拉出蓝色虚线框输入『倒角C5』『倒圆角R6』『新增6×Ø10均布螺栓孔 节圆80』原地改图！">🟦 点选/框选改图</button>
          <button class="ghost" id="btnFit2D">⛶ 适应窗口</button>
          <button class="ghost" id="btnTheme2D">☀️ 白纸蓝图</button>
          <button class="ghost" id="btnGrid2D"># 坐标网格</button>
          <button class="ghost" id="btnToggleLayers">图层面板</button>
        </div>
      </div>

      <div class="stage" id="stage">
        <!-- 2D Vector CAD Canvas -->
        <div class="viewport" id="vp2d">
          <canvas id="canvas2d" class="cad-canvas marquee-mode"></canvas>
          <div id="pinChipsBar2D" style="display:none;position:absolute;top:10px;left:12px;z-index:18;display:flex;flex-wrap:wrap;gap:6px;max-width:75%;pointer-events:auto;"></div>
          <div class="floating-layers" id="layerPanel" style="display:none;"></div>
          <!-- Image 1 Style Floating White Rounded Prompt Pill (supports secondary edit & delete!) -->
          <div class="prompt-pill" id="promptPill2D">
            <input id="pillInput2D" type="text" placeholder="输入指令如：把孔改成Φ18 / 删掉虚线 / 问题一" autocomplete="off" />
            <span class="hit-tag" id="pillHits2D">0 图元</span>
            <button class="pill-cancel" id="pillDelete2D" style="display:none;color:#dc2626;" title="删除该条批注">🗑️</button>
            <button class="pill-cancel" id="pillCancel2D" title="关闭胶囊">✕</button>
            <button class="pill-submit" id="pillSubmit2D" title="执行改图或保存批注（支持二次修改）">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </button>
          </div>
        </div>

        <!-- 3D Industrial Crease-Edge Viewport -->
        <div class="viewport" id="vp3d" style="display:none;">
          <div class="floating-3d-bar">
            <button class="ghost" onclick="set3DView(-0.58, 0.68)">等轴测</button>
            <button class="ghost" onclick="set3DView(-1.5708, 0)">主视</button>
            <button class="ghost" onclick="set3DView(0, 0)">俯视</button>
            <button class="ghost" onclick="set3DView(-1.5708, -1.5708)">右视</button>
            <button class="ghost" id="btnRenderMode3D" onclick="cycle3DMode()">实体+棱线</button>
          </div>
          <canvas id="viewer3d" class="cad-canvas"></canvas>
        </div>

        <!-- Before/After Revision Diff View -->
        <div class="viewport" id="vpDiff" style="display:none;">
          <div class="diff-grid" id="diffGrid"></div>
        </div>
      </div>

      <div class="status-strip">
        <span id="coordReadout">光标坐标 X: 0.00 mm, Y: 0.00 mm  |  滚轮缩放 · 左键拖拽平移</span>
        <span id="sceneStats">图元: 0 · 缩放: 100%</span>
      </div>

      <div class="artifact-strip" id="artifactBar">
        <span style="font-size:11.5px;color:var(--muted);">工程产物下载区：生成后在此一键下载 .dxf / .svg / .png / .pdf / .glb / .stl</span>
      </div>
    </section>
  </main>

  <input type="file" id="hiddenFileInput" style="display:none;" accept=".dxf,.md,.txt,.csv,.tsv,.docx,.pdf,.json" />

  <script>
    // ===================== Global Studio State =====================
    let activeTab = "mech2d";
    let lastTaskData = null;
    let cadScene = null;
    let hiddenLayers = new Set();
    let darkCadTheme = false; // Default: crisp white GB/T blueprint paper mode!
    let showCadGrid = false;
    let marqueeMode2D = true;
    let spaceHeld2D = false;
    let isSelectingBox2D = false;
    let selStartScreen2D = null;
    let activeMarquee2D = null; // { wx0, wy0, wx1, wy1, handles: [] }
    let savedRegionPins2D = []; // [{ idx, wx0, wy0, wx1, wy1, text, handles, applied }]
    let editingPinIndex2D = null; // index into savedRegionPins2D when re-editing an existing pin!
    let highlightedHandle = null;
    let highlightedHandleSet = null;
    let hoverHandle2D = null;
    let vectorDiffActive2D = false;
    let currentVectorDiff2D = null;
    let versionStack2D = [];
    let versionCursor2D = -1;
    let legacyAuditIssues2D = [];

    // 2D Camera (world mm -> screen px: sx = camPanX + wx * camScale, sy = camPanY - wy * camScale)
    let camScale = 1.8, camPanX = 60, camPanY = 420;
    let isPanning2D = false, panStartX = 0, panStartY = 0;

    // 3D Camera & Crease-Edge Mesh
    let mesh3d = null;
    let rotX = -0.58, rotY = 0.68, zoom3d = 1.0, renderMode3D = 0; // 0=solid+edges, 1=xray, 2=wireframe
    let drag3D = false, last3DX = 0, last3DY = 0;

    // Revision Workspace State
    let revState = {
      dxfPath: null,
      taskId: null,
      sha256: null,
      units: "mm",
      entities: [],
      selectedEntity: null
    };

    // Pavement Dynamic Layers State
    let pavLayers = [
      { name: "细粒式沥青混凝土", material: "AC-13C", thickness: 40 },
      { name: "中粒式沥青混凝土", material: "AC-20C", thickness: 60 },
      { name: "水泥稳定碎石基层", material: "5%水泥稳定碎石", thickness: 200 },
      { name: "级配碎石底基层", material: "级配碎石", thickness: 180 }
    ];

    function authHeaders() {
      const k = document.getElementById("apiKeyInput").value.trim();
      const h = { "Content-Type": "application/json" };
      if (k) h["Authorization"] = "Bearer " + k;
      return h;
    }

    function setAlert(msg, kind = "warn") {
      const box = document.getElementById("alertBox");
      if (!msg) { box.style.display = "none"; return; }
      box.style.display = "block";
      box.className = "alert" + (kind === "error" ? " error" : (kind === "ok" ? " ok" : ""));
      box.textContent = msg;
    }

    // ===================== Sidebar Form Templates =====================
    function renderSidebar() {
      const body = document.getElementById("sidebarBody");
      const actRow = document.getElementById("actionRow");
      actRow.style.display = activeTab === "revise" ? "none" : "flex";
      setAlert("");

      if (activeTab === "mech2d") {
        body.innerHTML = `
          <div class="sec-title">方式一：一句话口语描述 或 拖入项目书 (.docx/.pdf/.md)</div>
          <div class="dropzone" onclick="triggerUpload('doc')">📄 点击或拖拽上传项目需求文档 (.docx / .pdf / .csv / .md)</div>
          <div class="field full">
            <label>自然语言工程描述（支持口语直接解析，留空则使用下方参数表单）</label>
            <textarea id="m2dBrief" placeholder="例如：画一个45钢直齿圆柱齿轮，模数3齿数28齿宽30轴孔24"></textarea>
          </div>
          <div class="row" style="flex-wrap:wrap;gap:5px;">
            <button class="ghost" onclick="fillM2DBrief('设计一个45钢直齿圆柱齿轮，模数3齿数28齿宽30轴孔24')">⚙ 示例：直齿圆柱齿轮</button>
            <button class="ghost" onclick="fillM2DBrief('画一个HT200减速器轴承透盖，外径130止口直径90内孔42高度32厚12，在110节圆上均布6个9的孔')">🔩 示例：轴承透盖</button>
            <button class="ghost" onclick="fillM2DBrief('做一个Q235B带加强筋L型直角支座，底板长130底板宽84总高92厚14加强筋厚10轴承孔28安装孔径12')">📐 示例：带筋L型支座</button>
            <button class="ghost" onclick="fillM2DBrief('画个304不锈钢连接法兰，外径150内孔48厚20，在112的节圆上均布6个14的螺栓孔')">连接法兰</button>
            <button class="ghost" onclick="fillM2DBrief('设计一根45钢三段阶梯轴，第一段直径26长32，第二段40长50，第三段22长28')">阶梯传动轴</button>
            <button class="ghost" onclick="fillAssemblyBomDemo()">📋 夹具总装图(4列明细表)</button>
          </div>

          <div class="sec-title" style="margin-top:4px;">方式二：可视化标准零件参数表单 & 行业制图标准</div>
          <div class="form-grid">
            <div class="field full">
              <label>制图标准风格预设 (GB/T · 重工 · 市政 · ISO)</label>
              <select id="m2dStdProfile">
                <option value="gb-mechanical">GB/T 机械精加工默认标准 (0.70/0.35mm)</option>
                <option value="gb-heavy-machinery">GB/T 重工装备与夹具总装标准 (高对比粗轮廓)</option>
                <option value="gb-civil-construction">GB/T 市政与土建结构图标准</option>
                <option value="iso-international">ISO 国际英文工程蓝图标准 (English Title Block)</option>
              </select>
            </div>
            <div class="field">
              <label>零件族 (共 6 类三视图标准件)</label>
              <select id="m2dFamily" onchange="syncM2DFormVisibility()">
                <option value="spur-gear">⚙ 直齿圆柱齿轮 (Spur Gear)</option>
                <option value="bearing-cap">🔩 减速器轴承透盖 (Bearing Cap)</option>
                <option value="ribbed-bracket">📐 带加强筋L型支座 (Ribbed Bracket)</option>
                <option value="flange">连接法兰盘 (Flange)</option>
                <option value="stepped-shaft">多阶梯传动轴 (Shaft)</option>
                <option value="slotted-bracket">圆角腰形槽支架板</option>
              </select>
            </div>
            <div class="field"><label>材料牌号</label><input id="m2dMat" value="45钢" /></div>
            <div class="field"><label>图纸名称</label><input id="m2dName" value="直齿圆柱齿轮" /></div>
            <div class="field"><label>图号</label><input id="m2dNo" value="GEAR-2026-01" /></div>
          </div>
          <div id="m2dParamFields" class="form-grid"></div>
          <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;margin:2px 0;">
            <input type="checkbox" id="chkProSymbols" checked /> ✨ 附加国标表面粗糙度 Ra 三角勾 + 基准 [A] + 形位公差框
          </label>
          <button class="secondary" onclick="checkCompile2D()">🔍 预检参数完整性 (缺失/冲突检查)</button>
        `;
        syncM2DFormVisibility();
      } else if (activeTab === "model3d") {
        body.innerHTML = `
          <div class="sec-title">三维参数化水密实体 (.STEP/.GLB/.STL) + 3D→2D 国标工程图联动</div>
          <div class="field full">
            <label>三维零件族 (与二维 1:1 全对齐，自动导出工业 .STEP)</label>
            <select id="m3dMode" onchange="syncM3DFields()">
              <option value="spur_gear_3d">⚙ 直齿圆柱齿轮三维实体 (Spur Gear 3D · 真实轮齿+键槽)</option>
              <option value="bearing_cap_3d">🔩 减速器轴承透盖三维实体 (Bearing Cap 3D · 止口+双腔)</option>
              <option value="ribbed_bracket_3d">📐 带三角形加强筋 L 型支座 (Ribbed Bracket 3D)</option>
              <option value="compound_3d">✨ 多特征组合实体 (底座 + 凸台 + 沉头孔 + 通孔)</option>
              <option value="flange_3d">带颈螺栓连接法兰 (Flange 3D)</option>
              <option value="stepped_shaft_3d">空心/实心多阶梯轴 (Shaft 3D)</option>
              <option value="brief_3d">口语一句话直出三维模型 + .STEP</option>
            </select>
          </div>
          <div id="m3dDynamic"></div>
          <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;">
            <input type="checkbox" id="chk3DWith2D" checked /> 同时正交投影生成二维国标三视图（双视口同屏检视）
          </label>
        `;
        syncM3DFields();
      } else if (activeTab === "sld") {
        body.innerHTML = `
          <div class="sec-title">变电站电气主接线图可视化配置</div>
          <div class="form-grid">
            <div class="field full"><label>工程图名</label><input id="sldTitle" value="110kV/35kV 变电站电气主接线图" /></div>
            <div class="field"><label>高压侧电压等级</label><input id="sldHvName" value="110kV" /></div>
            <div class="field">
              <label>高压侧母线制式</label>
              <select id="sldHvBus">
                <option value="双母线" selected>双母线 (含母联间隔)</option>
                <option value="单母分段">单母线分段</option>
                <option value="单母线">单母线</option>
              </select>
            </div>
            <div class="field"><label>高压进线回路数</label><input type="number" id="sldHvIn" value="2" min="1" max="6" /></div>
            <div class="field"><label>主变压器台数</label><input type="number" id="sldTrCount" value="2" min="1" max="4" /></div>
            <div class="field"><label>低压侧电压等级</label><input id="sldLvName" value="35kV" /></div>
            <div class="field">
              <label>低压侧母线制式</label>
              <select id="sldLvBus">
                <option value="单母分段" selected>单母线分段</option>
                <option value="单母线">单母线</option>
              </select>
            </div>
            <div class="field"><label>低压出线回路数</label><input type="number" id="sldLvOut" value="4" min="1" max="8" /></div>
            <div class="field"><label>无功补偿电容组数</label><input type="number" id="sldCap" value="2" min="0" max="4" /></div>
          </div>
        `;
      } else if (activeTab === "pavement") {
        body.innerHTML = `
          <div class="sec-title">道路路面结构层可视化设计表</div>
          <div class="form-grid">
            <div class="field full"><label>图纸标题</label><input id="pavTitle" value="主干路沥青混凝土路面结构图" /></div>
            <div class="field"><label>路面示意总宽 (mm)</label><input type="number" id="pavWidth" value="10000" step="500" /></div>
            <div class="field"><label>竖向厚度放大倍数</label><input type="number" id="pavExag" value="5" min="1" max="15" /></div>
          </div>
          <div class="row" style="justify-content:space-between;">
            <span style="font-size:11.5px;color:var(--muted);">自上而下结构层（单位 mm）：</span>
            <button class="ghost" onclick="addPavLayer()">+ 新增结构层</button>
          </div>
          <table class="layer-table">
            <thead><tr><th>结构层名称</th><th>材料</th><th style="width:68px;">厚(mm)</th><th></th></tr></thead>
            <tbody id="pavTableBody"></tbody>
          </table>
        `;
        renderPavTable();
      } else if (activeTab === "revise") {
        renderReviseSidebar();
      }
    }

    function fillM2DBrief(txt) { document.getElementById("m2dBrief").value = txt; }

    function fillAssemblyBomDemo() {
      document.getElementById("m2dBrief").value = [
        "# 导向支架钻孔夹具总装",
        "图号：JZ-03-00",
        "图幅：A2",
        "类型：装配图",
        "外径：160 mm",
        "内孔：52 mm",
        "节圆直径：116 mm",
        "安装孔：6×Φ14",
        "厚度：24 mm",
        "技术要求：1. 装配前所有配合面去毛刺并清洗干净。",
        "技术要求：2. 紧固螺钉按对角顺序均匀拧紧。",
        "",
        "序号 | 零件名称 | 数量 | 材料",
        "1 | 夹具体 | 1 | 45钢",
        "2 | 定位套 | 1 | 40Cr",
        "3 | 导向支架 | 1 | HT150",
        "4 | 法兰压紧螺栓 | 1 | 45钢",
        "5 | 定向挡销 | 1 | GCr15",
        "6 | 可换钻模板 | 2 | 45钢"
      ].join("\n");
    }

    function syncM2DFormVisibility() {
      const fam = document.getElementById("m2dFamily").value;
      const box = document.getElementById("m2dParamFields");
      if (fam === "spur-gear") {
        document.getElementById("m2dName").value = "直齿圆柱齿轮";
        document.getElementById("m2dNo").value = "GEAR-2026-01";
        document.getElementById("m2dMat").value = "45钢";
        box.innerHTML = `
          <div class="field"><label>模数 m (mm)</label><input type="number" id="g_m" value="3" step="0.5" /></div>
          <div class="field"><label>齿数 z</label><input type="number" id="g_z" value="28" /></div>
          <div class="field"><label>齿宽 b (mm)</label><input type="number" id="g_b" value="30" /></div>
          <div class="field"><label>轴孔径 d0 (mm)</label><input type="number" id="g_bore" value="24" /></div>
        `;
      } else if (fam === "bearing-cap") {
        document.getElementById("m2dName").value = "减速器轴承透盖";
        document.getElementById("m2dNo").value = "CAP-2026-02";
        document.getElementById("m2dMat").value = "HT200";
        box.innerHTML = `
          <div class="field"><label>凸缘外径 (mm)</label><input type="number" id="bc_od" value="130" /></div>
          <div class="field"><label>止口外径 (mm)</label><input type="number" id="bc_sp" value="90" /></div>
          <div class="field"><label>轴通孔径 (mm)</label><input type="number" id="bc_id" value="42" /></div>
          <div class="field"><label>总高度 (mm)</label><input type="number" id="bc_h" value="32" /></div>
          <div class="field"><label>凸缘厚度 (mm)</label><input type="number" id="bc_ft" value="12" /></div>
          <div class="field"><label>螺钉节圆 BCD</label><input type="number" id="bc_bcd" value="110" /></div>
          <div class="field"><label>螺钉孔数</label><input type="number" id="bc_hc" value="6" /></div>
          <div class="field"><label>螺钉孔径 (mm)</label><input type="number" id="bc_hd" value="9" /></div>
        `;
      } else if (fam === "ribbed-bracket") {
        document.getElementById("m2dName").value = "带加强筋L型支座";
        document.getElementById("m2dNo").value = "BRK-RIB-03";
        document.getElementById("m2dMat").value = "Q235B";
        box.innerHTML = `
          <div class="field"><label>底板长 L (mm)</label><input type="number" id="rb_l" value="130" /></div>
          <div class="field"><label>底板宽 W (mm)</label><input type="number" id="rb_w" value="84" /></div>
          <div class="field"><label>支座总高 H (mm)</label><input type="number" id="rb_h" value="92" /></div>
          <div class="field"><label>底板/立板厚 (mm)</label><input type="number" id="rb_t" value="14" /></div>
          <div class="field"><label>加强筋厚 (mm)</label><input type="number" id="rb_rt" value="10" /></div>
          <div class="field"><label>立板轴承孔 (mm)</label><input type="number" id="rb_bd" value="28" /></div>
          <div class="field"><label>底板安装孔径 (mm)</label><input type="number" id="rb_hd" value="12" /></div>
        `;
      } else if (fam === "flange") {
        document.getElementById("m2dName").value = "连接法兰盘";
        document.getElementById("m2dNo").value = "FLG-2026-01";
        box.innerHTML = `
          <div class="field"><label>外径 OD (mm)</label><input type="number" id="f_od" value="140" /></div>
          <div class="field"><label>内孔 ID (mm)</label><input type="number" id="f_id" value="48" /></div>
          <div class="field"><label>厚度 T (mm)</label><input type="number" id="f_th" value="20" /></div>
          <div class="field"><label>节圆直径 BCD (mm)</label><input type="number" id="f_bcd" value="102" /></div>
          <div class="field"><label>螺栓孔数</label><input type="number" id="f_hc" value="4" /></div>
          <div class="field"><label>螺栓孔径 (mm)</label><input type="number" id="f_hd" value="14" /></div>
        `;
      } else if (fam === "stepped-shaft") {
        document.getElementById("m2dName").value = "阶梯传动轴";
        box.innerHTML = `
          <div class="field"><label>轴段1 直径×长度</label><input id="s_1" value="26×32" /></div>
          <div class="field"><label>轴段2 直径×长度</label><input id="s_2" value="40×50" /></div>
          <div class="field"><label>轴段3 直径×长度</label><input id="s_3" value="22×28" /></div>
        `;
      } else {
        document.getElementById("m2dName").value = "圆角腰形槽安装板";
        box.innerHTML = `
          <div class="field"><label>板宽 W (mm)</label><input type="number" id="b_w" value="140" /></div>
          <div class="field"><label>板高 H (mm)</label><input type="number" id="b_h" value="80" /></div>
          <div class="field"><label>板厚 T (mm)</label><input type="number" id="b_t" value="12" /></div>
          <div class="field"><label>圆角 R (mm)</label><input type="number" id="b_r" value="8" /></div>
          <div class="field"><label>腰形槽长 (mm)</label><input type="number" id="b_sl" value="46" /></div>
          <div class="field"><label>腰形槽宽 (mm)</label><input type="number" id="b_sw" value="14" /></div>
        `;
      }
    }

    function syncM3DFields() {
      const m = document.getElementById("m3dMode").value;
      const box = document.getElementById("m3dDynamic");
      if (m === "spur_gear_3d") {
        box.innerHTML = `
          <div class="form-grid">
            <div class="field"><label>模数 m (mm)</label><input type="number" id="g3_m" value="3" step="0.5" /></div>
            <div class="field"><label>齿数 z</label><input type="number" id="g3_z" value="28" /></div>
            <div class="field"><label>齿宽 b (mm)</label><input type="number" id="g3_b" value="30" /></div>
            <div class="field"><label>轴孔径 d0 (mm)</label><input type="number" id="g3_bore" value="24" /></div>
          </div>
        `;
      } else if (m === "bearing_cap_3d") {
        box.innerHTML = `
          <div class="form-grid">
            <div class="field"><label>凸缘外径 (mm)</label><input type="number" id="bc3_od" value="130" /></div>
            <div class="field"><label>止口外径 (mm)</label><input type="number" id="bc3_sp" value="90" /></div>
            <div class="field"><label>轴通孔径 (mm)</label><input type="number" id="bc3_id" value="42" /></div>
            <div class="field"><label>总高度 (mm)</label><input type="number" id="bc3_h" value="32" /></div>
            <div class="field"><label>凸缘厚度 (mm)</label><input type="number" id="bc3_ft" value="12" /></div>
            <div class="field"><label>螺钉节圆 BCD</label><input type="number" id="bc3_bcd" value="110" /></div>
            <div class="field"><label>螺钉孔数</label><input type="number" id="bc3_hc" value="6" /></div>
            <div class="field"><label>螺钉孔径 (mm)</label><input type="number" id="bc3_hd" value="9" /></div>
          </div>
        `;
      } else if (m === "ribbed_bracket_3d") {
        box.innerHTML = `
          <div class="form-grid">
            <div class="field"><label>底板长 L (mm)</label><input type="number" id="rb3_l" value="130" /></div>
            <div class="field"><label>底板宽 W (mm)</label><input type="number" id="rb3_w" value="84" /></div>
            <div class="field"><label>支座总高 H (mm)</label><input type="number" id="rb3_h" value="92" /></div>
            <div class="field"><label>底板/立板厚 (mm)</label><input type="number" id="rb3_t" value="14" /></div>
            <div class="field"><label>加强筋厚 (mm)</label><input type="number" id="rb3_rt" value="10" /></div>
            <div class="field"><label>立板轴承孔 (mm)</label><input type="number" id="rb3_bd" value="28" /></div>
            <div class="field"><label>底板安装孔径 (mm)</label><input type="number" id="rb3_hd" value="12" /></div>
          </div>
        `;
      } else if (m === "compound_3d") {
        box.innerHTML = `
          <div class="form-grid">
            <div class="field"><label>底板长度 W (mm)</label><input type="number" id="c3_w" value="150" /></div>
            <div class="field"><label>底板宽度 D (mm)</label><input type="number" id="c3_d" value="96" /></div>
            <div class="field"><label>底板厚度 H (mm)</label><input type="number" id="c3_h" value="18" /></div>
            <div class="field"><label>底板圆角 R (mm)</label><input type="number" id="c3_r" value="8" /></div>
            <div class="field"><label>中心圆柱凸台外径</label><input type="number" id="c3_bd" value="54" /></div>
            <div class="field"><label>中心凸台高度</label><input type="number" id="c3_bh" value="24" /></div>
            <div class="field"><label>中心轴承通孔径</label><input type="number" id="c3_bbore" value="26" /></div>
            <div class="field"><label>四角沉头孔 M8/Φ9×Φ15</label><input type="number" id="c3_cb" value="4" disabled /></div>
          </div>
        `;
      } else if (m === "flange_3d") {
        box.innerHTML = `
          <div class="form-grid">
            <div class="field"><label>法兰外径 (mm)</label><input type="number" id="f3_od" value="145" /></div>
            <div class="field"><label>中心通孔 (mm)</label><input type="number" id="f3_id" value="46" /></div>
            <div class="field"><label>底盘厚度 (mm)</label><input type="number" id="f3_th" value="18" /></div>
            <div class="field"><label>法兰颈外径 (mm)</label><input type="number" id="f3_hdia" value="74" /></div>
            <div class="field"><label>法兰颈高度 (mm)</label><input type="number" id="f3_hh" value="22" /></div>
            <div class="field"><label>节圆直径 BCD</label><input type="number" id="f3_bcd" value="108" /></div>
            <div class="field"><label>螺栓孔数</label><input type="number" id="f3_hc" value="6" /></div>
            <div class="field"><label>螺栓孔径 (mm)</label><input type="number" id="f3_hd" value="12" /></div>
          </div>
        `;
      } else if (m === "stepped_shaft_3d") {
        box.innerHTML = `
          <div class="form-grid">
            <div class="field"><label>第1段 长度,直径</label><input id="sh3_1" value="30,26" /></div>
            <div class="field"><label>第2段 长度,直径</label><input id="sh3_2" value="46,42" /></div>
            <div class="field"><label>第3段 长度,直径</label><input id="sh3_3" value="28,22" /></div>
            <div class="field"><label>轴向中心通孔 (mm)</label><input type="number" id="sh3_bore" value="10" /></div>
          </div>
        `;
      } else {
        box.innerHTML = `
          <div class="field full">
            <label>一句口语描述三维零件</label>
            <textarea id="m3dBriefTxt">做一个304不锈钢带颈法兰，外径150内孔48底盘厚18颈径76颈高24，在112的分布圆上均布6个12的孔</textarea>
          </div>
        `;
      }
    }

    function renderPavTable() {
      const tb = document.getElementById("pavTableBody");
      if (!tb) return;
      tb.innerHTML = "";
      pavLayers.forEach((l, i) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><input value="${l.name}" onchange="pavLayers[${i}].name=this.value" /></td>
          <td><input value="${l.material}" onchange="pavLayers[${i}].material=this.value" /></td>
          <td><input type="number" value="${l.thickness}" onchange="pavLayers[${i}].thickness=+this.value" /></td>
          <td><button class="ghost" onclick="pavLayers.splice(${i},1);renderPavTable()">×</button></td>
        `;
        tb.appendChild(tr);
      });
    }
    function addPavLayer() { pavLayers.push({ name: "结构层", material: "级配碎石", thickness: 150 }); renderPavTable(); }

    // ===================== Build Request Payload from Visual Forms =====================
    function buildGeneratePayload() {
      if (activeTab === "mech2d") {
        const stdProf = document.getElementById("m2dStdProfile")?.value || "gb-mechanical";
        const proSym = document.getElementById("chkProSymbols") ? document.getElementById("chkProSymbols").checked : true;
        const brief = document.getElementById("m2dBrief").value.trim();
        if (brief) return { domain: "mechanical_2d", markdown_brief: brief, standard_profile: stdProf, include_pro_symbols: proSym };
        const fam = document.getElementById("m2dFamily").value;
        const name = document.getElementById("m2dName").value.trim() || "机械零件";
        const dno = document.getElementById("m2dNo").value.trim() || "MECH-01";
        const mat = document.getElementById("m2dMat").value.trim() || "45钢";
        let md = `# ${name}\n图号：${dno}\n材料：${mat}\n`;
        if (fam === "spur-gear") {
          md += `模数：${+document.getElementById("g_m").value} mm\n齿数：${+document.getElementById("g_z").value}\n齿宽：${+document.getElementById("g_b").value} mm\n轴孔：${+document.getElementById("g_bore").value} mm\n`;
        } else if (fam === "bearing-cap") {
          md += `轴承透盖\n外径：${+document.getElementById("bc_od").value} mm\n止口直径：${+document.getElementById("bc_sp").value} mm\n内孔：${+document.getElementById("bc_id").value} mm\n高度：${+document.getElementById("bc_h").value} mm\n厚度：${+document.getElementById("bc_ft").value} mm\n节圆直径：${+document.getElementById("bc_bcd").value} mm\n安装孔：${+document.getElementById("bc_hc").value}×Φ${+document.getElementById("bc_hd").value}\n`;
        } else if (fam === "ribbed-bracket") {
          md += `加强筋直角支座\n底板长：${+document.getElementById("rb_l").value} mm\n底板宽：${+document.getElementById("rb_w").value} mm\n总高：${+document.getElementById("rb_h").value} mm\n厚度：${+document.getElementById("rb_t").value} mm\n加强筋厚：${+document.getElementById("rb_rt").value} mm\n轴承孔：${+document.getElementById("rb_bd").value} mm\n安装孔径：${+document.getElementById("rb_hd").value} mm\n`;
        } else if (fam === "flange") {
          md += `外径：${+document.getElementById("f_od").value} mm\n内孔：${+document.getElementById("f_id").value} mm\n厚度：${+document.getElementById("f_th").value} mm\n`;
          const hc = +document.getElementById("f_hc").value;
          if (hc > 0) md += `节圆直径：${+document.getElementById("f_bcd").value} mm\n安装孔：${hc}×Φ${+document.getElementById("f_hd").value}\n`;
        } else if (fam === "stepped-shaft") {
          ["s_1", "s_2", "s_3"].forEach((id, idx) => {
            const v = document.getElementById(id).value.trim();
            if (v) md += `轴段${idx + 1}：Φ${v}\n`;
          });
        } else {
          md += `宽度：${+document.getElementById("b_w").value} mm\n高度：${+document.getElementById("b_h").value} mm\n厚度：${+document.getElementById("b_t").value} mm\n圆角：R${+document.getElementById("b_r").value}\n腰形槽长：${+document.getElementById("b_sl").value} mm\n腰形槽宽：${+document.getElementById("b_sw").value} mm\n`;
        }
        return { domain: "mechanical_2d", markdown_brief: md, standard_profile: stdProf, include_pro_symbols: proSym };
      }

      if (activeTab === "model3d") {
        const m = document.getElementById("m3dMode").value;
        const with2d = document.getElementById("chk3DWith2D").checked;
        if (m === "brief_3d") {
          return { domain: "model_3d", markdown_brief: document.getElementById("m3dBriefTxt").value.trim(), with_2d_sheet: with2d };
        }
        let bodyObj;
        let pName = "三维参数化零件";
        let mat3d = "45钢";
        if (m === "spur_gear_3d") {
          pName = "直齿圆柱齿轮";
          bodyObj = {
            kind: "spur_gear_3d",
            module_mm: +document.getElementById("g3_m").value,
            teeth: +document.getElementById("g3_z").value,
            face_width: +document.getElementById("g3_b").value,
            bore_diameter: +document.getElementById("g3_bore").value,
            source_id: "src"
          };
        } else if (m === "bearing_cap_3d") {
          pName = "减速器轴承透盖";
          mat3d = "HT200";
          bodyObj = {
            kind: "bearing_cap_3d",
            flange_diameter: +document.getElementById("bc3_od").value,
            spigot_diameter: +document.getElementById("bc3_sp").value,
            bore_diameter: +document.getElementById("bc3_id").value,
            total_height: +document.getElementById("bc3_h").value,
            flange_thickness: +document.getElementById("bc3_ft").value,
            bolt_circle_diameter: +document.getElementById("bc3_bcd").value,
            hole_count: +document.getElementById("bc3_hc").value,
            hole_diameter: +document.getElementById("bc3_hd").value,
            source_id: "src"
          };
        } else if (m === "ribbed_bracket_3d") {
          pName = "带加强筋L型直角支座";
          mat3d = "Q235B";
          bodyObj = {
            kind: "ribbed_bracket_3d",
            base_length: +document.getElementById("rb3_l").value,
            base_width: +document.getElementById("rb3_w").value,
            total_height: +document.getElementById("rb3_h").value,
            base_thickness: +document.getElementById("rb3_t").value,
            wall_thickness: +document.getElementById("rb3_t").value,
            rib_thickness: +document.getElementById("rb3_rt").value,
            boss_hole_diameter: +document.getElementById("rb3_bd").value,
            mount_hole_diameter: +document.getElementById("rb3_hd").value,
            source_id: "src"
          };
        } else if (m === "compound_3d") {
          pName = "轴承凸台沉孔安装座";
          const w = +document.getElementById("c3_w").value, d = +document.getElementById("c3_d").value, h = +document.getElementById("c3_h").value;
          const cx = w / 2 - 18, cy = d / 2 - 16;
          bodyObj = {
            kind: "compound_3d",
            base_shape: "box",
            width: w, depth: d, base_height: h,
            corner_radius: +document.getElementById("c3_r").value,
            bosses: [{
              kind: "cylinder_boss", x: 0, y: 0,
              diameter: +document.getElementById("c3_bd").value,
              height: +document.getElementById("c3_bh").value,
              bore_diameter: +document.getElementById("c3_bbore").value
            }],
            through_holes: [],
            counterbores: [
              { x: -cx, y: -cy, through_diameter: 9.0, cbore_diameter: 15.0, cbore_depth: 6.0 },
              { x: cx, y: -cy, through_diameter: 9.0, cbore_diameter: 15.0, cbore_depth: 6.0 },
              { x: cx, y: cy, through_diameter: 9.0, cbore_diameter: 15.0, cbore_depth: 6.0 },
              { x: -cx, y: cy, through_diameter: 9.0, cbore_diameter: 15.0, cbore_depth: 6.0 }
            ],
            source_id: "src"
          };
        } else if (m === "flange_3d") {
          pName = "带颈连接法兰";
          bodyObj = {
            kind: "flange_3d",
            outer_diameter: +document.getElementById("f3_od").value,
            bore_diameter: +document.getElementById("f3_id").value,
            thickness: +document.getElementById("f3_th").value,
            hub_diameter: +document.getElementById("f3_hdia").value,
            hub_height: +document.getElementById("f3_hh").value,
            bolt_circle_diameter: +document.getElementById("f3_bcd").value,
            hole_count: +document.getElementById("f3_hc").value,
            hole_diameter: +document.getElementById("f3_hd").value,
            source_id: "src"
          };
        } else {
          pName = "空心多阶梯传动轴";
          const steps = ["sh3_1", "sh3_2", "sh3_3"].map(id => document.getElementById(id).value.split(",").map(Number));
          bodyObj = { kind: "stepped_shaft_3d", steps, bore_diameter: +document.getElementById("sh3_bore").value, source_id: "src" };
        }
        return {
          domain: "model_3d",
          with_2d_sheet: with2d,
          spec: {
            schema_version: "1.0",
            project: { name: pName, drawing_number: "CAD3D-2026-01" },
            material: { text: mat3d, source_id: "src" },
            technical_notes: [{ text: "未注倒角 C1.5，去毛刺。", source_id: "src" }],
            body: bodyObj,
            provenance: [{ id: "src", file: "studio-form", locator: "form", confidence: 1.0 }]
          }
        };
      }

      if (activeTab === "sld") {
        const hvName = document.getElementById("sldHvName").value.trim();
        const lvName = document.getElementById("sldLvName").value.trim();
        const hvIn = +document.getElementById("sldHvIn").value;
        const trCnt = +document.getElementById("sldTrCount").value;
        const lvOut = +document.getElementById("sldLvOut").value;
        return {
          domain: "electrical_sld",
          spec: {
            title: document.getElementById("sldTitle").value.trim(),
            sheet: { width_mm: 440, height_mm: 317 },
            title_block: { "专业": "电气", "比例": "示意", "图号": "SLD-01" },
            voltage_levels: [
              {
                name: hvName,
                bus: document.getElementById("sldHvBus").value,
                feeders: Array.from({ length: hvIn }, (_, i) => `${hvName}进线${i + 1}`),
                transformers: Array.from({ length: trCnt }, (_, i) => `${i + 1}#主变`),
                pt: 2
              },
              {
                name: lvName,
                bus: document.getElementById("sldLvBus").value,
                feeders: Array.from({ length: lvOut }, (_, i) => `${lvName}出线${i + 1}`),
                pt: 2,
                capacitors: +document.getElementById("sldCap").value
              }
            ]
          }
        };
      }

      return {
        domain: "pavement",
        spec: {
          title: document.getElementById("pavTitle").value.trim(),
          width: +document.getElementById("pavWidth").value,
          vertical_exaggeration: +document.getElementById("pavExag").value,
          scale_note: `水平按实际尺寸，竖向放大${+document.getElementById("pavExag").value}倍示意`,
          layers: pavLayers,
          annotations: ["未注明单位均为 mm", "结构层厚度以施工图为准"]
        }
      };
    }

    async function checkCompile2D() {
      const p = buildGeneratePayload();
      const r = await fetch("/api/v1/tasks/compile", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ markdown_text: p.markdown_brief })
      });
      const d = await r.json();
      const miss = d.validation?.missing || [], conf = d.validation?.conflicts || [];
      if (miss.length || conf.length) {
        setAlert(`检测到参数缺失或冲突：\n缺失：${miss.join(", ") || "无"}\n冲突：${conf.join(", ") || "无"}`, "warn");
      } else {
        setAlert(`✅ 语义编译检查通过！识别零件族：${d.spec?.geometry?.parameters?.part_family || "已就绪"}，自动比例 ${d.spec?.sheet?.scale}`, "ok");
      }
    }

    // ===================== Interactive 2D Vector CAD Canvas =====================
    function resizeCanvases() {
      for (const id of ["canvas2d", "viewer3d"]) {
        const cv = document.getElementById(id);
        if (!cv || !cv.parentElement) continue;
        const rect = cv.parentElement.getBoundingClientRect();
        if (rect.width > 10 && rect.height > 10) {
          cv.width = Math.floor(rect.width * window.devicePixelRatio);
          cv.height = Math.floor(rect.height * window.devicePixelRatio);
        }
      }
      draw2DScene();
      draw3D();
    }
    window.addEventListener("resize", resizeCanvases);

    function fit2DCamera() {
      if (!cadScene) return;
      const cv = document.getElementById("canvas2d");
      const dpr = window.devicePixelRatio || 1;
      const w = cv.width / dpr, h = cv.height / dpr;
      const [minX, minY, maxX, maxY] = cadScene.bounds_mm;
      const spanX = Math.max(maxX - minX, 1), spanY = Math.max(maxY - minY, 1);
      camScale = Math.min((w - 64) / spanX, (h - 64) / spanY);
      camPanX = (w - spanX * camScale) / 2 - minX * camScale;
      camPanY = h - (h - spanY * camScale) / 2 + minY * camScale;
      draw2DScene();
    }

    function drawBlueSpeechBadge2D(ctx, x, y, labelText) {
      const r = 13;
      ctx.save();
      ctx.fillStyle = "#0d6efd";
      ctx.shadowColor = "rgba(13, 110, 253, 0.34)";
      ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
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

    function draw2DScene() {
      const cv = document.getElementById("canvas2d");
      if (!cv) return;
      const ctx = cv.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const W = cv.width / dpr, H = cv.height / dpr;
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.fillStyle = darkCadTheme ? "#070c16" : "#e8eef5";
      ctx.fillRect(0, 0, W, H);

      if (cadScene && !darkCadTheme) {
        const [bx0, by0, bx1, by1] = cadScene.bounds_mm;
        const sx0 = camPanX + (bx0 - 6) * camScale;
        const sy0 = camPanY - (by1 + 6) * camScale;
        const sx1 = camPanX + (bx1 + 6) * camScale;
        const sy1 = camPanY - (by0 - 6) * camScale;
        ctx.save();
        ctx.fillStyle = "#ffffff";
        ctx.shadowColor = "rgba(15, 23, 42, 0.10)";
        ctx.shadowBlur = 18;
        ctx.fillRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.restore();
      }

      if (showCadGrid) {
        let stepMm = 10;
        while (stepMm * camScale < 28) stepMm *= 2;
        while (stepMm * camScale > 90 && stepMm > 1) stepMm /= 2;
        ctx.strokeStyle = darkCadTheme ? "rgba(51,65,85,0.32)" : "rgba(203,213,225,0.55)";
        ctx.lineWidth = 0.6;
        const x0 = Math.floor((-camPanX / camScale) / stepMm) * stepMm;
        const x1 = Math.ceil(((W - camPanX) / camScale) / stepMm) * stepMm;
        const y0 = Math.floor(((camPanY - H) / camScale) / stepMm) * stepMm;
        const y1 = Math.ceil((camPanY / camScale) / stepMm) * stepMm;
        ctx.beginPath();
        for (let x = x0; x <= x1; x += stepMm) {
          const sx = camPanX + x * camScale;
          ctx.moveTo(sx, 0); ctx.lineTo(sx, H);
        }
        for (let y = y0; y <= y1; y += stepMm) {
          const sy = camPanY - y * camScale;
          ctx.moveTo(0, sy); ctx.lineTo(W, sy);
        }
        ctx.stroke();
      }

      if (!cadScene) {
        ctx.fillStyle = "#64748b";
        ctx.font = "13px 'Microsoft YaHei', sans-serif";
        ctx.fillText("交互式矢量 CAD 审图画布 — 生成图纸后可直接拉出蓝色虚线框针对局部提问或改图", Math.max(24, W / 2 - 245), H / 2);
        ctx.restore();
        return;
      }

      const lyrMap = {};
      for (const l of cadScene.layers) lyrMap[l.name] = l;

      const diffOn = vectorDiffActive2D && currentVectorDiff2D;
      const diffAdded = diffOn ? new Set(currentVectorDiff2D.added_handles || []) : null;
      const diffMod = diffOn ? new Set(currentVectorDiff2D.modified_handles || []) : null;

      function renderPrimPath2D(p) {
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
          ctx.arc(
            camPanX + cx * camScale,
            camPanY - cy * camScale,
            Math.max(r * camScale, 0.5),
            (-sa * Math.PI) / 180,
            (-ea * Math.PI) / 180,
            true
          );
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
            ctx.font = `${Math.min(pxSize, 48)}px 'Microsoft YaHei', Arial, sans-serif`;
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

      // 1. If Red/Green Vector Diff is active, render ghost pre-edit / deleted contours in Crimson Red Dashed
      if (diffOn && Array.isArray(currentVectorDiff2D.ghost_before_primitives)) {
        ctx.save();
        ctx.globalAlpha = 0.88;
        ctx.strokeStyle = "#ef4444";
        ctx.fillStyle = "#ef4444";
        ctx.lineWidth = 2.1;
        ctx.setLineDash([6, 4]);
        for (const gp of currentVectorDiff2D.ghost_before_primitives) {
          if (hiddenLayers.has(gp.l)) continue;
          renderPrimPath2D(gp);
        }
        ctx.restore();
      }

      // 2. Render current vector scene primitives (with diff coloring + hover highlight + selection highlight)
      for (const p of cadScene.primitives) {
        if (hiddenLayers.has(p.l)) continue;
        const lInfo = lyrMap[p.l];
        const isHi = (highlightedHandle && p.h === highlightedHandle) || (highlightedHandleSet && highlightedHandleSet.has(p.h));
        const isHover = !isHi && hoverHandle2D && p.h === hoverHandle2D;
        const isDiffGreen = diffOn && (diffAdded.has(p.h) || diffMod.has(p.h));

        ctx.save();
        let baseStroke;
        if (darkCadTheme) {
          baseStroke = lInfo?.hex_dark || "#f8fafc";
        } else {
          baseStroke = p.l === "CENTER" ? "#b91c1c" : "#111827";
        }
        let stroke = baseStroke;
        let lw = (p.l === "OUTLINE" || p.l === "FRAME" || p.l === "BUS") ? 1.45 : 0.9;
        if (diffOn && !isDiffGreen && !isHi && !isHover) {
          ctx.globalAlpha = 0.24;
        }
        if (isDiffGreen) {
          stroke = "#10b981";
          lw = 2.4;
        }
        if (isHover) {
          stroke = "#f59e0b";
          lw = 2.5;
        }
        if (isHi) {
          stroke = "#1668dc";
          lw = 2.5;
        }
        ctx.strokeStyle = stroke;
        ctx.fillStyle = stroke;
        ctx.lineWidth = lw;

        const lt = lInfo?.linetype || "CONTINUOUS";
        if (!isHi && !isHover && !isDiffGreen && lt.includes("CENTER")) ctx.setLineDash([10, 4, 2, 4]);
        else if (!isHi && !isHover && !isDiffGreen && lt.includes("HIDDEN")) ctx.setLineDash([6, 4]);
        else ctx.setLineDash([]);

        renderPrimPath2D(p);
        ctx.restore();
      }

      // 3. Diff Legend Banner on Canvas
      if (diffOn && currentVectorDiff2D.summary) {
        const sm = currentVectorDiff2D.summary;
        const banner = `🔴 旧轮廓/已删: ${sm.deleted + sm.modified}   🟢 新特征/已改: ${sm.added + sm.modified}   ⚪ 未动图元: ${sm.untouched}`;
        ctx.save();
        ctx.font = "bold 12px 'Microsoft YaHei', sans-serif";
        const bw = ctx.measureText(banner).width + 24;
        ctx.fillStyle = "rgba(15, 23, 42, 0.86)";
        ctx.fillRect(W - bw - 14, 12, bw, 28);
        ctx.fillStyle = "#f8fafc";
        ctx.textBaseline = "middle";
        ctx.fillText(banner, W - bw - 2, 26);
        ctx.restore();
      }

      // 4. Legacy Audit Issue Markers (red/amber badges at exact CAD coordinates)
      for (let i = 0; i < legacyAuditIssues2D.length; i++) {
        const iss = legacyAuditIssues2D[i];
        if (!iss.location_box) continue;
        const [lx0, ly0, lx1, ly1] = iss.location_box;
        const sx0 = camPanX + lx0 * camScale, sy1 = camPanY - ly0 * camScale;
        const sx1 = camPanX + lx1 * camScale, sy0 = camPanY - ly1 * camScale;
        const isErr = iss.severity === "error";
        const col = isErr ? "#ef4444" : "#f59e0b";
        ctx.save();
        ctx.strokeStyle = col;
        ctx.fillStyle = isErr ? "rgba(239,68,68,0.09)" : "rgba(245,158,11,0.09)";
        ctx.lineWidth = 1.6;
        ctx.setLineDash([4, 3]);
        ctx.fillRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.strokeRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.setLineDash([]);
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(sx1 + 9, sy0 - 9, 10.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#fff";
        ctx.font = "bold 10.5px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("!" + (i + 1), sx1 + 9, sy0 - 8.5);
        ctx.restore();
      }

      // Saved numbered region comment boxes & pins
      for (const pin of savedRegionPins2D) {
        const sx0 = camPanX + pin.wx0 * camScale, sy1 = camPanY - pin.wy0 * camScale;
        const sx1 = camPanX + pin.wx1 * camScale, sy0 = camPanY - pin.wy1 * camScale;
        ctx.save();
        ctx.fillStyle = "rgba(22, 104, 220, 0.05)";
        ctx.strokeStyle = "rgba(22, 104, 220, 0.55)";
        ctx.lineWidth = 1.4;
        ctx.setLineDash([5, 4]);
        ctx.fillRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.strokeRect(sx0, sy0, sx1 - sx0, sy1 - sy0);
        ctx.restore();
        drawBlueSpeechBadge2D(ctx, sx1, sy0, pin.idx);
      }

      // Active Blue Dashed Marquee Box + Top-Right Blue Droplet Speech Badge (Reference Image 1)
      if (activeMarquee2D) {
        const sx0 = camPanX + activeMarquee2D.wx0 * camScale, sy1 = camPanY - activeMarquee2D.wy0 * camScale;
        const sx1 = camPanX + activeMarquee2D.wx1 * camScale, sy0 = camPanY - activeMarquee2D.wy1 * camScale;
        const rw = sx1 - sx0, rh = sy1 - sy0;
        ctx.save();
        ctx.fillStyle = "rgba(22, 104, 220, 0.09)";
        ctx.fillRect(sx0, sy0, rw, rh);
        ctx.strokeStyle = "#1668dc";
        ctx.lineWidth = 2.0;
        ctx.setLineDash([6, 4]);
        ctx.strokeRect(sx0, sy0, rw, rh);
        ctx.restore();
        if (!isSelectingBox2D && Math.abs(rw) > 8 && Math.abs(rh) > 8) {
          drawBlueSpeechBadge2D(ctx, sx1, sy0, "");
        }
      }

      ctx.restore();
      syncPromptPill2DPos();
      document.getElementById("sceneStats").textContent = `图元: ${cadScene.primitive_count} · 批注: ${savedRegionPins2D.length} · 缩放: ${Math.round(camScale * 50)}%`;
    }

    function hitTestMarquee2D(wx0, wy0, wx1, wy1) {
      if (!cadScene) return [];
      const handles = new Set();
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
          if (!hit && p.pts.length >= 2) {
            const cnt = (p.c || p.k === "solid") ? p.pts.length : p.pts.length - 1;
            for (let i = 0; i < cnt; i++) {
              const [x1, y1] = p.pts[i], [x2, y2] = p.pts[(i + 1) % p.pts.length];
              if (boxOverlap(Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2))) { hit = true; break; }
            }
          }
        } else if (p.k === "text") {
          const tw = Math.max(p.s, (p.t || "").length * p.s * 0.62);
          const tx0 = (p.a === "mc" || p.a === "c") ? (p.x - tw / 2) : p.x;
          const ty0 = p.a === "mc" ? (p.y - p.s / 2) : p.y;
          hit = boxOverlap(tx0, ty0, tx0 + tw, ty0 + p.s);
        }
        if (hit) handles.add(p.h);
      }
      return Array.from(handles);
    }

    function distToSeg2D(px, py, x1, y1, x2, y2) {
      const dx = x2 - x1, dy = y2 - y1;
      const l2 = dx * dx + dy * dy;
      if (l2 <= 1e-12) return Math.hypot(px - x1, py - y1);
      const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / l2));
      return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
    }

    function hitTestPoint2D(wx, wy, tolMm) {
      if (!cadScene) return null;
      let bestHandle = null;
      let bestDist = tolMm;
      for (const p of cadScene.primitives) {
        if (!p.h || p.l === "FRAME" || hiddenLayers.has(p.l)) continue;
        let d = 1e9;
        if (p.k === "seg") {
          d = distToSeg2D(wx, wy, p.d[0], p.d[1], p.d[2], p.d[3]);
        } else if (p.k === "circle") {
          d = Math.abs(Math.hypot(wx - p.d[0], wy - p.d[1]) - p.d[2]);
        } else if (p.k === "arc") {
          const [cx, cy, r, sa, ea] = p.d;
          const ang = ((Math.atan2(wy - cy, wx - cx) * 180) / Math.PI + 360) % 360;
          const sweep = (ea - sa + 360) % 360;
          const rel = (ang - sa + 360) % 360;
          if (rel <= sweep + 2.0) d = Math.abs(Math.hypot(wx - cx, wy - cy) - r);
        } else if (p.k === "poly" || p.k === "solid") {
          const cnt = (p.c || p.k === "solid") ? p.pts.length : p.pts.length - 1;
          for (let i = 0; i < cnt; i++) {
            const [x1, y1] = p.pts[i], [x2, y2] = p.pts[(i + 1) % p.pts.length];
            d = Math.min(d, distToSeg2D(wx, wy, x1, y1, x2, y2));
          }
        } else if (p.k === "text") {
          const tw = Math.max(p.s, (p.t || "").length * p.s * 0.60);
          const tx0 = (p.a === "mc" || p.a === "c") ? (p.x - tw / 2) : p.x;
          const ty0 = p.a === "mc" ? (p.y - p.s / 2) : p.y;
          if (wx >= tx0 - tolMm * 0.4 && wx <= tx0 + tw + tolMm * 0.4 && wy >= ty0 - tolMm * 0.4 && wy <= ty0 + p.s + tolMm * 0.4) {
            d = 0.05;
          }
        }
        if (d < bestDist) {
          bestDist = d;
          bestHandle = p.h;
        }
      }
      return bestHandle;
    }

    function computeHandlesBounds2D(handles) {
      if (!cadScene || !handles?.length) return null;
      const hset = new Set(handles);
      let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
      const acc = (x, y) => { if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y; };
      for (const p of cadScene.primitives) {
        if (!hset.has(p.h)) continue;
        if (p.k === "seg") { acc(p.d[0], p.d[1]); acc(p.d[2], p.d[3]); }
        else if (p.k === "circle" || p.k === "arc") { acc(p.d[0] - p.d[2], p.d[1] - p.d[2]); acc(p.d[0] + p.d[2], p.d[1] + p.d[2]); }
        else if (p.k === "poly" || p.k === "solid") { p.pts.forEach(([x, y]) => acc(x, y)); }
        else if (p.k === "text") { acc(p.x - 4, p.y - 2); acc(p.x + Math.max(p.s * 3, 10), p.y + p.s + 2); }
      }
      if (x0 > x1) return null;
      return { wx0: x0 - 2.5, wy0: y0 - 2.5, wx1: x1 + 2.5, wy1: y1 + 2.5 };
    }

    function findClickedLegacyPin2D(mx, my) {
      for (let i = legacyAuditIssues2D.length - 1; i >= 0; i--) {
        const iss = legacyAuditIssues2D[i];
        if (!iss.location_box) continue;
        const sx1 = camPanX + iss.location_box[2] * camScale;
        const sy0 = camPanY - iss.location_box[3] * camScale;
        if (Math.hypot(mx - (sx1 + 9), my - (sy0 - 9)) <= 15) return i;
      }
      return -1;
    }

    function syncVersionButtons2D() {
      const bVer = document.getElementById("versionBadge2D");
      const bUndo = document.getElementById("btnUndo2D");
      const bRedo = document.getElementById("btnRedo2D");
      const bVDiff = document.getElementById("btnVectorDiff2D");
      if (versionStack2D.length > 0 && versionCursor2D >= 0) {
        const cur = versionStack2D[versionCursor2D];
        bVer.style.display = "inline-block";
        bVer.textContent = `${cur.label} (${versionCursor2D + 1}/${versionStack2D.length})`;
        bUndo.style.display = versionStack2D.length > 1 ? "inline-block" : "none";
        bRedo.style.display = versionStack2D.length > 1 ? "inline-block" : "none";
        bUndo.disabled = versionCursor2D <= 0;
        bRedo.disabled = versionCursor2D >= versionStack2D.length - 1;
        bUndo.style.opacity = bUndo.disabled ? "0.4" : "1";
        bRedo.style.opacity = bRedo.disabled ? "0.4" : "1";
        bVDiff.style.display = cur.vectorDiff ? "inline-block" : "none";
        bVDiff.classList.toggle("active", vectorDiffActive2D && !!cur.vectorDiff);
      } else {
        bVer.style.display = "none";
        bUndo.style.display = "none";
        bRedo.style.display = "none";
        bVDiff.style.display = "none";
      }
    }

    function initVersionStack2D(dxfPath, scene, sha256 = null, label = "v0 原图") {
      if (!scene) return;
      versionStack2D = [{
        dxfPath: dxfPath || scene.dxf_path || null,
        sha256: sha256 || scene.sha256 || null,
        scene,
        vectorDiff: null,
        label
      }];
      versionCursor2D = 0;
      currentVectorDiff2D = null;
      vectorDiffActive2D = false;
      legacyAuditIssues2D = [];
      document.getElementById("btnLegacyClean2D").style.display = "none";
      syncVersionButtons2D();
    }

    function pushVersionNode2D(dxfPath, scene, vectorDiff, labelSuffix = "") {
      if (!scene) return;
      if (versionCursor2D < versionStack2D.length - 1) {
        versionStack2D = versionStack2D.slice(0, versionCursor2D + 1);
      }
      const nextNo = versionStack2D.length;
      versionStack2D.push({
        dxfPath: dxfPath || scene.dxf_path || null,
        sha256: scene.sha256 || null,
        scene,
        vectorDiff: vectorDiff || null,
        label: `v${nextNo}${labelSuffix ? "·" + labelSuffix : ""}`
      });
      versionCursor2D = versionStack2D.length - 1;
      currentVectorDiff2D = vectorDiff || null;
      if (currentVectorDiff2D) vectorDiffActive2D = true; // Auto-highlight red/green changes right after edit!
      syncVersionButtons2D();
    }

    function applyVersionNode2D(idx) {
      if (idx < 0 || idx >= versionStack2D.length) return;
      versionCursor2D = idx;
      const node = versionStack2D[idx];
      cadScene = node.scene;
      currentVectorDiff2D = node.vectorDiff;
      if (!currentVectorDiff2D) vectorDiffActive2D = false;
      if (node.dxfPath) {
        revState.dxfPath = node.dxfPath;
        revState.sha256 = node.sha256;
      }
      highlightedHandle = null;
      highlightedHandleSet = null;
      closePromptPill2D();
      syncVersionButtons2D();
      renderLayerPanel();
      draw2DScene();
      setAlert(`⏱️ 已切换至历史版本 ${node.label}`, "ok");
    }

    function findClickedPinIndex2D(mx, my) {
      for (let i = savedRegionPins2D.length - 1; i >= 0; i--) {
        const pin = savedRegionPins2D[i];
        const sx1 = camPanX + pin.wx1 * camScale;
        const sy0 = camPanY - pin.wy1 * camScale;
        // Blue droplet speech badge is centered at (sx1 + 11, sy0 - 11) with radius 11
        if (Math.hypot(mx - (sx1 + 11), my - (sy0 - 11)) <= 18) return i;
      }
      return -1;
    }

    function renderPinChipsBar2D() {
      const bar = document.getElementById("pinChipsBar2D");
      if (!bar) return;
      if (!savedRegionPins2D.length) {
        bar.style.display = "none";
        bar.innerHTML = "";
        return;
      }
      bar.style.display = "flex";
      bar.innerHTML = "";
      savedRegionPins2D.forEach((pin, idx) => {
        const chip = document.createElement("span");
        chip.style.cssText = "display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;font-size:11.5px;cursor:pointer;background:" + (pin.applied ? "rgba(16,185,129,0.92)" : "rgba(22,104,220,0.92)") + ";color:#fff;box-shadow:0 2px 6px rgba(15,23,42,0.18);";
        const shortTxt = pin.text.length > 16 ? pin.text.slice(0, 16) + "…" : pin.text;
        chip.textContent = `#${pin.idx} ${shortTxt} ✎`;
        chip.title = "点击二次修改或重新执行该批注";
        chip.onclick = (ev) => { ev.stopPropagation(); reopenSavedPin2D(idx); };
        bar.appendChild(chip);
      });
    }

    function reopenSavedPin2D(index) {
      const pin = savedRegionPins2D[index];
      if (!pin) return;
      editingPinIndex2D = index;
      const freshHandles = hitTestMarquee2D(pin.wx0, pin.wy0, pin.wx1, pin.wy1);
      activeMarquee2D = {
        wx0: pin.wx0, wy0: pin.wy0, wx1: pin.wx1, wy1: pin.wy1,
        handles: freshHandles.length ? freshHandles : (pin.handles || [])
      };
      highlightedHandleSet = new Set(activeMarquee2D.handles);
      draw2DScene();
      openPromptPill2D(pin.text);
    }

    function deleteEditingPin2D() {
      if (editingPinIndex2D === null || editingPinIndex2D < 0 || editingPinIndex2D >= savedRegionPins2D.length) return;
      const removed = savedRegionPins2D.splice(editingPinIndex2D, 1)[0];
      savedRegionPins2D.forEach((p, i) => { p.idx = i + 1; });
      editingPinIndex2D = null;
      highlightedHandleSet = null;
      closePromptPill2D();
      renderPinChipsBar2D();
      setAlert(`🗑️ 已删除批注 #${removed.idx}（剩余批注已自动重排编号）`, "ok");
    }

    function openPromptPill2D(prefillText = null) {
      if (!activeMarquee2D) return;
      const pill = document.getElementById("promptPill2D");
      const inp = document.getElementById("pillInput2D");
      const delBtn = document.getElementById("pillDelete2D");
      const cn = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];
      const idx = editingPinIndex2D !== null ? editingPinIndex2D : savedRegionPins2D.length;
      inp.placeholder = `问题${cn[idx] || (idx + 1)}（输入提问或局部改图指令...）`;
      inp.value = prefillText !== null ? prefillText : "";
      if (delBtn) delBtn.style.display = editingPinIndex2D !== null ? "inline-flex" : "none";
      document.getElementById("pillHits2D").textContent = `${activeMarquee2D.handles.length} 图元`;
      pill.style.display = "flex";
      syncPromptPill2DPos();
      setTimeout(() => { inp.focus(); if (prefillText !== null) inp.select(); }, 20);
    }

    function syncPromptPill2DPos() {
      const pill = document.getElementById("promptPill2D");
      if (!activeMarquee2D || !pill || pill.style.display === "none") return;
      const vp = document.getElementById("vp2d").getBoundingClientRect();
      const sx0 = camPanX + activeMarquee2D.wx0 * camScale;
      const sy0 = camPanY - activeMarquee2D.wy1 * camScale;
      const sx1 = camPanX + activeMarquee2D.wx1 * camScale;
      const sy1 = camPanY - activeMarquee2D.wy0 * camScale;
      const cx = (sx0 + sx1) / 2, cy = (sy0 + sy1) / 2;
      const pw = Math.min(410, vp.width - 28);
      pill.style.left = Math.max(12, Math.min(vp.width - pw - 12, cx - pw * 0.25)) + "px";
      pill.style.top = Math.max(12, Math.min(vp.height - 64, cy - 10)) + "px";
    }

    function closePromptPill2D() {
      activeMarquee2D = null;
      editingPinIndex2D = null;
      const pill = document.getElementById("promptPill2D");
      const delBtn = document.getElementById("pillDelete2D");
      if (pill) pill.style.display = "none";
      if (delBtn) delBtn.style.display = "none";
      draw2DScene();
    }

    let lastNlDiffPair = null;

    async function submitPromptPill2D() {
      if (!activeMarquee2D) return;
      const inp = document.getElementById("pillInput2D");
      const txt = inp.value.trim() || "问题一";
      const boxMm = [
        +activeMarquee2D.wx0.toFixed(2),
        +activeMarquee2D.wy0.toFixed(2),
        +activeMarquee2D.wx1.toFixed(2),
        +activeMarquee2D.wy1.toFixed(2)
      ];
      const handles = activeMarquee2D.handles;
      const marqueeSnap = { ...activeMarquee2D };
      const targetPinIdx = editingPinIndex2D;
      closePromptPill2D();

      function upsertPinRecord(appliedFlag) {
        const refreshed = hitTestMarquee2D(marqueeSnap.wx0, marqueeSnap.wy0, marqueeSnap.wx1, marqueeSnap.wy1);
        if (targetPinIdx !== null && targetPinIdx >= 0 && targetPinIdx < savedRegionPins2D.length) {
          savedRegionPins2D[targetPinIdx].text = txt;
          savedRegionPins2D[targetPinIdx].handles = refreshed.length ? refreshed : handles;
          savedRegionPins2D[targetPinIdx].applied = !!appliedFlag;
          renderPinChipsBar2D();
          return savedRegionPins2D[targetPinIdx].idx;
        }
        const pinNo = savedRegionPins2D.length + 1;
        savedRegionPins2D.push({
          idx: pinNo,
          wx0: marqueeSnap.wx0, wy0: marqueeSnap.wy0, wx1: marqueeSnap.wx1, wy1: marqueeSnap.wy1,
          text: txt, handles: refreshed.length ? refreshed : handles, applied: !!appliedFlag
        });
        renderPinChipsBar2D();
        return pinNo;
      }

      // Determine current 2D DXF source (supports continuous multi-step marquee edits!)
      let dxfPath = revState.dxfPath || null;
      let taskId = null;
      let artName = "drawing.dxf";
      if (!dxfPath && lastTaskData?.task_id) {
        const dxfArt = (lastTaskData.artifacts || []).find(a => a.name.endsWith(".dxf") && a.name !== "model3d.dxf");
        if (dxfArt) {
          taskId = lastTaskData.task_id;
          artName = dxfArt.name;
        }
      }

      if (dxfPath || taskId) {
        setAlert(`正在解析并执行框选指令：「${txt}」...`);
        try {
          const r = await fetch("/api/v1/tasks/revise-nl", {
            method: "POST",
            headers: authHeaders(),
            body: JSON.stringify({
              dxf_path: dxfPath,
              task_id: dxfPath ? null : taskId,
              artifact_name: artName,
              prompt_text: txt,
              region_box: boxMm,
              candidate_handles: handles
            })
          });
          const res = await r.json();
          if (!r.ok) {
            setAlert(res.error || res.detail || JSON.stringify(res), "warn");
            return;
          }

          if (res.mode === "revision" && res.scene) {
            cadScene = res.scene;
            lastTaskData = res;
            pushVersionNode2D(res.output_path, res.scene, res.vector_diff || null, res.action || "改图");
            if (res.inventory) {
              revState = {
                dxfPath: res.output_path,
                taskId: res.task_id,
                artifactName: "revised.dxf",
                sha256: res.inventory.sha256,
                units: res.inventory.units,
                entities: res.inventory.entities || [],
                selectedEntity: null
              };
              if (activeTab === "revise") renderReviseSidebar();
            }
            if (res.source_dxf_path && res.output_path) {
              lastNlDiffPair = { before: res.source_dxf_path, after: res.output_path, revTask: res };
              document.getElementById("btnQuickDiff2D").style.display = "inline-block";
            }
            const badges = document.getElementById("auditBadges");
            badges.innerHTML = `<span class="badge ok">✅ 口语局部改图完成 · ${res.task_id}</span>`;
            if (res.rubric_score) appendRubricBadge(res.rubric_score, badges);

            const bar = document.getElementById("artifactBar");
            bar.innerHTML = "";
            (res.artifacts || []).forEach(a => {
              const chip = document.createElement("span");
              chip.className = "dl-chip";
              chip.textContent = `⬇ ${a.name} (${(a.size/1024).toFixed(1)} KB)`;
              chip.onclick = async () => {
                const dl = await fetch(a.download_url, { headers: authHeaders() });
                const u = URL.createObjectURL(await dl.blob());
                const l = document.createElement("a"); l.href = u; l.download = a.name; l.click();
              };
              bar.appendChild(chip);
            });

            const pNo = upsertPinRecord(true);
            highlightedHandleSet = null;
            renderLayerPanel();
            draw2DScene();
            setAlert(`✅ [批注 #${pNo} 已生效] ${res.summary_zh || "局部改图已原地生效！"}（点击蓝色角标 #${pNo} 可继续二次修改或删除批注）`, "ok");
            return;
          }

          // Question / inspection pin mode (supports secondary editing!)
          const pinNo = upsertPinRecord(false);
          highlightedHandleSet = new Set(handles);
          draw2DScene();
          setAlert(`💬 [批注 #${pinNo}] ${res.summary_zh || `命中 ${handles.length} 个图元`}（点击角标 #${pinNo} 可二次修改为改图指令或删除）`, "ok");
          return;
        } catch (err) {
          setAlert(String(err), "error");
          return;
        }
      }

      const pinNo = upsertPinRecord(false);
      highlightedHandleSet = new Set(handles);
      draw2DScene();
      setAlert(`已保存框选批注 #${pinNo}（点击角标 #${pinNo} 可随时二次修改或删除）`, "ok");
    }

    document.getElementById("btnQuickDiff2D").onclick = async () => {
      if (!lastNlDiffPair) return;
      const vpDiff = document.getElementById("vpDiff");
      if (vpDiff.style.display === "block") {
        vpDiff.style.display = "none";
        document.getElementById("vp2d").style.display = "block";
        draw2DScene();
        return;
      }
      setAlert("正在渲染改图前后像素级对比图...");
      const dr = await fetch("/api/v1/tasks/diff", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ before_dxf_path: lastNlDiffPair.before, after_dxf_path: lastNlDiffPair.after })
      });
      if (dr.ok) {
        setAlert("✅ 分屏对比已打开（再次点击右上角“🔍 改图前后对比”可切回交互蓝图画布）", "ok");
        await showBeforeAfterDiff(await dr.json(), lastNlDiffPair.revTask);
      }
    };

    document.getElementById("pillSubmit2D").onclick = submitPromptPill2D;
    document.getElementById("pillCancel2D").onclick = closePromptPill2D;
    document.getElementById("pillDelete2D").onclick = deleteEditingPin2D;
    document.getElementById("pillInput2D").addEventListener("keydown", e => {
      if (e.key === "Enter") submitPromptPill2D();
      else if (e.key === "Escape") closePromptPill2D();
    });

    function renderLayerPanel() {
      const panel = document.getElementById("layerPanel");
      if (!cadScene) { panel.innerHTML = "<span style='color:var(--muted)'>暂无图层</span>"; return; }
      panel.innerHTML = `<div style="font-weight:700;margin-bottom:5px;color:var(--accent);">图层显隐控制</div>`;
      cadScene.layers.forEach(l => {
        const row = document.createElement("label");
        row.className = "lyr-item";
        const checked = !hiddenLayers.has(l.name) ? "checked" : "";
        const dotColor = darkCadTheme ? l.hex_dark : l.hex_light;
        row.innerHTML = `<input type="checkbox" ${checked} /> <span class="color-dot" style="background:${dotColor}"></span> <span>${l.name} (${l.count})</span>`;
        row.querySelector("input").onchange = e => {
          if (e.target.checked) hiddenLayers.delete(l.name); else hiddenLayers.add(l.name);
          draw2DScene();
        };
        panel.appendChild(row);
      });
    }

    // 2D Canvas Wheel Zoom, Marquee Box Selection & Drag Pan
    const cv2d = document.getElementById("canvas2d");
    window.addEventListener("keydown", e => { if (e.code === "Space") spaceHeld2D = true; });
    window.addEventListener("keyup", e => { if (e.code === "Space") spaceHeld2D = false; });

    cv2d.addEventListener("wheel", e => {
      e.preventDefault();
      const rect = cv2d.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const wx = (mx - camPanX) / camScale;
      const wy = (camPanY - my) / camScale;
      const factor = e.deltaY < 0 ? 1.16 : 1 / 1.16;
      camScale = Math.max(0.05, Math.min(80.0, camScale * factor));
      camPanX = mx - wx * camScale;
      camPanY = my + wy * camScale;
      draw2DScene();
    }, { passive: false });

    cv2d.addEventListener("mousedown", e => {
      const rect = cv2d.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (e.button === 0 && !spaceHeld2D) {
        const hitPin = findClickedPinIndex2D(mx, my);
        if (hitPin >= 0) {
          reopenSavedPin2D(hitPin);
          return;
        }
      }
      const wantMarquee = (marqueeMode2D || e.shiftKey) && !spaceHeld2D && e.button === 0 && cadScene;
      if (!wantMarquee) {
        isPanning2D = true;
        panStartX = e.clientX - camPanX;
        panStartY = e.clientY - camPanY;
        return;
      }
      editingPinIndex2D = null;
      isSelectingBox2D = true;
      document.getElementById("promptPill2D").style.display = "none";
      selStartScreen2D = [mx, my];
      const wx = (mx - camPanX) / camScale, wy = (camPanY - my) / camScale;
      activeMarquee2D = { wx0: wx, wy0: wy, wx1: wx, wy1: wy, handles: [] };
      draw2DScene();
    });

    window.addEventListener("mouseup", (ev) => {
      isPanning2D = false;
      drag3D = false;
      if (isSelectingBox2D) {
        isSelectingBox2D = false;
        if (activeMarquee2D) {
          const sx0 = camPanX + activeMarquee2D.wx0 * camScale, sy1 = camPanY - activeMarquee2D.wy0 * camScale;
          const sx1 = camPanX + activeMarquee2D.wx1 * camScale, sy0 = camPanY - activeMarquee2D.wy1 * camScale;
          if (Math.abs(sx1 - sx0) < 9 && Math.abs(sy1 - sy0) < 9) {
            // Single-click entity point-selection mode!
            const rect = cv2d.getBoundingClientRect();
            const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
            const hitLeg = findClickedLegacyPin2D(mx, my);
            if (hitLeg >= 0) {
              const iss = legacyAuditIssues2D[hitLeg];
              highlightedHandleSet = new Set(iss.handles || []);
              closePromptPill2D();
              setAlert(`🩺 [${iss.issue_id} · ${iss.code}] ${iss.message_zh}\n💡 建议：${iss.suggestion_zh || "请核查高亮图元"}`, iss.severity === "error" ? "error" : "warn");
              draw2DScene();
              return;
            }
            const wx = (mx - camPanX) / camScale, wy = (camPanY - my) / camScale;
            const clickedHandle = hitTestPoint2D(wx, wy, 7.5 / camScale);
            if (clickedHandle) {
              const prevHandles = (ev.ctrlKey || ev.altKey || ev.metaKey) && highlightedHandleSet ? Array.from(highlightedHandleSet) : [];
              const nextSet = new Set(prevHandles);
              if (nextSet.has(clickedHandle) && prevHandles.length > 1) nextSet.delete(clickedHandle);
              else nextSet.add(clickedHandle);
              const picked = Array.from(nextSet);
              const hb = computeHandlesBounds2D(picked) || { wx0: wx - 5, wy0: wy - 5, wx1: wx + 5, wy1: wy + 5 };
              activeMarquee2D = { ...hb, handles: picked };
              highlightedHandleSet = new Set(picked);
              draw2DScene();
              openPromptPill2D();
              const meta = cadScene.entities_meta?.[clickedHandle];
              if (meta) {
                document.getElementById("pillInput2D").placeholder = `已点选 #${picked.join(",#")} (${meta.desc})：如 改成Ø18 / 倒角C5 / 倒圆角R6`;
              }
            } else {
              highlightedHandleSet = null;
              closePromptPill2D();
            }
          } else {
            activeMarquee2D.handles = hitTestMarquee2D(activeMarquee2D.wx0, activeMarquee2D.wy0, activeMarquee2D.wx1, activeMarquee2D.wy1);
            highlightedHandleSet = new Set(activeMarquee2D.handles);
            draw2DScene();
            openPromptPill2D();
          }
        }
      }
    });

    cv2d.addEventListener("mousemove", e => {
      const rect = cv2d.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const wx = (mx - camPanX) / camScale, wy = (camPanY - my) / camScale;
      const hoverPin = !isPanning2D && !isSelectingBox2D ? findClickedPinIndex2D(mx, my) : -1;
      const hoverLeg = (!isPanning2D && !isSelectingBox2D && hoverPin < 0) ? findClickedLegacyPin2D(mx, my) : -1;
      const nextHoverH = (!isPanning2D && !isSelectingBox2D && hoverPin < 0 && hoverLeg < 0)
        ? hitTestPoint2D(wx, wy, 7.0 / camScale)
        : null;
      if (nextHoverH !== hoverHandle2D) {
        hoverHandle2D = nextHoverH;
        if (!isPanning2D && !isSelectingBox2D) draw2DScene();
      }
      cv2d.style.cursor = (hoverPin >= 0 || hoverLeg >= 0 || hoverHandle2D) ? "pointer" : "";
      if (hoverPin >= 0) {
        document.getElementById("coordReadout").textContent = `点击批注角标 #${savedRegionPins2D[hoverPin].idx} 可二次修改指令或删除批注`;
      } else if (hoverLeg >= 0) {
        const iss = legacyAuditIssues2D[hoverLeg];
        document.getElementById("coordReadout").textContent = `⚠️ [${iss.code}] ${iss.message_zh}`;
      } else if (hoverHandle2D && cadScene?.entities_meta?.[hoverHandle2D]) {
        const meta = cadScene.entities_meta[hoverHandle2D];
        document.getElementById("coordReadout").textContent = `🎯 命中 #${hoverHandle2D} · ${meta.desc} [${meta.l}]  |  单击点选该图元（按住 Ctrl 可多选相邻边执行倒角/圆角）`;
      } else {
        document.getElementById("coordReadout").textContent = `光标坐标 X: ${wx.toFixed(2)} mm, Y: ${wy.toFixed(2)} mm  |  单击图元精准点选 · 拖拽拉框改图（按住空格平移） · 滚轮缩放`;
      }
      if (isPanning2D) {
        camPanX = e.clientX - panStartX;
        camPanY = e.clientY - panStartY;
        draw2DScene();
        return;
      }
      if (isSelectingBox2D && selStartScreen2D) {
        const wStartX = (selStartScreen2D[0] - camPanX) / camScale;
        const wStartY = (camPanY - selStartScreen2D[1]) / camScale;
        activeMarquee2D = {
          wx0: Math.min(wStartX, wx),
          wy0: Math.min(wStartY, wy),
          wx1: Math.max(wStartX, wx),
          wy1: Math.max(wStartY, wy),
          handles: []
        };
        draw2DScene();
      }
    });
    cv2d.addEventListener("dblclick", fit2DCamera);

    document.getElementById("btnMarquee2D").onclick = e => {
      marqueeMode2D = !marqueeMode2D;
      e.target.classList.toggle("active", marqueeMode2D);
      cv2d.classList.toggle("marquee-mode", marqueeMode2D);
    };
    document.getElementById("btnFit2D").onclick = fit2DCamera;
    document.getElementById("btnTheme2D").onclick = e => {
      darkCadTheme = !darkCadTheme;
      e.target.textContent = darkCadTheme ? "🌙 黑底彩线" : "☀️ 白纸蓝图";
      renderLayerPanel();
      draw2DScene();
    };
    document.getElementById("btnGrid2D").onclick = e => {
      showCadGrid = !showCadGrid;
      e.target.classList.toggle("active", showCadGrid);
      draw2DScene();
    };
    document.getElementById("btnToggleLayers").onclick = () => {
      const p = document.getElementById("layerPanel");
      p.style.display = p.style.display === "none" ? "block" : "none";
      renderLayerPanel();
    };
    document.getElementById("btnUndo2D").onclick = () => {
      if (versionCursor2D > 0) applyVersionNode2D(versionCursor2D - 1);
    };
    document.getElementById("btnRedo2D").onclick = () => {
      if (versionCursor2D < versionStack2D.length - 1) applyVersionNode2D(versionCursor2D + 1);
    };
    document.getElementById("btnVectorDiff2D").onclick = () => {
      if (!currentVectorDiff2D) return;
      vectorDiffActive2D = !vectorDiffActive2D;
      syncVersionButtons2D();
      draw2DScene();
    };
    document.getElementById("btnLegacyAudit2D").onclick = async () => {
      let dxfPath = revState.dxfPath || cadScene?.dxf_path || null;
      let taskId = null, artName = "drawing.dxf";
      if (!dxfPath && lastTaskData?.task_id) {
        const dxfArt = (lastTaskData.artifacts || []).find(a => a.name.endsWith(".dxf") && a.name !== "model3d.dxf");
        if (dxfArt) { taskId = lastTaskData.task_id; artName = dxfArt.name; }
      }
      if (!dxfPath && !taskId) { setAlert("请先生成或上传一份 2D DXF 图纸再执行存量体检", "warn"); return; }
      setAlert("正在执行独立存量图纸健康度与防篡改体检...");
      const r = await fetch("/api/v1/tasks/legacy-audit", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ dxf_path: dxfPath, task_id: dxfPath ? null : taskId, artifact_name: artName })
      });
      const rep = await r.json();
      if (!r.ok) { setAlert(rep.error || rep.detail || JSON.stringify(rep), "error"); return; }
      legacyAuditIssues2D = rep.issues || [];
      const cleanable = (rep.summary?.degenerate_count || 0) + (rep.summary?.duplicate_count || 0) + (rep.summary?.topology_gap_count || 0);
      document.getElementById("btnLegacyClean2D").style.display = cleanable > 0 ? "inline-block" : "none";
      draw2DScene();
      if (!legacyAuditIssues2D.length) {
        setAlert(`✅ 存量体检通过：健康分 ${rep.health_score}/100 (${rep.grade}级)，未发现篡改尺寸、断口微缝或重线！`, "ok");
      } else {
        const topLines = legacyAuditIssues2D.slice(0, 4).map(x => `• [${x.code}] ${x.message_zh}`).join("\n");
        setAlert(`🩺 存量体检报告：健康分 ${rep.health_score}/100 (${rep.grade}级) · 检出错误 ${rep.summary.errors} 项 / 警告 ${rep.summary.warnings} 项（已在画布标出 !1..!${legacyAuditIssues2D.length} 定位角标）：\n${topLines}`, rep.summary.errors > 0 ? "error" : "warn");
      }
    };
    document.getElementById("btnLegacyClean2D").onclick = async () => {
      let dxfPath = revState.dxfPath || cadScene?.dxf_path || null;
      if (!dxfPath) return;
      setAlert("正在自动清理退化图元、重叠图元并吸附闭合轮廓微缝...");
      const r = await fetch("/api/v1/tasks/legacy-audit", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ dxf_path: dxfPath, auto_clean: true })
      });
      const res = await r.json();
      if (!r.ok) { setAlert(res.error || res.detail || JSON.stringify(res), "error"); return; }
      if (res.scene) {
        cadScene = res.scene;
        revState.dxfPath = res.output_path;
        revState.sha256 = res.after_sha256;
        legacyAuditIssues2D = res.after_audit?.issues || [];
        document.getElementById("btnLegacyClean2D").style.display = "none";
        pushVersionNode2D(res.output_path, res.scene, res.vector_diff || null, "清洗");
        renderLayerPanel();
        draw2DScene();
        setAlert(`🧹 自动清洗完成：健康分 ${res.before_health_score} → ${res.after_health_score} (${res.after_grade}级) · 移除退化 ${res.removed_degenerate_handles.length} 项 / 重线 ${res.removed_duplicate_handles.length} 项 / 闭合微缝 ${res.snapped_gap_handles.length} 处！`, "ok");
      }
    };

    // ===================== Industrial Crease-Edge 3D Viewer =====================
    function parseOBJWithCreaseEdges(txt) {
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

      // Compute face normals & extract sharp feature/crease edges (angle > 26 deg)
      const fNormals = [];
      const edgeAdj = new Map();
      faces.forEach(([a, b, c], fi) => {
        const va = verts[a], vb = verts[b], vc = verts[c];
        const ux = vb[0]-va[0], uy = vb[1]-va[1], uz = vb[2]-va[2];
        const vx = vc[0]-va[0], vy = vc[1]-va[1], vz = vc[2]-va[2];
        let nx = uy*vz - uz*vy, ny = uz*vx - ux*vz, nz = ux*vy - uy*vx;
        const nl = Math.hypot(nx, ny, nz) || 1;
        fNormals.push([nx/nl, ny/nl, nz/nl]);
        for (const [u, v] of [[a,b],[b,c],[c,a]]) {
          const key = u < v ? `${u}_${v}` : `${v}_${u}`;
          if (!edgeAdj.has(key)) edgeAdj.set(key, { u: Math.min(u,v), v: Math.max(u,v), faces: [] });
          edgeAdj.get(key).faces.push(fi);
        }
      });

      const creaseEdges = [];
      for (const item of edgeAdj.values()) {
        if (item.faces.length === 1) {
          creaseEdges.push([item.u, item.v]);
        } else if (item.faces.length >= 2) {
          const n1 = fNormals[item.faces[0]], n2 = fNormals[item.faces[1]];
          const dot = n1[0]*n2[0] + n1[1]*n2[1] + n1[2]*n2[2];
          if (dot < 0.898) creaseEdges.push([item.u, item.v]); // > ~26 deg sharp CAD edge!
        }
      }
      return { verts, faces, creaseEdges, minZNorm: (minZ - cz) / span };
    }

    function set3DView(rx, ry) { rotX = rx; rotY = ry; draw3D(); }
    function cycle3DMode() {
      renderMode3D = (renderMode3D + 1) % 3;
      document.getElementById("btnRenderMode3D").textContent = ["实体+棱线", "X-Ray 半透明", "工程线框"][renderMode3D];
      draw3D();
    }

    function rotPt3D([x, y, z]) {
      // Z-up CAD world -> rotate around Z by rotY, then tilt by rotX
      const cz = Math.cos(rotY), sz = Math.sin(rotY);
      const x1 = x * cz - y * sz;
      const y1 = x * sz + y * cz;
      const cx = Math.cos(rotX), sx = Math.sin(rotX);
      const y2 = y1 * cx - z * sx;
      const z2 = y1 * sx + z * cx;
      return [x1, z2, y2]; // screen X, screen Y (up), depth
    }

    function draw3D() {
      const cv = document.getElementById("viewer3d");
      if (!cv || cv.parentElement.style.display === "none") return;
      const ctx = cv.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const W = cv.width / dpr, H = cv.height / dpr;
      ctx.save();
      ctx.scale(dpr, dpr);

      const grad = ctx.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, "#0b1324");
      grad.addColorStop(1, "#132038");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, H);

      if (!mesh3d) { ctx.restore(); return; }
      const cx = W / 2, cy = H / 2 + 10;
      const sc = Math.min(W, H) * 0.68 * zoom3d;

      // 1. Draw XY Reference Grid on floor z = minZNorm
      ctx.strokeStyle = "rgba(56,189,248,0.13)";
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      const gz = mesh3d.minZNorm;
      for (let g = -0.6; g <= 0.601; g += 0.2) {
        const a1 = rotPt3D([g, -0.6, gz]), a2 = rotPt3D([g, 0.6, gz]);
        ctx.moveTo(cx + a1[0]*sc, cy - a1[1]*sc); ctx.lineTo(cx + a2[0]*sc, cy - a2[1]*sc);
        const b1 = rotPt3D([-0.6, g, gz]), b2 = rotPt3D([0.6, g, gz]);
        ctx.moveTo(cx + b1[0]*sc, cy - b1[1]*sc); ctx.lineTo(cx + b2[0]*sc, cy - b2[1]*sc);
      }
      ctx.stroke();

      // 2. Project vertices
      const proj = mesh3d.verts.map(v => {
        const [rx, ry, rz] = rotPt3D(v);
        return [cx + rx * sc, cy - ry * sc, rz, rx, ry];
      });

      // 3. Draw shaded triangles (if not wireframe)
      if (renderMode3D !== 2) {
        const tris = mesh3d.faces.map(([a, b, c]) => {
          const pa = proj[a], pb = proj[b], pc = proj[c];
          const ux = pb[3]-pa[3], uy = pb[4]-pa[4], uz = pb[2]-pa[2];
          const vx = pc[3]-pa[3], vy = pc[4]-pa[4], vz = pc[2]-pa[2];
          let nx = uy*vz - uz*vy, ny = uz*vx - ux*vz, nz = ux*vy - uy*vx;
          const nl = Math.hypot(nx, ny, nz) || 1;
          nx/=nl; ny/=nl; nz/=nl;
          const diff = Math.max(0, Math.abs(nx * 0.35 + ny * 0.52 + nz * 0.78));
          const spec = Math.pow(Math.max(0, Math.abs(nz)), 12) * 0.28;
          return { pa, pb, pc, depth: (pa[2]+pb[2]+pc[2])/3, lum: Math.min(1, 0.24 + diff * 0.65 + spec) };
        }).sort((m, n) => m.depth - n.depth);

        const alpha = renderMode3D === 1 ? 0.42 : 1.0;
        for (const t of tris) {
          const r = Math.round(52 + t.lum * 135);
          const g = Math.round(92 + t.lum * 142);
          const b = Math.round(135 + t.lum * 115);
          ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`;
          ctx.beginPath();
          ctx.moveTo(t.pa[0], t.pa[1]);
          ctx.lineTo(t.pb[0], t.pb[1]);
          ctx.lineTo(t.pc[0], t.pc[1]);
          ctx.closePath();
          ctx.fill();
        }
      }

      // 4. Crisp CAD Feature / Crease Edges
      ctx.strokeStyle = renderMode3D === 0 ? "#09101d" : "#38bdf8";
      ctx.lineWidth = renderMode3D === 0 ? 1.15 : 0.85;
      ctx.beginPath();
      for (const [u, v] of mesh3d.creaseEdges) {
        ctx.moveTo(proj[u][0], proj[u][1]);
        ctx.lineTo(proj[v][0], proj[v][1]);
      }
      ctx.stroke();

      // 5. RGB XYZ Axis Triad in bottom-left
      const ox = 42, oy = H - 38, alen = 26;
      for (const [vec, col, lbl] of [[[1,0,0],"#ef4444","X"], [[0,1,0],"#22c55e","Y"], [[0,0,1],"#38bdf8","Z"]]) {
        const [ax, ay] = rotPt3D(vec);
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + ax*alen, oy - ay*alen); ctx.stroke();
        ctx.font = "bold 11px Consolas";
        ctx.fillText(lbl, ox + ax*(alen+9) - 3, oy - ay*(alen+9) + 4);
      }
      ctx.restore();
    }

    const cv3d = document.getElementById("viewer3d");
    cv3d.addEventListener("mousedown", e => { drag3D = true; last3DX = e.clientX; last3DY = e.clientY; });
    cv3d.addEventListener("mousemove", e => {
      if (!drag3D) return;
      rotY += (e.clientX - last3DX) * 0.011;
      rotX = Math.max(-3.0, Math.min(0.4, rotX + (e.clientY - last3DY) * 0.011));
      last3DX = e.clientX; last3DY = e.clientY;
      draw3D();
    });
    cv3d.addEventListener("wheel", e => {
      e.preventDefault();
      zoom3d = Math.max(0.35, Math.min(3.5, zoom3d * (e.deltaY < 0 ? 1.12 : 0.89)));
      draw3D();
    }, { passive: false });

    // ===================== Display Generated Task Result =====================
    function appendRubricBadge(rubric, container) {
      if (!rubric || typeof rubric.total_score !== "number") return;
      const bRub = document.createElement("span");
      bRub.className = "badge " + (rubric.total_score >= 82 ? "ok" : "warn");
      bRub.style.cursor = "pointer";
      bRub.textContent = `🏆 图纸评分: ${rubric.total_score}/100 (${rubric.grade}级·${rubric.grade_label_zh || ""})`;
      bRub.title = "点击查看五维 100 分制工程审图明细";
      bRub.onclick = () => {
        const dims = (rubric.dimensions || []).map(x => `• ${x.title_zh}: ${x.score}/${x.max_score}`).join("\n");
        const recs = (rubric.recommendations || []).length ? `\n优化建议：${rubric.recommendations.join("；")}` : "\n结论：工程制图各项指标卓越！";
        setAlert(`🏆 Rubric 百分制审图报告：总分 ${rubric.total_score}/100 (${rubric.grade}级)\n${dims}${recs}`, rubric.total_score >= 82 ? "ok" : "warn");
      };
      container.appendChild(bRub);
    }

    async function displayTaskResult(data) {
      lastTaskData = data;
      if (activeTab !== "revise") {
        revState.dxfPath = null;
        lastNlDiffPair = null;
        document.getElementById("btnQuickDiff2D").style.display = "none";
      }
      highlightedHandle = null;
      highlightedHandleSet = null;
      savedRegionPins2D = [];
      editingPinIndex2D = null;
      renderPinChipsBar2D();
      closePromptPill2D();
      document.getElementById("vpDiff").style.display = "none";
      document.getElementById("vp2d").style.display = "block";

      const badges = document.getElementById("auditBadges");
      badges.innerHTML = "";
      const bMain = document.createElement("span");
      bMain.className = "badge " + (["passed","complete","partial"].includes(data.status) ? "ok" : "warn");
      bMain.textContent = `状态: ${data.status} · ${data.task_id || ""}`;
      badges.appendChild(bMain);

      if (data.rubric_score) {
        appendRubricBadge(data.rubric_score, badges);
      }

      if (data.audit_3d) {
        const b3 = document.createElement("span");
        b3.className = "badge ok";
        b3.textContent = `3D水密流形: ${data.audit_3d.watertight ? "通过" : "异常"} · 体积 ${Math.round(data.audit_3d.actual_volume_mm3)} mm³`;
        badges.appendChild(b3);
      }
      const s2d = data.audit_summary || data.sheet_2d?.audit_summary;
      if (s2d) {
        const b2 = document.createElement("span");
        b2.className = "badge " + ((s2d.errors || 0) === 0 ? "ok" : "warn");
        b2.textContent = `2D审计: 错误 ${s2d.errors ?? 0} / 警告 ${s2d.warnings ?? 0}`;
        badges.appendChild(b2);
      }

      const arts = data.artifacts || [];
      const has2DDxf = arts.some(a => a.name.endsWith(".dxf") && a.name !== "model3d.dxf");
      document.getElementById("btnSendToRevise").style.display = has2DDxf ? "inline-block" : "none";

      // Download chips
      const bar = document.getElementById("artifactBar");
      bar.innerHTML = "";
      arts.forEach(a => {
        const chip = document.createElement("span");
        chip.className = "dl-chip";
        chip.textContent = `⬇ ${a.name} (${(a.size/1024).toFixed(1)} KB)`;
        chip.onclick = async () => {
          const r = await fetch(a.download_url, { headers: authHeaders() });
          const blob = await r.blob();
          const u = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = u; link.download = a.name; link.click();
          URL.revokeObjectURL(u);
        };
        bar.appendChild(chip);
      });

      // Load 2D Vector CAD Scene if task has a 2D DXF
      if (has2DDxf && data.task_id) {
        const sr = await fetch(`/api/v1/tasks/${data.task_id}/cad-scene`, { headers: authHeaders() });
        if (sr.ok) {
          cadScene = await sr.json();
          initVersionStack2D(cadScene.dxf_path, cadScene, cadScene.sha256, "v0 原图");
          hiddenLayers.clear();
          renderLayerPanel();
          resizeCanvases();
          fit2DCamera();
        }
      }

      // Load 3D Crease-Edge Mesh if task has .obj
      const objArt = arts.find(a => a.name.endsWith(".obj"));
      const vp3d = document.getElementById("vp3d");
      const stage = document.getElementById("stage");
      if (objArt) {
        vp3d.style.display = "block";
        stage.classList.add("split");
        if (!has2DDxf) {
          document.getElementById("vp2d").style.display = "none";
          stage.classList.remove("split");
        }
        const objTxt = await (await fetch(objArt.download_url, { headers: authHeaders() })).text();
        mesh3d = parseOBJWithCreaseEdges(objTxt);
        resizeCanvases();
      } else {
        vp3d.style.display = "none";
        stage.classList.remove("split");
        resizeCanvases();
      }
    }

    // ===================== Step B: Visual DXF Upload, Highlight & Revision Studio =====================
    function renderReviseSidebar() {
      const body = document.getElementById("sidebarBody");
      body.innerHTML = `
        <div class="sec-title">第 1 步：导入现有 DXF 图纸</div>
        <div class="dropzone" onclick="triggerUpload('dxf')">📂 点击或拖拽上传本地 .dxf 图纸进入改图台</div>
        <div style="font-size:11.5px;color:var(--muted);" id="revFileInfo">
          ${revState.dxfPath ? `当前基线：${revState.dxfPath.split(/[\\/]/).pop()} · SHA256: ${revState.sha256?.slice(0,10)}...` : "尚未加载 DXF（也可在其他标签页生成后点右上角“送入局部改图台”）"}
        </div>

        <div class="sec-title">第 2 步：点击选择图元（画布自动高亮定位）</div>
        <div class="field full">
          <select id="revTypeFilter" onchange="renderEntityList()">
            <option value="ALL">全部可编辑图元 (${revState.entities.length})</option>
            <option value="LINE">直线 LINE</option>
            <option value="CIRCLE">圆孔 CIRCLE</option>
            <option value="LWPOLYLINE">多段线轮廓 LWPOLYLINE</option>
            <option value="DIMENSION">尺寸标注 DIMENSION</option>
          </select>
        </div>
        <div class="entity-list" id="revEntityList"></div>

        <div class="sec-title">第 3 步：零代码局部修改操作</div>
        <div class="field full">
          <label>改图动作</label>
          <select id="revOpKind" onchange="renderRevOpParams()">
            <option value="delete_entities">删除选中图元 (delete_entities)</option>
            <option value="set_length">修改选中直线长度 (set_length)</option>
            <option value="resize_fixed_edge">固定边调整轮廓跨度 (resize_fixed_edge)</option>
            <option value="dimension_move">平移选中尺寸文字位置 (dimension_move)</option>
          </select>
        </div>
        <div id="revOpFields" class="form-grid"></div>
        <div class="row">
          <button class="secondary" onclick="executeRevision(true)" style="flex:1;">🔍 Dry-Run 预演</button>
          <button class="primary" onclick="executeRevision(false)" style="flex:1;">✅ 正式改图并对比</button>
        </div>
      `;
      renderEntityList();
      renderRevOpParams();
    }

    function renderEntityList() {
      const box = document.getElementById("revEntityList");
      if (!box) return;
      const flt = document.getElementById("revTypeFilter").value;
      box.innerHTML = "";
      const list = revState.entities.filter(e => flt === "ALL" || e.type === flt);
      if (!list.length) {
        box.innerHTML = `<div style="padding:10px;color:var(--muted);">无匹配图元</div>`;
        return;
      }
      list.slice(0, 120).forEach(ent => {
        const row = document.createElement("div");
        const isSel = revState.selectedEntity?.handle === ent.handle;
        row.className = "ent-row" + (isSel ? " selected" : "");
        let brief = "";
        if (ent.geometry?.radius) brief = `R=${ent.geometry.radius}`;
        else if (ent.geometry?.length) brief = `L=${ent.geometry.length}`;
        row.innerHTML = `<span>#${ent.handle} · ${ent.type} [${ent.layer}]</span><span>${brief}</span>`;
        row.onclick = () => {
          revState.selectedEntity = ent;
          highlightedHandle = ent.handle;
          document.getElementById("vpDiff").style.display = "none";
          document.getElementById("vp2d").style.display = "block";
          renderEntityList();
          renderRevOpParams();
          draw2DScene();
        };
        box.appendChild(row);
      });
    }

    function renderRevOpParams() {
      const box = document.getElementById("revOpFields");
      if (!box) return;
      const op = document.getElementById("revOpKind").value;
      const sel = revState.selectedEntity;
      if (op === "delete_entities") {
        box.innerHTML = `<div class="field full"><label>目标图元 Handle</label><input id="ro_target" value="${sel?.handle || ''}" /></div>`;
      } else if (op === "set_length") {
        box.innerHTML = `
          <div class="field"><label>直线 Handle</label><input id="ro_target" value="${sel?.handle || ''}" /></div>
          <div class="field"><label>新长度 (mm)</label><input type="number" id="ro_len" value="80" /></div>
          <div class="field full"><label>不动端点</label><select id="ro_fixed"><option value="start">固定起点 (start)</option><option value="end">固定终点 (end)</option></select></div>
        `;
      } else if (op === "resize_fixed_edge") {
        box.innerHTML = `
          <div class="field"><label>轮廓 Handle</label><input id="ro_target" value="${sel?.handle || ''}" /></div>
          <div class="field"><label>新跨度尺寸 (mm)</label><input type="number" id="ro_size" value="120" /></div>
          <div class="field"><label>方向轴</label><select id="ro_axis"><option value="x">水平 X 轴</option><option value="y">竖直 Y 轴</option></select></div>
          <div class="field"><label>固定侧</label><select id="ro_fixed"><option value="min">固定小坐标侧 (min)</option><option value="max">固定大坐标侧 (max)</option></select></div>
        `;
      } else {
        box.innerHTML = `
          <div class="field"><label>标注 Handle</label><input id="ro_target" value="${sel?.handle || ''}" /></div>
          <div class="field"><label>新位置 X,Y</label><input id="ro_pos" value="120,90" /></div>
        `;
      }
    }

    async function executeRevision(dryRun) {
      if (!revState.sha256) { setAlert("请先上传或导入一份 DXF 图纸", "error"); return; }
      const opKind = document.getElementById("revOpKind").value;
      const target = document.getElementById("ro_target")?.value.trim();
      if (!target) { setAlert("请先在上方图元列表中点击选择一个目标图元", "warn"); return; }

      let opObj = { id: "op-1", type: opKind };
      if (opKind === "delete_entities") opObj.targets = [{ handle: target }];
      else if (opKind === "set_length") {
        opObj.target = { handle: target };
        opObj.length = +document.getElementById("ro_len").value;
        opObj.fixed = document.getElementById("ro_fixed").value;
      } else if (opKind === "resize_fixed_edge") {
        opObj.targets = [{ handle: target }];
        opObj.axis = document.getElementById("ro_axis").value;
        opObj.fixed = document.getElementById("ro_fixed").value;
        opObj.size = +document.getElementById("ro_size").value;
      } else {
        const [px, py] = document.getElementById("ro_pos").value.split(",").map(Number);
        opObj.target = { handle: target };
        opObj.position = [px, py];
      }

      const reqBody = {
        dxf_path: revState.dxfPath,
        task_id: revState.dxfPath ? null : revState.taskId,
        artifact_name: revState.artifactName || "drawing.dxf",
        dry_run: dryRun,
        plan: {
          schema: "cad-revision/v1",
          baseline_sha256: revState.sha256,
          units: revState.units,
          tolerance: 1e-4,
          operations: [opObj]
        }
      };

      const r = await fetch("/api/v1/tasks/revise", { method: "POST", headers: authHeaders(), body: JSON.stringify(reqBody) });
      const d = await r.json();
      if (!r.ok) { setAlert(d.error || d.detail || JSON.stringify(d), "error"); return; }
      if (dryRun) {
        setAlert("✅ Dry-Run 安全预演验证通过！几何约束合法，可放心点击“正式改图并对比”。", "ok");
        return;
      }

      setAlert("✅ 改图已完成！正在生成修改前后分屏对比审阅图...", "ok");
      const newDxfArt = (d.artifacts || []).find(a => a.name.endsWith(".dxf"));
      if (newDxfArt && (revState.dxfPath || d.source_path)) {
        const beforeP = revState.dxfPath;
        const afterP = newDxfArt.path;
        if (beforeP && afterP) {
          const dr = await fetch("/api/v1/tasks/diff", {
            method: "POST", headers: authHeaders(),
            body: JSON.stringify({ before_dxf_path: beforeP, after_dxf_path: afterP })
          });
          if (dr.ok) {
            const diffData = await dr.json();
            await showBeforeAfterDiff(diffData, d);
            return;
          }
        }
      }
      await displayTaskResult(d);
    }

    async function showBeforeAfterDiff(diffData, revTaskData) {
      document.getElementById("vp2d").style.display = "none";
      document.getElementById("vp3d").style.display = "none";
      document.getElementById("stage").classList.remove("split");
      const vpDiff = document.getElementById("vpDiff");
      vpDiff.style.display = "block";

      const arts = diffData.artifacts || [];
      const bArt = arts.find(a => a.name.includes("before") && a.name.endsWith(".png")) || arts[0];
      const aArt = arts.find(a => a.name.includes("after") && a.name.endsWith(".png")) || arts[1];

      const fetchImg = async art => {
        if (!art) return "";
        const res = await fetch(art.download_url, { headers: authHeaders() });
        return URL.createObjectURL(await res.blob());
      };
      const bUrl = await fetchImg(bArt), aUrl = await fetchImg(aArt);
      document.getElementById("diffGrid").innerHTML = `
        <div class="diff-card"><div class="badge">修改前基线 (Before)</div>${bUrl ? `<img src="${bUrl}" />` : ""}</div>
        <div class="diff-card"><div class="badge ok">修改后结果 (After · 邻域问题 ${(diffData.issues||[]).length} 项)</div>${aUrl ? `<img src="${aUrl}" />` : ""}</div>
      `;

      // Also expose revised.dxf in bottom download bar
      const bar = document.getElementById("artifactBar");
      bar.innerHTML = "";
      (revTaskData.artifacts || []).forEach(a => {
        const chip = document.createElement("span");
        chip.className = "dl-chip";
        chip.textContent = `⬇ ${a.name} (${(a.size/1024).toFixed(1)} KB)`;
        chip.onclick = async () => {
          const r = await fetch(a.download_url, { headers: authHeaders() });
          const u = URL.createObjectURL(await r.blob());
          const l = document.createElement("a"); l.href = u; l.download = a.name; l.click();
        };
        bar.appendChild(chip);
      });
    }

    // Send generated drawing directly into Revision Studio
    document.getElementById("btnSendToRevise").onclick = async () => {
      if (!lastTaskData) return;
      const dxfArt = (lastTaskData.artifacts || []).find(a => a.name.endsWith(".dxf") && a.name !== "model3d.dxf");
      if (!dxfArt) return;
      const invResp = await fetch("/api/v1/tasks/inventory", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ task_id: lastTaskData.task_id, artifact_name: dxfArt.name })
      });
      if (invResp.ok) {
        const inv = await invResp.json();
        revState = {
          dxfPath: inv.path,
          taskId: lastTaskData.task_id,
          artifactName: dxfArt.name,
          sha256: inv.sha256,
          units: inv.units,
          entities: inv.entities || [],
          selectedEntity: null
        };
        switchTab("revise");
      }
    };

    // ===================== Drag & Drop / File Upload =====================
    let uploadMode = "doc";
    function triggerUpload(mode) {
      uploadMode = mode;
      const inp = document.getElementById("hiddenFileInput");
      inp.accept = mode === "dxf" ? ".dxf" : ".md,.txt,.csv,.tsv,.docx,.pdf";
      inp.click();
    }

    document.getElementById("hiddenFileInput").onchange = async e => {
      const file = e.target.files[0];
      if (!file) return;
      const buf = await file.arrayBuffer();
      let binary = "";
      const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
      const b64 = btoa(binary);

      setAlert(`正在安全上传并解析 ${file.name}...`);
      const r = await fetch("/api/v1/uploads", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ filename: file.name, content_base64: b64 })
      });
      const d = await r.json();
      if (!r.ok) { setAlert(d.detail || JSON.stringify(d), "error"); return; }

      if (file.name.toLowerCase().endsWith(".dxf") && d.inventory) {
        revState = {
          dxfPath: d.uploaded_path,
          taskId: null,
          sha256: d.inventory.sha256,
          units: d.inventory.units,
          entities: d.inventory.entities || [],
          selectedEntity: null
        };
        if (d.scene) {
          cadScene = d.scene;
          initVersionStack2D(d.uploaded_path, d.scene, d.inventory.sha256, "v0 导入");
          if (d.legacy_audit?.issues?.length) {
            legacyAuditIssues2D = d.legacy_audit.issues;
            const cleanable = (d.legacy_audit.summary?.degenerate_count || 0) + (d.legacy_audit.summary?.duplicate_count || 0) + (d.legacy_audit.summary?.topology_gap_count || 0);
            document.getElementById("btnLegacyClean2D").style.display = cleanable > 0 ? "inline-block" : "none";
          }
          hiddenLayers.clear();
          document.getElementById("vpDiff").style.display = "none";
          document.getElementById("vp3d").style.display = "none";
          document.getElementById("vp2d").style.display = "block";
          document.getElementById("stage").classList.remove("split");
          renderLayerPanel();
          resizeCanvases();
          fit2DCamera();
        }
        if (d.rubric_score) {
          const badges = document.getElementById("auditBadges");
          badges.innerHTML = `<span class="badge ok">已加载本地 DXF: ${file.name}</span>`;
          appendRubricBadge(d.rubric_score, badges);
        }
        renderReviseSidebar();
        setAlert(`✅ 已加载 DXF（共 ${revState.entities.length} 个可编辑图元），点击顶部徽章可查看 100 分制体检明细！`, "ok");
      } else {
        const compResp = await fetch("/api/v1/tasks/compile", {
          method: "POST", headers: authHeaders(),
          body: JSON.stringify({ input_paths: [d.uploaded_path] })
        });
        const comp = await compResp.json();
        if (comp.spec) {
          setAlert(`✅ 已从文档提取工程规约（图名：${comp.spec.project.name}），正在自动渲染图纸...`, "ok");
          const genR = await fetch("/api/v1/tasks/generate", {
            method: "POST", headers: authHeaders(),
            body: JSON.stringify({ domain: "mechanical_2d", spec: comp.spec })
          });
          if (genR.ok) await displayTaskResult(await genR.json());
        }
      }
      e.target.value = "";
    };

    // ===================== Main Run Buttons & Tab Switching =====================
    document.getElementById("btnRunSync").onclick = async () => {
      setAlert("正在编译几何、核验字体防重叠并生成交互图纸...");
      try {
        const payload = buildGeneratePayload();
        const r = await fetch("/api/v1/tasks/generate", {
          method: "POST", headers: authHeaders(), body: JSON.stringify(payload)
        });
        const d = await r.json();
        if (!r.ok) { setAlert(d.error || d.detail || JSON.stringify(d), "error"); return; }
        setAlert("");
        await displayTaskResult(d);
      } catch (err) { setAlert(String(err), "error"); }
    };

    document.getElementById("btnRunAsync").onclick = async () => {
      setAlert("正在提交独立子进程异步队列...");
      try {
        const payload = buildGeneratePayload();
        const r = await fetch("/api/v1/jobs", {
          method: "POST", headers: authHeaders(), body: JSON.stringify(payload)
        });
        const d = await r.json();
        if (!r.ok) { setAlert(d.error || d.detail || JSON.stringify(d), "error"); return; }
        setAlert(`⏳ 后台子进程任务 ${d.job_id} 运行中...`);
        const timer = setInterval(async () => {
          const jr = await (await fetch(`/api/v1/jobs/${d.job_id}`, { headers: authHeaders() })).json();
          if (["completed", "failed", "cancelled"].includes(jr.status)) {
            clearInterval(timer);
            if (jr.status === "completed" && jr.result) { setAlert(""); await displayTaskResult(jr.result); }
            else setAlert(`后台任务结束: ${jr.status} ${jr.error || ""}`, "error");
          }
        }, 700);
      } catch (err) { setAlert(String(err), "error"); }
    };

    function switchTab(t) {
      activeTab = t;
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
      renderSidebar();
    }
    document.querySelectorAll(".tab-btn").forEach(b => b.onclick = () => switchTab(b.dataset.tab));

    async function initStudio() {
      renderSidebar();
      resizeCanvases();
      try {
        const h = await (await fetch("/api/v1/health")).json();
        const hb = document.getElementById("healthBadge");
        hb.textContent = `在线 · ${h.tool_count} MCP Tools`;
        hb.className = "badge ok";
      } catch (_) {}
      try {
        const dr = await fetch("/api/v1/drivers", { headers: authHeaders() });
        if (dr.ok) {
          const d = await dr.json();
          const box = document.getElementById("driverBadges");
          for (const [id, info] of Object.entries(d.drivers || {})) {
            const sp = document.createElement("span");
            sp.className = "badge " + (info.status === "available" ? "ok" : "");
            sp.textContent = `${id.toUpperCase()}: ${info.status}`;
            sp.title = info.reason || "";
            box.appendChild(sp);
          }
        }
      } catch (_) {}
    }
    initStudio();
  </script>
</body>
</html>
"""
