// kb-intake web UI: four views (submit, run, page review, packages) and the configuration page.
// Plain JavaScript, no build step; every value shown comes from the HTTP API.
"use strict";

const $view = document.getElementById("view");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtMs = (ms) => (ms == null ? "" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
let timer = null;

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((body && body.detail) || `${r.status} ${r.statusText}`);
  return body;
}

function pill(text, cls) { return `<span class="pill ${cls || ""}">${esc(text)}</span>`; }
function statusPill(s) {
  const cls = { done: "ok", ok: "ok", success: "ok", running: "warn", queued: "warn", failed: "bad", interrupted: "bad",
                refused: "bad", skipped_same_sha: "muted", skipped_no_declaration: "warn" }[s] || "";
  return pill(s, cls);
}
function gapsLine(gaps) {
  const e = Object.entries(gaps || {});
  return e.length ? e.map(([k, v]) => `${esc(k)} ${v}`).join(", ") : '<span class="muted">none</span>';
}

// ---------- engine badge ----------
async function engineBadge() {
  const el = document.getElementById("engine");
  try {
    const e = await api("/v1/engines");
    el.className = "pill " + (e.ready ? "ok" : "bad");
    el.textContent = `${e.layout.name} ${e.layout.version} ${e.ready ? "ready" : "not ready"}`;
    el.title = e.ready ? "" : e.layout.problems.join("\n");
  } catch (err) { el.className = "pill bad"; el.textContent = "engine: " + err.message; }
}

// ---------- 1. submit ----------
const DECL_TEMPLATE = `# One file: a plain mapping. A directory: defaults + files (overrides by file name).
defaults:
  issuer_id: example
  doc_type: annual_report
  period_end: 2025-12-31
  source_url: https://example.com/report.pdf
  completeness: {full: true, sections: null}
  # lang: en          # optional; detected when omitted
  # doc_id: ...       # optional in a directory or upload; defaults to the file name's slug
files: {}
`;

function viewNew() {
  $view.innerHTML = `
    <h1>Submit</h1>
    <div class="panel">
      <div class="tabs"><button id="t-up" class="on">Upload files</button><button id="t-dir">A directory on this machine</button></div>
      <div id="p-up"><input type="file" id="files" accept="application/pdf" multiple></div>
      <div id="p-dir" hidden><input type="text" id="dir" placeholder="/path/inside/a/scan/root">
        <label><input type="checkbox" id="recursive"> include subdirectories</label>
        <div id="browse" class="scroll muted"></div></div>
      <h2>Declaration (YAML)</h2>
      <textarea id="decl">${esc(DECL_TEMPLATE)}</textarea>
      <div class="row" style="margin-top:10px">
        <label><input type="checkbox" id="replace"> rebuild packages that already exist</label>
        <button id="go">Start</button><span id="msg" class="muted"></span>
      </div>
    </div>`;
  let mode = "up";
  const tab = (m) => { mode = m; document.getElementById("t-up").classList.toggle("on", m === "up");
    document.getElementById("t-dir").classList.toggle("on", m === "dir");
    document.getElementById("p-up").hidden = m !== "up"; document.getElementById("p-dir").hidden = m !== "dir"; };
  document.getElementById("t-up").onclick = () => tab("up");
  document.getElementById("t-dir").onclick = () => { tab("dir"); browse(null); };
  async function browse(path) {
    const el = document.getElementById("browse");
    try {
      const r = await api("/v1/directories" + (path ? `?path=${encodeURIComponent(path)}` : ""));
      const items = r.entries ? r.entries : r.roots.map((p) => ({ name: p, path: p, kind: "directory" }));
      el.innerHTML = `<p>${r.path ? esc(r.path) : "Scan roots"}</p>` + items.map((e) =>
        `<div><a href="#" data-path="${esc(e.path)}" data-kind="${e.kind}">${e.kind === "directory" ? "▸ " : ""}${esc(e.name)}</a></div>`).join("");
      for (const a of el.querySelectorAll("[data-path]")) a.onclick = (ev) => {
        ev.preventDefault(); document.getElementById("dir").value = a.dataset.path;
        if (a.dataset.kind === "directory") browse(a.dataset.path);
      };
    } catch (err) { el.innerHTML = `<p class="warn">${esc(err.message)}</p>`; }
  }
  document.getElementById("go").onclick = async () => {
    const msg = document.getElementById("msg");
    const decl = document.getElementById("decl").value, replace = document.getElementById("replace").checked;
    try {
      let r;
      if (mode === "up") {
        const files = document.getElementById("files").files;
        if (!files.length) throw new Error("choose at least one PDF");
        const fd = new FormData();
        for (const f of files) fd.append("files", f);
        fd.append("declaration", decl); fd.append("replace", replace);
        r = await api("/v1/jobs", { method: "POST", body: fd });
      } else {
        r = await api("/v1/jobs", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ directory: document.getElementById("dir").value, declaration: decl, replace,
                                 recursive: document.getElementById("recursive").checked }) });
      }
      location.hash = `#/jobs/${r.job_id}`;
    } catch (err) { msg.className = "bad"; msg.textContent = err.message; }
  };
}

// ---------- 2. runs ----------
async function viewJobs() {
  const { jobs } = await api("/v1/jobs");
  $view.innerHTML = `<h1>Runs</h1><div class="panel"><table>
    <tr><th>Job</th><th>Status</th><th>Input</th><th>Started</th><th>Finished</th></tr>
    ${jobs.map((j) => `<tr><td><a href="#/jobs/${esc(j.job_id)}">${esc(j.job_id)}</a></td><td>${statusPill(j.status)}</td>
      <td>${esc(j.input)}</td><td>${esc(j.started_at || "")}</td><td>${esc(j.finished_at || "")}</td></tr>`).join("")}
    </table>${jobs.length ? "" : '<p class="muted">No runs yet.</p>'}</div>`;
}

const STEP_ORDER = ["detect", "text_layer", "tables_secondary", "layout", "postprocess", "package"];

function stepInfo(step, ev) {
  const i = ev.info || {};
  if (step === "detect" && ev.status === "done")
    return `${i.pages} pages, flow ${esc(i.flow)}, lang ${esc(i.lang)}, kinds ${esc(JSON.stringify(i.page_kinds || {}))}`;
  if (step === "text_layer" && ev.status === "done") return `${i.words} words`;
  if (step === "tables_secondary" && ev.status === "done") return `${i.tables} find_tables candidates`;
  if (step === "layout") return ev.status === "done"
    ? `${esc(i.status)}${i.cached ? " (cache hit)" : ""}${i.seconds != null ? `, engine ${i.seconds} s` : ""}` +
      `${(i.failed_pages || []).length ? `, failed pages ${esc(i.failed_pages.join(","))}` : ""}` +
      `${(i.errors || []).length ? ` — ${esc(i.errors[0])}` : ""}` : `${esc(i.engine)} running`;
  if (step === "postprocess" && ev.status === "done") {
    const s = i.summary || {};
    return `${s.tables_primary} primary / ${s.tables_secondary} secondary tables; checks: ${gapsLine(s.gaps)}` +
      (i.postprocess_error ? ` — <span class="bad">${esc(i.postprocess_error)}</span>` : "");
  }
  if (step === "package" && ev.status === "done") return `${esc(i.package)}${i.degraded ? " (degraded)" : ""}`;
  return "";
}

async function viewJob(id) {
  const j = await api(`/v1/jobs/${id}`);
  const files = {};
  for (const e of j.events) {
    if (!e.file) continue;
    (files[e.file] ||= {})[e.step] = e;
  }
  const results = Object.fromEntries(j.results.filter((r) => r.file).map((r) => [r.file, r]));
  const pkgs = Object.fromEntries(j.packages.map((p) => [p.package, p]));
  const fileNames = [...new Set([...Object.keys(files), ...Object.keys(results)])];
  $view.innerHTML = `<h1>Run ${esc(j.job_id)} ${statusPill(j.status)}</h1>
    <p class="muted">${esc(j.kind)}: ${esc(j.input)}${j.error ? ` — <span class="bad">${esc(j.error)}</span>` : ""}</p>
    ${fileNames.map((f) => {
      const r = results[f], steps = files[f] || {}, p = r && r.package ? pkgs[r.package] : null;
      return `<div class="panel"><div class="row"><b>${esc(f)}</b>${r ? statusPill(r.status) : statusPill("running")}
        ${p ? `<a href="#/review/${esc(p.package)}/1">review pages</a> <a href="/v1/packages/${esc(p.package)}">download zip</a>
              ${pill("contract " + (p.check_ok ? "ok" : "failed"), p.check_ok ? "ok" : "bad")}` : ""}
        ${r && r.message ? `<span class="bad">${esc(r.message)}</span>` : ""}
        ${r && r.missing ? `<span class="warn">declaration lacks ${esc(r.missing.join(", "))}</span>` : ""}</div>
        <table class="steps"><tr><th>Step</th><th>Status</th><th>Time</th><th>Result</th></tr>
        ${STEP_ORDER.filter((s) => steps[s]).map((s) => `<tr><td>${s}</td><td>${statusPill(steps[s].status)}</td>
          <td>${fmtMs(steps[s].info.duration_ms)}</td><td>${stepInfo(s, steps[s])}</td></tr>`).join("")}</table>
        ${p ? `<p>Gaps: ${gapsLine(p.gaps)}</p>` : ""}</div>`;
    }).join("")}
    ${j.results.filter((r) => r.status === "unused_overrides").map((r) =>
      `<div class="panel warn">Declaration overrides that matched no file: ${esc(r.files.join(", "))}</div>`).join("")}`;
  if (j.status === "running" || j.status === "queued") timer = setTimeout(() => route(), 1500);
}

// ---------- 3. page review ----------
const TYPE_COLOR = { text: "#7a7a72", heading: "#c2410c", title: "#c2410c", list: "#6d28d9", caption: "#0e7490",
  footnote: "#64748b", page_header: "#94a3b8", page_footer: "#94a3b8", formula: "#a16207", toc: "#4d7c0f",
  table: "#1d4ed8", picture: "#be185d", chart: "#db2777", diagram: "#9333ea", unknown: "#000000" };
const CELL_COLOR = { text: "#15803d", words: "#ca8a04", cell: "#dc2626" };
const layers = { regions: true, cells: true, grid: false, secondary: false, words: false, headings: true };

function rect(b, color, cls, title, id) {
  return `<rect ${id ? `data-id="${esc(id)}"` : ""} class="${cls || ""}" x="${b[0]}" y="${b[1]}" width="${Math.max(0.5, b[2] - b[0])}"
    height="${Math.max(0.5, b[3] - b[1])}" stroke="${color}" fill="${color}"><title>${esc(title)}</title></rect>`;
}

function overlay(p) {
  const out = [];
  if (layers.regions) for (const r of p.regions)
    out.push(rect(r.bbox, TYPE_COLOR[r.type] || "#000", "", `${r.region_id} ${r.type} (${r.type_source})`, r.region_id),
             `<text x="${r.bbox[0] + 1}" y="${r.bbox[1] + 8}" fill="${TYPE_COLOR[r.type] || "#000"}">${esc(r.type)}</text>`);
  for (const t of p.tables) {
    const secondary = t.role === "secondary";
    if (secondary && !layers.secondary) continue;
    out.push(rect(t.bbox, secondary ? "#f59e0b" : "#1d4ed8", "", `${t.table_id} ${t.extract_method} (${t.role})`, t.table_id));
    for (const c of t.cells) {
      if (layers.grid && c.grid_bbox) out.push(rect(c.grid_bbox, "#9ca3af", "cell", `grid r${c.r} c${c.c}`));
      if (layers.cells && c.bbox) out.push(rect(c.bbox, secondary ? "#f59e0b" : CELL_COLOR[c.bbox_source] || "#15803d",
        "cell", `r${c.r} c${c.c}${c.is_header ? " header" : ""} [${c.bbox_source}]: ${c.text ?? ""}`));
    }
  }
  if (layers.headings) for (const h of p.headings)
    out.push(rect(h.bbox, h.furniture ? "#94a3b8" : "#c2410c", "cell", `L${h.level} ${h.furniture ? "furniture " : ""}${h.text}`));
  if (layers.words) for (const w of p.words) out.push(rect(w, "#64748b", "cell", w[4]));
  return out.join("");
}

function gapBox(g) { return g.bbox || null; }

async function viewReview(name, n) {
  n = Number(n) || 1;
  const p = await api(`/v1/packages/${name}/pages/${n}`);
  const primary = p.tables.filter((t) => t.role === "primary"), secondary = p.tables.filter((t) => t.role === "secondary");
  const sources = {};
  for (const t of primary) for (const c of t.cells) sources[c.bbox_source || "empty"] = (sources[c.bbox_source || "empty"] || 0) + 1;
  $view.innerHTML = `
    <div class="row"><h1 style="margin-right:auto">${esc(name)} · page ${n} / ${p.page_count}
      ${p.page_label ? `<span class="muted">(printed ${esc(p.page_label)})</span>` : ""}</h1>
      <button class="secondary" id="prev" ${n <= 1 ? "disabled" : ""}>‹ prev</button>
      <input type="text" id="goto" value="${n}" style="width:70px">
      <button class="secondary" id="next" ${n >= p.page_count ? "disabled" : ""}>next ›</button>
      <button class="secondary" id="nextgap">next page with gaps</button></div>
    <div class="review">
      <div>
        <div class="panel layers">${Object.keys(layers).map((k) =>
          `<label><input type="checkbox" data-layer="${k}" ${layers[k] ? "checked" : ""}> ${k}</label>`).join("")}
          <div class="legend">${Object.entries(TYPE_COLOR).filter(([k]) => p.regions.some((r) => r.type === k))
            .map(([k, c]) => `<span><i style="border-color:${c}"></i>${k}</span>`).join("")}
            · cell box: ${Object.entries(CELL_COLOR).map(([k, c]) => `<span><i style="border-color:${c}"></i>${k}</span>`).join("")}</div></div>
        <div class="page"><img src="${esc(p.image)}" alt="page ${n}">
          <svg id="ov" viewBox="0 0 ${p.width} ${p.height}" preserveAspectRatio="none">${overlay(p)}</svg></div>
      </div>
      <div>
        <div class="panel"><div class="row">${pill(p.kind || "kind?")}
          ${pill("docling: " + (p.docling_confidence ?? "n/a"))}
          ${pill(`${p.regions.length} regions`)}${pill(`${primary.length} tables`)}${pill(`${p.gaps.length} gaps`, p.gaps.length ? "warn" : "ok")}</div>
          ${p.document_gaps.length ? `<p class="bad">Document: ${p.document_gaps.map((g) => esc(`${g.code} ${g.extractor || ""} ${g.status || ""}`)).join("; ")}</p>` : ""}</div>
        <div class="panel"><h2>Gaps on this page</h2><div class="scroll">
          ${p.gaps.length ? `<table>${p.gaps.map((g, i) => `<tr class="gap" data-gap="${i}"><td>${esc(g.code)}</td>
            <td>${esc(g.table_id || g.ref || "")}</td><td>${esc(g.text || g.resolution || g.source || g.detail || "")}
            ${g.only_engine ? `<div class="muted">engine only: ${esc(g.only_engine.join(" "))}; find_tables only: ${esc(g.only_find_tables.join(" "))}</div>` : ""}
            ${g.engine !== undefined ? `<div class="muted">engine: ${esc(g.engine)} / text layer: ${esc(g.text_layer)}</div>` : ""}</td></tr>`).join("")}</table>`
            : '<p class="muted">none</p>'}</div></div>
        <div class="panel"><h2>Tables</h2>
          ${primary.map((t) => `<p><b>${esc(t.table_id)}</b> ${pill(t.extract_method)} ${t.n_rows}×${t.n_cols}
            ${t.pages.length > 1 ? pill("pages " + t.pages.join("–")) : ""}
            ${t.checks.text_conservation ? pill(`conservation −${t.checks.text_conservation.missing} +${t.checks.text_conservation.extra}`,
              t.checks.text_conservation.missing || t.checks.text_conservation.extra ? "warn" : "ok") : ""}
            ${t.context_above.length ? `<br><span class="muted">above: ${esc(t.context_above.map((c) => c.text).join(" · "))}</span>` : ""}</p>`).join("") || '<p class="muted">none</p>'}
          ${secondary.length ? `<p class="muted">${secondary.length} find_tables candidate(s) kept for cross-checks (layer "secondary")</p>` : ""}
          <p class="muted">cell boxes: ${esc(Object.entries(sources).map(([k, v]) => `${k} ${v}`).join(", ") || "none")}</p></div>
        <div class="panel"><h2>Text layer</h2><pre>${esc(p.text)}</pre></div>
      </div>
    </div>`;
  const go = (k) => { location.hash = `#/review/${name}/${k}`; };
  document.getElementById("prev").onclick = () => go(n - 1);
  document.getElementById("next").onclick = () => go(n + 1);
  document.getElementById("goto").onchange = (e) => go(Math.min(p.page_count, Math.max(1, Number(e.target.value) || 1)));
  document.getElementById("nextgap").onclick = () => { const k = p.pages_with_gaps.find((x) => x > n); if (k) go(k); };
  for (const cb of document.querySelectorAll("[data-layer]"))
    cb.onchange = () => { layers[cb.dataset.layer] = cb.checked; document.getElementById("ov").innerHTML = overlay(p); };
  for (const tr of document.querySelectorAll("[data-gap]")) tr.onclick = () => {
    const g = p.gaps[Number(tr.dataset.gap)], ov = document.getElementById("ov");
    ov.innerHTML = overlay(p);
    const box = gapBox(g);
    if (box) ov.insertAdjacentHTML("beforeend", rect(box, "#dc2626", "hit", g.code));
    const target = g.table_id && ov.querySelector(`[data-id="${CSS.escape(g.table_id)}"]`);
    if (target) target.classList.add("hit");
  };
}

// ---------- 4. packages ----------
async function viewPackages() {
  const { packages } = await api("/v1/packages");
  $view.innerHTML = `<h1>Packages</h1>
    <div class="panel"><table><tr><th>Package</th><th>Source</th><th>Pages</th><th>Layout</th><th>Tables</th><th>Gaps</th><th>Contract</th><th></th></tr>
    ${packages.map((p) => p.error ? `<tr><td>${esc(p.package)}</td><td colspan="7" class="bad">${esc(p.error)}</td></tr>` :
      `<tr><td>${esc(p.package)}</td><td>${esc(p.source)}</td><td>${p.pages}</td>
      <td>${statusPill(p.layout_status || "n/a")}${p.degraded ? " " + pill("degraded", "bad") : ""}</td><td>${p.tables ?? ""}</td>
      <td>${gapsLine(p.gaps)}</td><td>${pill(p.check_ok ? "ok" : "failed", p.check_ok ? "ok" : "bad")}</td>
      <td><a href="#/review/${esc(p.package)}/1">review</a> · <a href="#/manifest/${esc(p.package)}">manifest</a> ·
          <a href="/v1/packages/${esc(p.package)}">zip</a></td></tr>`).join("")}</table>
    ${packages.length ? "" : '<p class="muted">No packages yet.</p>'}</div>
    <div class="panel"><h2>Check a package zip</h2><div class="row"><input type="file" id="zip" accept=".zip">
      <button id="check">Check</button></div><pre id="checkout" hidden></pre></div>`;
  document.getElementById("check").onclick = async () => {
    const out = document.getElementById("checkout"), f = document.getElementById("zip").files[0];
    if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    out.hidden = false;
    try { out.textContent = JSON.stringify(await api("/v1/packages/check", { method: "POST", body: fd }), null, 1); }
    catch (err) { out.textContent = err.message; }
  };
}

async function viewManifest(name) {
  const m = await api(`/v1/packages/${name}/manifest`);
  $view.innerHTML = `<h1>${esc(name)}</h1><p><a href="#/review/${esc(name)}/1">review pages</a> · <a href="/v1/packages/${esc(name)}">download zip</a></p>
    <div class="panel"><h2>Contract check</h2><pre>${esc(JSON.stringify(m.checks, null, 1))}</pre></div>
    <div class="panel"><h2>Steps</h2><table><tr><th>Step</th><th>Tool</th><th>Version</th><th>Status</th></tr>
      ${m.manifest.steps.map((s) => `<tr><td>${esc(s.step)}</td><td>${esc(s.tool)}</td><td>${esc(s.version)}</td><td>${esc(s.status || "")}</td></tr>`).join("")}</table></div>
    <div class="panel"><h2>manifest.json</h2><pre>${esc(JSON.stringify(m.manifest, null, 1))}</pre></div>`;
}

// ---------- configuration ----------
async function viewConfig() {
  const c = await api("/v1/config");
  $view.innerHTML = `<h1>Configuration</h1>
    <div class="panel"><p class="muted">${esc(c.path)} — every key is optional; unknown keys are refused.
      Environment: ${esc(Object.entries(c.environment).map(([k, v]) => `${k}=${v ?? "(unset)"}`).join(", "))}</p>
      <textarea id="yaml">${esc(c.yaml)}</textarea>
      <div class="row" style="margin-top:10px"><button id="save">Save</button><button class="secondary" id="test">Test engine</button>
        <span id="msg"></span></div></div>
    <div class="panel"><h2>Effective</h2><pre id="eff">${esc(JSON.stringify(c.effective, null, 1))}</pre></div>
    <div class="panel"><h2>Engine</h2><pre id="eng">press "Test engine"</pre></div>`;
  const msg = document.getElementById("msg");
  document.getElementById("save").onclick = async () => {
    try {
      const r = await api("/v1/config", { method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ yaml: document.getElementById("yaml").value }) });
      document.getElementById("eff").textContent = JSON.stringify(r.effective, null, 1);
      msg.className = "ok"; msg.textContent = "saved"; engineBadge();
    } catch (err) { msg.className = "bad"; msg.textContent = err.message; }
  };
  document.getElementById("test").onclick = async () => {
    const e = await api("/v1/engines");
    document.getElementById("eng").textContent = JSON.stringify(e, null, 1);
    engineBadge();
  };
}

// ---------- router ----------
async function route() {
  clearTimeout(timer);
  const parts = (location.hash.replace(/^#\/?/, "") || "new").split("/");
  for (const a of document.querySelectorAll("nav a"))
    a.classList.toggle("active", a.dataset.view === (parts[0] === "review" || parts[0] === "manifest" ? "packages" : parts[0]));
  try {
    if (parts[0] === "new") viewNew();
    else if (parts[0] === "jobs" && parts[1]) await viewJob(parts[1]);
    else if (parts[0] === "jobs") await viewJobs();
    else if (parts[0] === "review") await viewReview(parts[1], parts[2]);
    else if (parts[0] === "packages") await viewPackages();
    else if (parts[0] === "manifest") await viewManifest(parts[1]);
    else if (parts[0] === "config") await viewConfig();
    else viewNew();
  } catch (err) { $view.innerHTML = `<div class="panel bad">${esc(err.message)}</div>`; }
}
window.addEventListener("hashchange", route);
engineBadge();
route();
