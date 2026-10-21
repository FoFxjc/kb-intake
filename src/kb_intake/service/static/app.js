// kb-intake web UI (intake-design-v2 section 6): six views on the /v1 API.
//   1 upload          #/upload
//   2 labels & stats  #/job/<id>/labels/<page>
//   3 workflow        #/job/<id>/workflow
//   4 progress        #/job/<id>/progress
//   5 review          #/job/<id>/review/<page>
//   6 delivery        #/packages
// Plus the job list (#/jobs). Plain JavaScript, no build step, no external resources; every value comes from the API.
"use strict";

const $view = document.getElementById("view");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);
let timer = null;

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    const d = body && body.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `${r.status} ${r.statusText}`);
  }
  return body;
}
const post = (path, body) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });

function pill(text, cls) { return `<span class="pill ${cls || ""}">${esc(text)}</span>`; }
const STATE_CLS = { planned: "warn", in_review: "warn", delivered: "ok" };
function statePill(job) {
  if (job.running_op) return pill(`${job.status}: running ${job.running_op}`, "warn");
  return pill(job.status, job.last_error ? "bad" : STATE_CLS[job.status]);
}
function counts(obj) {
  const e = Object.entries(obj || {});
  return e.length ? e.map(([k, v]) => `${esc(k)} ${v}`).join(", ") : '<span class="muted">none</span>';
}

// The reviewer's name, remembered in this browser only (a convenience; the server records who did what).
function savedName() { try { return localStorage.getItem("kb-intake-name") || ""; } catch { return ""; } }
function saveName(v) { try { localStorage.setItem("kb-intake-name", v); } catch { /* storage unavailable */ } }
function nameInput(id) { return `<input type="text" id="${id}" placeholder="your name" value="${esc(savedName())}">`; }
function nameOf(id) {
  const v = $(id).value.trim();
  if (!v) throw new Error("enter your name first");
  saveName(v);
  return v;
}

async function engineBadge() {
  const el = $("engine");
  try {
    const e = await api("/v1/engines");
    const s = e.stages.label;
    el.className = "pill " + (e.ready ? "ok" : "bad");
    el.textContent = `${s.name} ${s.version} ${e.ready ? "ready" : "not ready"}`;
    el.title = e.ready ? "" : Object.values(e.stages).flatMap((i) => i.problems).join("\n");
  } catch (err) { el.className = "pill bad"; el.textContent = "engine: " + err.message; }
}

// ---------- page drawing (views 2 and 5) ----------
const TYPE_COLOR = {
  title: "#c2410c", heading: "#ea580c", text: "#6b7280", list: "#6d28d9", caption: "#0e7490", footnote: "#64748b",
  formula: "#a16207", toc: "#4d7c0f", table: "#1d4ed8", chart: "#db2777", diagram: "#9333ea", picture: "#be185d",
  logo: "#0f766e", icon: "#0d9488", signature: "#7c2d12", stamp: "#991b1b", page_header: "#94a3b8",
  page_footer: "#94a3b8", unknown: "#000000",
};
const CELL_COLOR = "#16a34a", GAP_COLOR = "#dc2626";

function rect(b, color, cls, title, id) {
  return `<rect ${id ? `data-id="${esc(id)}"` : ""} class="${cls || ""}" x="${b[0]}" y="${b[1]}"
    width="${Math.max(0.5, b[2] - b[0])}" height="${Math.max(0.5, b[3] - b[1])}" stroke="${color}" fill="${color}">
    <title>${esc(title)}</title></rect>`;
}

// SVG overlay in PDF points (viewBox = the page size), so the boxes scale with the rendered image.
function overlay(p, opts) {
  const out = [];
  for (const r of p.regions) {
    const c = TYPE_COLOR[r.type] || "#000";
    out.push(rect(r.bbox, c, r.status === "excluded" ? "excluded" : "",
      `${r.region_id} ${r.type}${r.subtype ? "/" + r.subtype : ""}${r.status ? " [" + r.status + "]" : ""}`, r.region_id));
    out.push(`<text x="${r.bbox[0] + 1}" y="${r.bbox[1] + 8}" fill="${c}">${esc(r.type)}</text>`);
  }
  if (opts.cells) for (const t of p.tables || []) for (const cell of t.cells || [])
    if (cell.bbox) out.push(rect(cell.bbox, CELL_COLOR, "cell", `r${cell.r} c${cell.c}: ${cell.text ?? ""}`));
  for (const g of p.gaps || []) if (g.bbox) out.push(rect(g.bbox, GAP_COLOR, "cell", `${g.code}: ${g.detail}`));
  return out.join("");
}

function pageFigure(jobId, p) {
  const used = [...new Set(p.regions.map((r) => r.type))];
  return `<div class="legend">${used.map((t) => `<span><i style="border-color:${TYPE_COLOR[t] || "#000"}"></i>${esc(t)}</span>`).join("")}
      ${(p.tables || []).length ? `<span><i style="border-color:${CELL_COLOR}"></i>cell text box</span>` : ""}
      ${(p.gaps || []).some((g) => g.bbox) ? `<span><i style="border-color:${GAP_COLOR}"></i>gap box</span>` : ""}</div>
    <div class="page"><img src="/v1/jobs/${esc(jobId)}/pages/${p.page_no}/image?dpi=110" alt="page ${p.page_no}">
      <svg id="ov" viewBox="0 0 ${p.width} ${p.height}" preserveAspectRatio="none"></svg></div>`;
}

function highlight(id) {
  for (const el of document.querySelectorAll("#ov rect.hit")) el.classList.remove("hit");
  const el = id && document.querySelector(`#ov rect[data-id="${CSS.escape(id)}"]`);
  if (el) el.classList.add("hit");
}

function pageNav(n, count) {
  return `<span class="row"><button class="secondary small" id="prev" ${n <= 1 ? "disabled" : ""}>prev</button>
    <span>page <input type="text" id="goto" value="${n}" style="width:60px"> / ${count ?? "?"}</span>
    <button class="secondary small" id="next" ${count && n >= count ? "disabled" : ""}>next</button></span>`;
}
function wirePageNav(base, n, count) {
  const go = (k) => { location.hash = `${base}/${Math.max(1, count ? Math.min(count, k) : k)}`; };
  $("prev").onclick = () => go(n - 1);
  $("next").onclick = () => go(n + 1);
  $("goto").onchange = (e) => go(Number(e.target.value) || 1);
}

// ---------- the header of one job: tabs to its views ----------
function jobHeader(d, on) {
  const j = d.job, id = esc(j.id);
  const tabs = [["labels", "Labels & stats", `#/job/${id}/labels/1`], ["workflow", "Workflow", `#/job/${id}/workflow`],
                ["progress", "Progress", `#/job/${id}/progress`], ["review", "Review", `#/job/${id}/review/1`]];
  return `<h1>${esc(j.source_name)} <span class="muted">${esc(j.doc_id)}</span> ${statePill(j)}</h1>
    ${j.last_error ? `<p class="bad">${esc(j.last_error)}</p>` : ""}
    <div class="tabs">${tabs.map(([k, t, h]) => `<a href="${h}" class="${k === on ? "on" : ""}">${t}</a>`).join("")}</div>`;
}

// ---------- 1. upload ----------
const DECL_TEMPLATE = `# doc_id defaults to the file name's slug
issuer_id: example
doc_type: annual_report
period_end: 2025-12-31
source_url: https://example.com/report.pdf
completeness: {full: true}
# lang: en          # optional; detected when omitted
`;

function viewUpload() {
  $view.innerHTML = `<h1>Upload</h1>
    <div class="panel">
      <h2>File</h2>
      <div class="row"><input type="file" id="file" accept="application/pdf">
        <span class="muted">or a path on this machine (inside scan_roots):</span>
        <input type="text" id="path" placeholder="/path/to/report.pdf" style="min-width:320px">
        <button class="secondary small" id="browse">browse</button></div>
      <div id="dirs" class="scroll muted"></div>
      <h2 style="margin-top:14px">Declaration (YAML)</h2>
      <textarea id="decl">${esc(DECL_TEMPLATE)}</textarea>
      <div class="row" style="margin-top:10px"><button id="go">Upload and label</button><span id="msg"></span></div>
    </div>`;
  async function browse(path) {
    const el = $("dirs");
    try {
      const r = await api("/v1/directories" + (path ? `?path=${encodeURIComponent(path)}` : ""));
      const items = r.entries ? r.entries : r.roots.map((p) => ({ name: p, path: p, kind: "directory" }));
      el.innerHTML = `<p>${esc(r.path || "scan roots")}</p>` + items.map((e) =>
        `<div><a href="#" data-path="${esc(e.path)}" data-kind="${e.kind}">${e.kind === "directory" ? "[dir] " : ""}${esc(e.name)}</a></div>`).join("");
      for (const a of el.querySelectorAll("[data-path]")) a.onclick = (ev) => {
        ev.preventDefault();
        if (a.dataset.kind === "directory") browse(a.dataset.path); else $("path").value = a.dataset.path;
      };
    } catch (err) { el.innerHTML = `<p class="warn">${esc(err.message)}</p>`; }
  }
  $("browse").onclick = () => browse(null);
  $("go").onclick = async () => {
    const msg = $("msg");
    msg.className = "muted"; msg.textContent = "uploading...";
    try {
      const file = $("file").files[0], path = $("path").value.trim(), decl = $("decl").value;
      let r;
      if (file) {
        const fd = new FormData();
        fd.append("file", file); fd.append("declaration", decl);
        r = await api("/v1/jobs", { method: "POST", body: fd });
      } else if (path) {
        r = await post("/v1/jobs", { path, declaration: decl });
      } else throw new Error("choose a PDF or give a path");
      location.hash = `#/job/${r.job_id}/progress`;
    } catch (err) { msg.className = "bad"; msg.textContent = err.message; }
  };
}

// ---------- job list ----------
const NEXT_VIEW = { uploaded: "progress", labeled: "labels/1", planned: "labels/1", approved: "progress",
                    extracted: "progress", in_review: "review/1", confirmed: "progress", delivered: "progress" };

async function viewJobs() {
  const { jobs } = await api("/v1/jobs");
  $view.innerHTML = `<h1>Jobs</h1><div class="panel"><table>
    <tr><th>Source</th><th>doc_id</th><th>State</th><th>Updated</th><th>Package</th></tr>
    ${jobs.map((j) => `<tr><td><a href="#/job/${esc(j.job_id)}/${NEXT_VIEW[j.status]}">${esc(j.source_name)}</a>
      <div class="muted">${esc(j.job_id)}</div></td><td>${esc(j.doc_id)}</td>
      <td>${statePill(j)}${j.last_error ? `<div class="bad">${esc(j.last_error)}</div>` : ""}</td>
      <td>${esc(j.updated_at)}</td><td>${esc(j.package || "")}</td></tr>`).join("")}
    </table>${jobs.length ? "" : '<p class="muted">No jobs yet. <a href="#/upload">Upload a file.</a></p>'}</div>`;
}

// ---------- 2. labels & stats ----------
async function viewLabels(id, n) {
  n = Number(n) || 1;
  const d = await api(`/v1/jobs/${id}`);
  const s = d.stats;
  if (!s) {
    $view.innerHTML = jobHeader(d, "labels") + `<div class="panel muted">Not labelled yet. <a href="#/job/${esc(id)}/progress">Progress</a></div>`;
    return;
  }
  const [{ gaps }, p] = await Promise.all([api(`/v1/jobs/${id}/gaps`), api(`/v1/jobs/${id}/pages/${n}`)]);
  const lg = gaps.filter((g) => g.stage === "label");
  const gapRows = (rows) => rows.length ? `<table>${rows.map((g) => `<tr class="click" data-page="${g.page_no || ""}" data-region="${esc(g.region_id || "")}">
      <td>p${g.page_no ?? "-"}</td><td>${esc(g.source || "")}</td><td>${esc(g.detail)}</td></tr>`).join("")}</table>` : '<p class="muted">none</p>';
  const typeRows = Object.entries(s.regions_by_type).map(([t, c]) =>
    `<tr><td><span class="legend"><span><i style="border-color:${TYPE_COLOR[t] || "#000"}"></i>${esc(t)}</span></span></td><td>${c}</td></tr>`).join("");
  $view.innerHTML = jobHeader(d, "labels") + `
    <div class="split">
      <div><div class="panel">${pageNav(n, s.pages)}</div>${pageFigure(id, p)}</div>
      <div>
        <div class="panel"><h2>Stats</h2><table class="kv">
          <tr><td>pages</td><td>${s.pages} (${counts(s.pages_by_kind)})</td></tr>
          <tr><td>regions</td><td>${s.regions}</td></tr>
          <tr><td>low confidence</td><td>${s.low_confidence}</td></tr>
          <tr><td>suspected tables</td><td>${s.table_suspected}</td></tr>
          <tr><td>estimated extraction</td><td>${s.estimated_extract_seconds} s</td></tr>
          <tr><td>label time</td><td>${s.label_seconds} s</td></tr></table>
          ${(s.workflow_notes || []).map((t) => `<p class="warn">${esc(t)}</p>`).join("")}
          ${d.job.status === "planned" ? `<p><a href="#/job/${esc(id)}/workflow">Next: check the workflow and approve it</a></p>` : ""}</div>
        <div class="panel"><h2>Regions by type</h2><table>${typeRows}</table></div>
        <div class="panel"><h2>Suspected tables</h2><div class="scroll">${gapRows(lg.filter((g) => g.code === "table_suspected"))}</div></div>
        <div class="panel"><h2>Low-confidence regions</h2><div class="scroll">${gapRows(lg.filter((g) => g.code === "low_confidence"))}</div></div>
        <div class="panel"><h2>Other label gaps</h2><div class="scroll">${gapRows(lg.filter((g) => !["table_suspected", "low_confidence"].includes(g.code)))}</div></div>
      </div></div>`;
  $("ov").innerHTML = overlay(p, { cells: false });
  wirePageNav(`#/job/${id}/labels`, n, s.pages);
  for (const tr of document.querySelectorAll("tr[data-page]")) tr.onclick = () => {
    const k = Number(tr.dataset.page);
    if (k && k !== n) location.hash = `#/job/${id}/labels/${k}`; else highlight(tr.dataset.region);
  };
}

// ---------- 3. workflow ----------
function workflowYaml(wf) {
  // The layout kb_intake.workflow.dump writes; the name as a JSON string (valid YAML).
  return [`name: ${JSON.stringify(wf.name)}`, `version: ${wf.version}`, "handlers:",
          ...Object.entries(wf.handlers).map(([t, h]) => `  ${t}: ${h}`),
          `exclude_pages: ${JSON.stringify(wf.exclude_pages)}`].join("\n") + "\n";
}

async function viewWorkflow(id) {
  const d = await api(`/v1/jobs/${id}`);
  let w;
  try { w = await api(`/v1/jobs/${id}/workflow`); }
  catch (err) { $view.innerHTML = jobHeader(d, "workflow") + `<div class="panel muted">${esc(err.message)}</div>`; return; }
  const editable = d.job.status === "planned" && !d.job.running_op;
  const wf = w.workflow;
  const byType = (d.stats && d.stats.regions_by_type) || {};
  const dis = editable ? "" : "disabled";
  const options = (t, cur) => w.handlers.filter((h) => h !== "table_structure" || t === "table")
    .map((h) => `<option ${h === cur ? "selected" : ""}>${h}</option>`).join("");
  $view.innerHTML = jobHeader(d, "workflow") + `
    <div class="split"><div class="panel">
      <h2>${esc(wf ? wf.name : "workflow")} <span class="muted">version ${w.version}${w.approved_version ? `; approved version ${w.approved_version} by ${esc(d.job.approved_by)}` : ""}</span></h2>
      ${w.error ? `<p class="bad">${esc(w.error)}</p>` : ""}
      ${(w.notes || []).map((t) => `<p class="warn">${esc(t)}</p>`).join("")}
      ${wf ? `<table><tr><th>Region type</th><th>Regions</th><th>Handler</th></tr>
        ${w.types.map((t) => `<tr><td>${esc(t)}</td><td>${byType[t] || ""}</td>
          <td><select data-type="${t}" ${dis}>${options(t, wf.handlers[t])}</select></td></tr>`).join("")}
      </table>
      <p>Exclude pages (comma separated): <input type="text" id="exclude" value="${esc(wf.exclude_pages.join(", "))}" ${dis}></p>` : ""}
      <div class="row">${nameInput("by")}<button id="approve" ${wf ? dis : "disabled"}>Approve and extract</button>
        <button class="secondary" id="save" ${wf ? dis : "disabled"}>Save draft</button><span id="msg"></span></div>
    </div>
    <div class="panel"><h2>YAML</h2><textarea id="yaml" ${dis}>${esc(w.text)}</textarea>
      <div class="row"><button class="secondary" id="saveyaml" ${dis}>Save YAML as a new version</button></div>
      <p class="muted">Handlers: text (text layer by box), table_structure (TableFormer rows and columns, cell text from the
      text layer), record (position only), discard, unhandled (a gap), ocr (not implemented: unhandled).</p></div></div>`;
  if (!editable) return;
  const msg = $("msg");
  const fail = (err) => { msg.className = "bad"; msg.textContent = err.message; };
  const fromForm = () => {
    const pages = $("exclude").value.split(/[\s,]+/).filter(Boolean).map(Number);
    if (pages.some((x) => !Number.isInteger(x) || x < 1)) throw new Error("exclude pages must be page numbers");
    const handlers = {};
    for (const sel of document.querySelectorAll("select[data-type]")) handlers[sel.dataset.type] = sel.value;
    return workflowYaml({ ...wf, handlers, exclude_pages: pages });
  };
  const put = (text) => api(`/v1/jobs/${id}/workflow`, { method: "PUT", headers: { "content-type": "text/yaml" }, body: text });
  $("saveyaml").onclick = async () => { try { await put($("yaml").value); route(); } catch (err) { fail(err); } };
  if (!wf) return;
  $("save").onclick = async () => { try { await put(fromForm()); route(); } catch (err) { fail(err); } };
  $("approve").onclick = async () => {
    try {
      const by = nameOf("by");
      const text = fromForm();
      let version = w.version;
      if (text !== workflowYaml(wf)) version = (await put(text)).version;
      await post(`/v1/jobs/${id}/approve`, { by, version });
      location.hash = `#/job/${id}/progress`;
    } catch (err) { fail(err); }
  };
}

// ---------- 4. progress ----------
function eventInfo(e) {
  return esc(Object.entries(e.info || {}).map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", "));
}

async function viewProgress(id) {
  const [d, { events }] = await Promise.all([api(`/v1/jobs/${id}`), api(`/v1/jobs/${id}/events`)]);
  const j = d.job;
  const retry = !j.running_op && !d.busy && ["uploaded", "approved", "confirmed"].includes(j.status);   // what /retry accepts
  const next = { planned: ["labels/1", "labels & stats, then approve the workflow"], in_review: ["review/1", "review"] }[j.status];
  $view.innerHTML = jobHeader(d, "progress") + `
    <div class="panel"><div class="row">
      ${retry ? '<button id="retry">Retry the failed step</button>' : ""}
      ${next ? `<a href="#/job/${esc(id)}/${next[0]}">Next: ${next[1]}</a>` : ""}
      ${j.status === "delivered" ? `<span>Delivered as <b>${esc(j.package)}</b>. <a href="#/packages">Delivery folder</a></span>` : ""}
      ${d.extract ? `<span class="muted">extraction ${d.extract.seconds} s, Docling ${esc(d.extract.engine.status)}${d.extract.engine.cached ? " (cached)" : ""}</span>` : ""}
      <span id="msg"></span></div></div>
    <div class="panel"><table><tr><th>#</th><th>At</th><th>Step</th><th>Status</th><th>Info</th></tr>
      ${events.map((e) => `<tr><td>${e.seq}</td><td class="muted">${esc(e.at)}</td><td>${esc(e.step)}</td>
        <td>${pill(e.status, e.status === "done" ? "ok" : e.status === "failed" ? "bad" : "warn")}</td><td>${eventInfo(e)}</td></tr>`).join("")}
    </table>${events.length ? "" : '<p class="muted">No events yet.</p>'}</div>`;
  if (retry) $("retry").onclick = async () => {
    try { await post(`/v1/jobs/${id}/retry`); route(); } catch (err) { $("msg").className = "bad"; $("msg").textContent = err.message; }
  };
  if (j.running_op || d.busy) timer = setTimeout(route, 1500);
}

// ---------- 5. review ----------
async function viewReview(id, n) {
  n = Number(n) || 1;
  const d = await api(`/v1/jobs/${id}`);
  const j = d.job;
  if (!["in_review", "confirmed", "delivered"].includes(j.status)) {
    $view.innerHTML = jobHeader(d, "review") + `<div class="panel muted">Review starts after extraction (the job is ${esc(j.status)}).</div>`;
    return;
  }
  const [p, w] = await Promise.all([api(`/v1/jobs/${id}/pages/${n}`), api(`/v1/jobs/${id}/workflow`)]);
  const open = j.status === "in_review" && !j.running_op;
  const count = d.stats && d.stats.pages;
  const can = (r) => (open && r.status !== "excluded" ? "" : "disabled");
  const typeSelect = (r) => `<select data-to="${esc(r.region_id)}" ${can(r)}>
    ${w.types.map((t) => `<option ${t === r.type ? "selected" : ""}>${t}</option>`).join("")}</select>`;
  $view.innerHTML = jobHeader(d, "review") + `
    <div class="split">
      <div><div class="panel"><div class="row">${pageNav(n, count)}
        ${pill(p.kind || "kind?")}${p.page_label ? pill("printed " + p.page_label) : ""}${p.excluded ? pill("excluded page", "bad") : ""}
        <label><input type="checkbox" id="cells" checked> table cells</label></div></div>
        ${pageFigure(id, p)}</div>
      <div>
        <div class="panel"><h2>Confirm</h2><div class="row">${nameInput("by")}
          <button id="confirm" ${open ? "" : "disabled"}>Confirm and deliver</button><span id="msg"></span></div>
          <p class="muted">Confirming freezes the package and writes it to the delivery folder. Content cannot be edited:
          reclassify or exclude regions; problems with the content stay as gaps for the consumer.</p></div>
        <div class="panel"><h2>Regions on this page</h2><div class="scroll"><table>
          <tr><th>Region</th><th>Type</th><th>Status</th><th></th></tr>
          ${p.regions.map((r) => `<tr class="click" data-region="${esc(r.region_id)}">
            <td>${esc(r.region_id.split(":").pop())}<div class="excerpt">${esc(r.text || "")}</div></td>
            <td>${typeSelect(r)}${r.subtype ? ` <span class="muted">${esc(r.subtype)}</span>` : ""}
              <div class="muted">${esc(r.label_source || "")}${r.score != null ? " " + Number(r.score).toFixed(2) : ""}</div></td>
            <td>${esc(r.status || "")}${r.table_id ? `<div class="muted">${esc(r.table_id.split(":").pop())}</div>` : ""}</td>
            <td><button class="small" data-reclassify="${esc(r.region_id)}" ${can(r)}>reclassify</button>
              <button class="small secondary" data-exclude="${esc(r.region_id)}" ${can(r)}>exclude</button></td></tr>`).join("")}
        </table></div></div>
        <div class="panel"><h2>Gaps on this page</h2>
          ${(p.gaps || []).length ? `<table>${p.gaps.map((g) => `<tr class="click" data-region="${esc(g.region_id || "")}">
            <td>${esc(g.code)}</td><td>${esc(g.detail)}</td></tr>`).join("")}</table>` : '<p class="muted">none</p>'}</div>
        <div class="panel"><h2>Tables</h2>
          ${(p.tables || []).map((t) => `<p><b>${esc(t.table_id)}</b> ${pill(t.extract_method)} ${t.n_rows} x ${t.n_cols}
            ${t.continued_hint ? pill("continues " + t.continued_hint.split(":").pop()) : ""}</p><pre>${esc(t.markdown || "")}</pre>`).join("")
            || '<p class="muted">none</p>'}</div>
      </div></div>`;
  const draw = () => { $("ov").innerHTML = overlay(p, { cells: $("cells").checked }); };
  draw();
  $("cells").onchange = draw;
  wirePageNav(`#/job/${id}/review`, n, count);
  const msg = $("msg");
  const fail = (err) => { msg.className = "bad"; msg.textContent = err.message; };
  for (const tr of document.querySelectorAll("tr[data-region]")) tr.onclick = (ev) => {
    if (ev.target.closest("button, select")) return;
    for (const x of document.querySelectorAll("tr.sel")) x.classList.remove("sel");
    tr.classList.add("sel");
    highlight(tr.dataset.region);
  };
  const apply = async (op) => {
    try { await post(`/v1/jobs/${id}/review`, { by: nameOf("by"), ops: [op] }); route(); } catch (err) { fail(err); }
  };
  for (const b of document.querySelectorAll("[data-reclassify]")) b.onclick = () => {
    const rid = b.dataset.reclassify, to = document.querySelector(`select[data-to="${CSS.escape(rid)}"]`).value;
    apply({ action: "reclassify", region_id: rid, to });
  };
  for (const b of document.querySelectorAll("[data-exclude]")) b.onclick = () => apply({ action: "exclude", region_id: b.dataset.exclude });
  if (open) $("confirm").onclick = async () => {
    try { await post(`/v1/jobs/${id}/confirm`, { by: nameOf("by") }); location.hash = `#/job/${id}/progress`; } catch (err) { fail(err); }
  };
}

// ---------- 6. delivery ----------
async function viewPackages() {
  const r = await api("/v1/packages");
  $view.innerHTML = `<h1>Delivery</h1><p class="muted">${esc(r.delivery_dir)}</p>
    <div class="panel"><table><tr><th>Package</th><th>Contract check</th><th>Counts</th><th>Job</th></tr>
    ${r.packages.map((p) => `<tr><td>${esc(p.package)}</td>
      <td>${pill(p.check.ok ? "ok" : "failed", p.check.ok ? "ok" : "bad")}
        ${p.check.errors.length ? `<details><summary>${p.check.errors.length} error(s)</summary><pre>${esc(p.check.errors.join("\n"))}</pre></details>` : ""}</td>
      <td>${counts(p.check.counts)}</td>
      <td>${p.job_id ? `<a href="#/job/${esc(p.job_id)}/progress">${esc(p.job_id)}</a>` : ""}</td></tr>`).join("")}
    </table>${r.packages.length ? "" : '<p class="muted">No packages yet.</p>'}</div>`;
}

// ---------- router ----------
async function route() {
  clearTimeout(timer);
  const parts = (location.hash.replace(/^#\/?/, "") || "jobs").split("/");
  for (const a of document.querySelectorAll("nav a"))
    a.classList.toggle("active", a.dataset.view === (parts[0] === "job" ? "jobs" : parts[0]));
  try {
    if (parts[0] === "upload") viewUpload();
    else if (parts[0] === "packages") await viewPackages();
    else if (parts[0] === "job" && parts[2] === "labels") await viewLabels(parts[1], parts[3]);
    else if (parts[0] === "job" && parts[2] === "workflow") await viewWorkflow(parts[1]);
    else if (parts[0] === "job" && parts[2] === "review") await viewReview(parts[1], parts[3]);
    else if (parts[0] === "job") await viewProgress(parts[1]);
    else await viewJobs();
  } catch (err) { $view.innerHTML = `<div class="panel bad">${esc(err.message)}</div>`; }
}
window.addEventListener("hashchange", route);
engineBadge();
route();
