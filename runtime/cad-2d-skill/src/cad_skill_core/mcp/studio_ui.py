"""Zero-build-step responsive Chinese Web Auto-Drafting Studio (2D SVG + 3D Orbit Viewer)."""

from __future__ import annotations


def render_studio_html() -> str:
    """Return self-contained HTML5/CSS/JS application for the CAD Auto-Drafting Studio."""

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CAD Operations Skill — 智能自动出图工作台</title>
  <style>
    :root {
      --bg: #0b1220;
      --panel: #121c2f;
      --panel-alt: #18253d;
      --border: #263654;
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
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 12px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
    }
    .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 17px; }
    .badge {
      font-size: 12px;
      padding: 3px 9px;
      border-radius: 999px;
      background: var(--panel-alt);
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .badge.ok { border-color: rgba(16,185,129,.45); color: #34d399; }
    .badge.warn { border-color: rgba(245,158,11,.45); color: #fbbf24; }
    .top-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    input.api-key {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 12px;
      width: 190px;
    }
    main {
      flex: 1;
      display: grid;
      grid-template-columns: 430px 1fr;
      gap: 16px;
      padding: 16px;
    }
    @media (max-width: 1024px) { main { grid-template-columns: 1fr; } }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 15px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .tabs { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }
    .tab-btn {
      background: var(--panel-alt);
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 8px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12.5px;
      font-weight: 600;
      transition: .15s;
    }
    .tab-btn.active {
      background: rgba(56,189,248,.16);
      border-color: var(--accent);
      color: var(--accent);
    }
    textarea {
      width: 100%;
      min-height: 230px;
      background: #080e1a;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: #f1f5f9;
      padding: 10px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12.5px;
      line-height: 1.5;
      resize: vertical;
    }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    button.primary, button.secondary {
      padding: 8px 14px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid transparent;
    }
    button.primary { background: var(--accent-strong); color: #fff; }
    button.primary:hover { background: #0369a1; }
    button.secondary { background: var(--panel-alt); border-color: var(--border); color: var(--text); }
    button.secondary:hover { border-color: var(--accent); }
    .preset-bar { display: flex; gap: 6px; flex-wrap: wrap; }
    .preset-chip {
      font-size: 11.5px;
      padding: 3px 8px;
      border-radius: 5px;
      background: #0f172a;
      border: 1px solid var(--border);
      color: var(--accent);
      cursor: pointer;
    }
    .alert {
      font-size: 12.5px;
      padding: 9px 11px;
      border-radius: 7px;
      background: rgba(245,158,11,.12);
      border: 1px solid rgba(245,158,11,.4);
      color: #fde68a;
      display: none;
      white-space: pre-wrap;
    }
    .alert.error {
      background: rgba(239,68,68,.12);
      border-color: rgba(239,68,68,.45);
      color: #fecaca;
    }
    .viewport-grid {
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 12px;
      min-height: 620px;
    }
    .preview-stage {
      background: #070c17;
      border: 1px solid var(--border);
      border-radius: 8px;
      display: grid;
      grid-template-columns: 1fr;
      overflow: hidden;
      position: relative;
      min-height: 440px;
    }
    .preview-stage.split { grid-template-columns: 1fr 1fr; }
    .pane {
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: auto;
      padding: 12px;
      position: relative;
      border-right: 1px solid var(--border);
    }
    .pane:last-child { border-right: none; }
    .pane img { max-width: 100%; max-height: 480px; background: #fff; border-radius: 4px; }
    canvas#viewer3d { width: 100%; height: 440px; display: block; cursor: grab; }
    .artifacts { display: flex; gap: 8px; flex-wrap: wrap; }
    .dl-btn {
      font-size: 12px;
      padding: 5px 10px;
      border-radius: 6px;
      background: var(--panel-alt);
      border: 1px solid var(--border);
      color: var(--accent);
      text-decoration: none;
      cursor: pointer;
    }
    .jobs-list { font-size: 12px; max-height: 150px; overflow-y: auto; display: flex; flex-direction: column; gap: 5px; }
    .job-item {
      display: flex;
      justify-content: space-between;
      padding: 6px 8px;
      background: var(--panel-alt);
      border-radius: 5px;
      cursor: pointer;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span>CAD Operations Skill · 智能自动出图工作台</span>
      <span id="healthBadge" class="badge">正在连接网关...</span>
    </div>
    <div class="top-controls">
      <div id="driverBadges" class="row"></div>
      <input id="apiKeyInput" class="api-key" type="password" placeholder="可选 API Key (Bearer)" />
    </div>
  </header>

  <main>
    <section class="card">
      <div class="tabs">
        <button class="tab-btn active" data-domain="mechanical_2d">1. 国标二维机械图</button>
        <button class="tab-btn" data-domain="model_3d">2. 三维建模 + 3D→2D</button>
        <button class="tab-btn" data-domain="electrical_sld">3. 电气主接线图</button>
        <button class="tab-btn" data-domain="pavement">4. 道路路面结构图</button>
      </div>

      <div class="preset-bar" id="presetBar"></div>
      <textarea id="inputEditor" spellcheck="false"></textarea>

      <div class="row" id="extraOptions">
        <label style="font-size:12.5px;color:var(--muted);display:flex;align-items:center;gap:5px;">
          <input type="checkbox" id="chkWith2D" checked /> 三维模式同步投影生成国标二维工程图
        </label>
      </div>

      <div id="alertBox" class="alert"></div>

      <div class="row">
        <button class="secondary" id="btnCompile">智能语义编译检查</button>
        <button class="primary" id="btnGenerateSync">立即自动出图 (同步)</button>
        <button class="secondary" id="btnSubmitAsync">提交后台队列 (异步)</button>
      </div>

      <div style="border-top:1px solid var(--border);padding-top:10px;">
        <div class="row" style="justify-content:space-between;margin-bottom:6px;">
          <span style="font-size:12.5px;font-weight:600;color:var(--muted);">异步任务队列 (/api/v1/jobs)</span>
          <button class="preset-chip" id="btnRefreshJobs">刷新列表</button>
        </div>
        <div id="jobsList" class="jobs-list"></div>
      </div>
    </section>

    <section class="card viewport-grid">
      <div class="row" style="justify-content:space-between;">
        <div class="row" id="auditSummary">
          <span class="badge">等待生成任务 — 选择左侧模板点击“立即自动出图”体验全闭环</span>
        </div>
      </div>

      <div id="previewStage" class="preview-stage">
        <div class="pane" id="pane2d">
          <span style="color:var(--muted);font-size:13px;">二维工程图 / 渲染预览区 (.png / .svg)</span>
        </div>
        <div class="pane" id="pane3d" style="display:none;">
          <canvas id="viewer3d" width="520" height="440"></canvas>
        </div>
      </div>

      <div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:6px;">已验证工程产物下载（零乱码 / 零重叠 / 独立审计）：</div>
        <div id="artifactBar" class="artifacts"></div>
      </div>
    </section>
  </main>

  <script>
    const PRESETS = {
      mechanical_2d: [
        {
          label: "法兰盘项目书 (Markdown)",
          text: "# 连接法兰盘零件图\\n图号：FLG-2026-08\\n材料：45钢\\n外径：140 mm\\n内孔：48 mm\\n节圆直径：102 mm\\n安装孔：4×Φ14\\n厚度：20 mm\\n技术要求：未注倒角 C1.5，去毛刺。"
        },
        {
          label: "阶梯传动轴项目书",
          text: "# 三段阶梯传动轴\\n图号：SHF-2026-02\\n材料：40Cr\\n阶梯段1：Φ26×32\\n阶梯段2：Φ40×48\\n阶梯段3：Φ22×28"
        }
      ],
      model_3d: [
        {
          label: "三维带颈法兰 (Model3DSpec)",
          text: JSON.stringify({
            schema_version: "1.0",
            project: { name: "带颈连接法兰", drawing_number: "FLG-3D-101" },
            material: { text: "304不锈钢", source_id: "src" },
            technical_notes: [{ text: "未注圆角 R2，去毛刺。", source_id: "src" }],
            body: {
              kind: "flange_3d",
              outer_diameter: 140.0,
              bore_diameter: 46.0,
              thickness: 18.0,
              hub_diameter: 74.0,
              hub_height: 22.0,
              bolt_circle_diameter: 104.0,
              hole_diameter: 12.0,
              hole_count: 6,
              source_id: "src"
            },
            provenance: [{ id: "src", file: "studio", locator: "preset", confidence: 1.0 }]
          }, null, 2)
        }
      ],
      electrical_sld: [
        {
          label: "110kV双母线/35kV单母分段主接线",
          text: JSON.stringify({
            title: "110kV/35kV 变电站主接线图",
            hv: { kv: "110kV", bus_mode: "double", incoming: ["110kV 进线I", "110kV 进线II"] },
            transformers: [{ name: "1#主变", cap: "50MVA" }, { name: "2#主变", cap: "50MVA" }],
            lv: { kv: "35kV", feeders: ["35kV 馈线1", "35kV 馈线2", "35kV 馈线3", "35kV 馈线4"] }
          }, null, 2)
        }
      ],
      pavement: [
        {
          label: "高等级沥青路面结构图",
          text: JSON.stringify({
            title: "主干路沥青混凝土路面结构图",
            total_width_cm: 240,
            layers: [
              { name: "细粒式沥青混凝土 AC-13C", thickness_cm: 4, hatch: "ANSI31" },
              { name: "中粒式沥青混凝土 AC-20C", thickness_cm: 6, hatch: "ANSI37" },
              { name: "水泥稳定碎石基层", thickness_cm: 36, hatch: "AR-CONC" },
              { name: "级配碎石垫层", thickness_cm: 20, hatch: "GRAVEL" }
            ]
          }, null, 2)
        }
      ]
    };

    let currentDomain = "mechanical_2d";
    let mesh3d = null;
    let rotX = -0.55, rotY = 0.65, zoom3d = 1.0, dragging = false, lastX = 0, lastY = 0;

    function authHeaders() {
      const k = document.getElementById("apiKeyInput").value.trim();
      const h = { "Content-Type": "application/json" };
      if (k) h["Authorization"] = "Bearer " + k;
      return h;
    }

    async function loadStatus() {
      try {
        const h = await (await fetch("/api/v1/health")).json();
        const hb = document.getElementById("healthBadge");
        hb.textContent = `在线 · ${h.tool_count} MCP Tools`;
        hb.className = "badge ok";
      } catch (e) {
        document.getElementById("healthBadge").textContent = "网关离线";
      }
      try {
        const dr = await fetch("/api/v1/drivers", { headers: authHeaders() });
        if (dr.ok) {
          const data = await dr.json();
          const box = document.getElementById("driverBadges");
          box.innerHTML = "";
          for (const [id, info] of Object.entries(data.drivers || {})) {
            const sp = document.createElement("span");
            sp.className = "badge " + (info.status === "available" ? "ok" : "");
            sp.textContent = `${id.toUpperCase()}: ${info.status}`;
            sp.title = info.reason || "";
            box.appendChild(sp);
          }
        }
      } catch (_) {}
    }

    function setDomain(dom) {
      currentDomain = dom;
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.domain === dom));
      const bar = document.getElementById("presetBar");
      bar.innerHTML = "";
      (PRESETS[dom] || []).forEach((p, idx) => {
        const chip = document.createElement("span");
        chip.className = "preset-chip";
        chip.textContent = "示例：" + p.label;
        chip.onclick = () => { document.getElementById("inputEditor").value = p.text; };
        bar.appendChild(chip);
        if (idx === 0) document.getElementById("inputEditor").value = p.text;
      });
      document.getElementById("btnCompile").style.display = dom === "mechanical_2d" ? "inline-block" : "none";
      document.getElementById("extraOptions").style.display = dom === "model_3d" ? "flex" : "none";
    }

    function showAlert(msg, isErr = false) {
      const box = document.getElementById("alertBox");
      if (!msg) { box.style.display = "none"; return; }
      box.style.display = "block";
      box.className = "alert" + (isErr ? " error" : "");
      box.textContent = msg;
    }

    function buildGenerateBody() {
      const raw = document.getElementById("inputEditor").value.trim();
      if (currentDomain === "mechanical_2d") {
        if (raw.startsWith("{")) return { domain: "mechanical_2d", spec: JSON.parse(raw) };
        return { domain: "mechanical_2d", markdown_brief: raw };
      }
      return {
        domain: currentDomain,
        spec: JSON.parse(raw),
        with_2d_sheet: document.getElementById("chkWith2D").checked
      };
    }

    async function renderResult(data) {
      const sum = document.getElementById("auditSummary");
      sum.innerHTML = "";
      const stBadge = document.createElement("span");
      stBadge.className = "badge " + (["passed","complete","partial"].includes(data.status) ? "ok" : "warn");
      stBadge.textContent = `状态: ${data.status} · 任务 ${data.task_id || ""}`;
      sum.appendChild(stBadge);

      if (data.audit_3d) {
        const b3 = document.createElement("span");
        b3.className = "badge ok";
        b3.textContent = `水密流形: ${data.audit_3d.watertight ? "通过" : "失败"} · 面片 ${data.audit_3d.triangle_count} · 体积 ${Math.round(data.audit_3d.actual_volume_mm3)} mm³`;
        sum.appendChild(b3);
      }
      if (data.audit_summary) {
        const b2 = document.createElement("span");
        b2.className = "badge " + (data.audit_summary.errors === 0 ? "ok" : "warn");
        b2.textContent = `二维审计: 错误 ${data.audit_summary.errors ?? 0} / 警告 ${data.audit_summary.warnings ?? 0}`;
        sum.appendChild(b2);
      }

      const arts = data.artifacts || [];
      const bar = document.getElementById("artifactBar");
      bar.innerHTML = "";
      arts.forEach(a => {
        const btn = document.createElement("a");
        btn.className = "dl-btn";
        btn.textContent = `⬇ ${a.name} (${(a.size/1024).toFixed(1)} KB)`;
        btn.onclick = async () => {
          const r = await fetch(a.download_url, { headers: authHeaders() });
          const blob = await r.blob();
          const u = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = u; link.download = a.name; link.click();
          URL.revokeObjectURL(u);
        };
        bar.appendChild(btn);
      });

      const pngArt = arts.find(a => a.name === "sheet2d.png") || arts.find(a => a.name.endsWith(".png"));
      const objArt = arts.find(a => a.name.endsWith(".obj"));
      const stage = document.getElementById("previewStage");
      const pane2d = document.getElementById("pane2d");
      const pane3d = document.getElementById("pane3d");

      if (pngArt) {
        const r = await fetch(pngArt.download_url, { headers: authHeaders() });
        const blob = await r.blob();
        const imgUrl = URL.createObjectURL(blob);
        pane2d.innerHTML = `<img src="${imgUrl}" alt="CAD Preview" />`;
      }
      if (objArt) {
        pane3d.style.display = "flex";
        stage.classList.add("split");
        const objTxt = await (await fetch(objArt.download_url, { headers: authHeaders() })).text();
        mesh3d = parseOBJ(objTxt);
        draw3D();
      } else {
        pane3d.style.display = "none";
        stage.classList.remove("split");
      }
    }

    function parseOBJ(txt) {
      const verts = [], faces = [];
      for (const line of txt.split("\\n")) {
        const p = line.trim().split(/\\s+/);
        if (p[0] === "v") verts.push([+p[1], +p[2], +p[3]]);
        else if (p[0] === "f") faces.push(p.slice(1, 4).map(tok => parseInt(tok.split("/")[0], 10) - 1));
      }
      if (!verts.length) return null;
      let minX=1e9,minY=1e9,minZ=1e9,maxX=-1e9,maxY=-1e9,maxZ=-1e9;
      for (const [x,y,z] of verts) {
        if(x<minX)minX=x; if(y<minY)minY=y; if(z<minZ)minZ=z;
        if(x>maxX)maxX=x; if(y>maxY)maxY=y; if(z>maxZ)maxZ=z;
      }
      const cx=(minX+maxX)/2, cy=(minY+maxY)/2, cz=(minZ+maxZ)/2;
      const span = Math.max(maxX-minX, maxY-minY, maxZ-minZ, 1);
      return { verts: verts.map(([x,y,z]) => [(x-cx)/span, (y-cy)/span, (z-cz)/span]), faces };
    }

    function draw3D() {
      const cv = document.getElementById("viewer3d");
      const ctx = cv.getContext("2d");
      ctx.clearRect(0, 0, cv.width, cv.height);
      if (!mesh3d) return;
      const cx = cv.width / 2, cy = cv.height / 2, sc = Math.min(cx, cy) * 1.45 * zoom3d;
      const cy1 = Math.cos(rotY), sy1 = Math.sin(rotY), cx1 = Math.cos(rotX), sx1 = Math.sin(rotX);
      const proj = mesh3d.verts.map(([x, y, z]) => {
        const x1 = x * cy1 + z * sy1;
        const z1 = -x * sy1 + z * cy1;
        const y2 = y * cx1 - z1 * sx1;
        const z2 = y * sx1 + z1 * cx1;
        return [cx + x1 * sc, cy - y2 * sc, z2, x1, y2];
      });
      const tris = mesh3d.faces.map(([a, b, c]) => {
        const pa = proj[a], pb = proj[b], pc = proj[c];
        const ux = pb[3]-pa[3], uy = pb[4]-pa[4], uz = pb[2]-pa[2];
        const vx = pc[3]-pa[3], vy = pc[4]-pa[4], vz = pc[2]-pa[2];
        let nx = uy*vz - uz*vy, ny = uz*vx - ux*vz, nz = ux*vy - uy*vx;
        const nl = Math.hypot(nx, ny, nz) || 1;
        nx/=nl; ny/=nl; nz/=nl;
        const shade = Math.max(0.22, Math.abs(nx*0.35 + ny*0.45 + nz*0.82));
        return { pa, pb, pc, depth: (pa[2]+pb[2]+pc[2])/3, shade };
      }).sort((m, n) => m.depth - n.depth);

      for (const t of tris) {
        const g = Math.round(55 + t.shade * 165);
        ctx.fillStyle = `rgb(${Math.round(g*0.58)}, ${Math.round(g*0.82)}, ${g})`;
        ctx.strokeStyle = `rgba(15,23,42,0.28)`;
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(t.pa[0], t.pa[1]);
        ctx.lineTo(t.pb[0], t.pb[1]);
        ctx.lineTo(t.pc[0], t.pc[1]);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
      ctx.fillStyle = "#94a3b8";
      ctx.font = "12px sans-serif";
      ctx.fillText("三维交互视图 (拖拽旋转 / 滚轮缩放)", 12, 22);
    }

    const cv3 = document.getElementById("viewer3d");
    cv3.onmousedown = e => { dragging = true; lastX = e.clientX; lastY = e.clientY; };
    window.onmouseup = () => { dragging = false; };
    window.onmousemove = e => {
      if (!dragging) return;
      rotY += (e.clientX - lastX) * 0.01;
      rotX += (e.clientY - lastY) * 0.01;
      lastX = e.clientX; lastY = e.clientY;
      draw3D();
    };
    cv3.onwheel = e => { e.preventDefault(); zoom3d = Math.max(0.4, Math.min(3.0, zoom3d * (e.deltaY < 0 ? 1.1 : 0.9))); draw3D(); };

    document.getElementById("btnCompile").onclick = async () => {
      showAlert("");
      try {
        const r = await fetch("/api/v1/tasks/compile", {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify({ markdown_text: document.getElementById("inputEditor").value })
        });
        const d = await r.json();
        if (d.validation && ((d.validation.missing||[]).length || (d.validation.conflicts||[]).length)) {
          showAlert(`检测到待确认参数：\\n缺失: ${(d.validation.missing||[]).join(", ")}\\n冲突: ${(d.validation.conflicts||[]).join(", ")}`);
        } else {
          showAlert(`语义编译通过！识别图幅比例 ${d.spec?.sheet?.scale || "1:1"}，零缺失零冲突。`);
        }
      } catch (e) { showAlert(String(e), true); }
    };

    document.getElementById("btnGenerateSync").onclick = async () => {
      showAlert("正在自动生成并审计图纸...");
      try {
        const body = buildGenerateBody();
        const r = await fetch("/api/v1/tasks/generate", { method: "POST", headers: authHeaders(), body: JSON.stringify(body) });
        const d = await r.json();
        if (!r.ok) { showAlert(d.error || d.detail || JSON.stringify(d), true); return; }
        showAlert("");
        await renderResult(d);
      } catch (e) { showAlert(String(e), true); }
    };

    async function refreshJobs() {
      const r = await fetch("/api/v1/jobs", { headers: authHeaders() });
      if (!r.ok) return;
      const d = await r.json();
      const box = document.getElementById("jobsList");
      box.innerHTML = "";
      (d.jobs || []).forEach(j => {
        const row = document.createElement("div");
        row.className = "job-item";
        row.innerHTML = `<span>${j.job_id} (${j.domain})</span><span class="badge ${j.status==='completed'?'ok':''}">${j.status}</span>`;
        row.onclick = async () => {
          const jr = await (await fetch(`/api/v1/jobs/${j.job_id}`, { headers: authHeaders() })).json();
          if (jr.result) await renderResult(jr.result);
        };
        box.appendChild(row);
      });
    }

    document.getElementById("btnSubmitAsync").onclick = async () => {
      try {
        const body = buildGenerateBody();
        const r = await fetch("/api/v1/jobs", { method: "POST", headers: authHeaders(), body: JSON.stringify(body) });
        const d = await r.json();
        if (!r.ok) { showAlert(d.error || d.detail || JSON.stringify(d), true); return; }
        showAlert(`已提交后台子进程任务：${d.job_id}（状态自动刷新中）`);
        await refreshJobs();
        const timer = setInterval(async () => {
          const jr = await (await fetch(`/api/v1/jobs/${d.job_id}`, { headers: authHeaders() })).json();
          await refreshJobs();
          if (["completed", "failed", "cancelled"].includes(jr.status)) {
            clearInterval(timer);
            if (jr.status === "completed" && jr.result) { showAlert(""); await renderResult(jr.result); }
            else showAlert(`后台任务结束：${jr.status} ${jr.error || ""}`, jr.status !== "completed");
          }
        }, 800);
      } catch (e) { showAlert(String(e), true); }
    };

    document.getElementById("btnRefreshJobs").onclick = refreshJobs;
    document.querySelectorAll(".tab-btn").forEach(b => b.onclick = () => setDomain(b.dataset.domain));
    setDomain("mechanical_2d");
    loadStatus();
    refreshJobs();
  </script>
</body>
</html>
"""
