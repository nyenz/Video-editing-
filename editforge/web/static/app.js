"use strict";
// Everything shown on the page is added with textContent, never as HTML, so nothing you type can run as code.
const TOKEN = document.querySelector('meta[name="ef-token"]').content;
const $ = (id) => document.getElementById(id);
let currentFile = "";
let validateTimer = null;
let validateSeq = 0;

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid) n.append(kid);
  return n;
}
async function api(path, opts = {}) {
  const headers = Object.assign({ "X-EditForge-Token": TOKEN }, opts.headers || {});
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body });
  let data = {};
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) { const err = new Error(data.error || ("Request failed (" + res.status + ")")); err.fix = data.fix; err.line = data.line; throw err; }
  return data;
}
function fmtTime(s) { s = Math.max(0, Math.round(s)); const m = Math.floor(s / 60); return m + ":" + String(s % 60).padStart(2, "0"); }
function fmtSize(b) { return b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(1) + " MB"; }
function showError(target, err) {
  target.replaceChildren(el("div", { class: "error" }, el("span", { text: (err.line ? "Line " + err.line + ": " : "") + err.message }), err.fix ? el("small", { text: "How to fix: " + err.fix }) : null));
}

async function loadState() {
  const st = await api("/api/state");
  const sel = $("preset");
  sel.replaceChildren(el("option", { value: "", text: "Same as the original" }));
  for (const p of st.presets) sel.append(el("option", { value: p.name, text: p.label, title: p.description }));
  $("features").textContent = "Captions: " + (st.features.captions ? "on" : "off (add-on not installed)") + " \u00b7 Face tracking: " + (st.features.faces ? "on" : "off (add-on not installed)");
  $("mediaNote").textContent = "Files you put in " + st.media_dir + " appear in the list.";
}
async function loadFiles(select) {
  const { files } = await api("/api/files");
  const list = $("fileList");
  list.replaceChildren(el("option", { value: "", text: files.length ? "(choose)" : "(no files yet)" }));
  for (const f of files) list.append(el("option", { value: f.id, text: f.name + "  \u2013  " + fmtSize(f.size) + "  (" + f.where + ")" }));
  if (select) { list.value = select; currentFile = select; }
}
async function loadExamples() {
  const { examples } = await api("/api/examples");
  const sel = $("examples");
  for (const ex of examples) { const o = el("option", { value: ex.script, text: ex.name }); sel.append(o); }
}
async function upload(file) {
  $("uploadStatus").textContent = "Uploading " + file.name + " ...";
  try {
    const res = await fetch("/api/upload?name=" + encodeURIComponent(file.name), { method: "POST", headers: { "X-EditForge-Token": TOKEN, "Content-Type": "application/octet-stream" }, body: file });
    const data = await res.json();
    if (!res.ok) throw Object.assign(new Error(data.error), { fix: data.fix });
    $("uploadStatus").textContent = "Ready: " + data.name + " (" + fmtTime(data.duration) + (data.has_video ? ", " + data.width + "\u00d7" + data.height : ", audio only") + ")";
    await loadFiles(data.id);
  } catch (e) { $("uploadStatus").textContent = e.message + (e.fix ? " \u2013 " + e.fix : ""); }
}

function renderProblems(target, problems, okText) {
  target.replaceChildren();
  if (!problems.length && okText) target.append(el("div", { class: "ok", text: okText }));
  for (const p of problems) {
    const d = el("div", { class: p.level }, el("span", { text: (p.line ? "Line " + p.line + ": " : "") + p.message }), p.fix ? el("small", { text: "How to fix: " + p.fix }) : null);
    if (p.line) d.addEventListener("click", () => jumpToLine(p.line));
    target.append(d);
  }
}
function jumpToLine(n) {
  const ta = $("script"); const lines = ta.value.split("\n"); let pos = 0;
  for (let i = 0; i < n - 1 && i < lines.length; i++) pos += lines[i].length + 1;
  ta.focus(); ta.setSelectionRange(pos, pos + (lines[n - 1] || "").length);
}
function scheduleValidate() { clearTimeout(validateTimer); validateTimer = setTimeout(validate, 350); }
async function validate() {
  const seq = ++validateSeq; const text = $("script").value;
  if (!text.trim()) { $("problems").replaceChildren(); return; }
  try {
    const r = await api("/api/validate", { method: "POST", json: { script: text } });
    if (seq !== validateSeq) return;
    renderProblems($("problems"), r.problems, "The script looks good (" + r.info.steps + " instruction" + (r.info.steps === 1 ? "" : "s") + ").");
  } catch (e) { if (seq === validateSeq) showError($("problems"), e); }
}
function options() { return { preset: $("preset").value || undefined, preview: $("preview").checked, fast_cuts: $("fast").checked }; }
function requireFile() { if (!currentFile) { $("actionNote").textContent = "Choose or upload a video first (step 1)."; return false; } $("actionNote").textContent = ""; return true; }

function classFor(s) {
  if (s.kind === "freeze") return "freeze"; if (s.reverse) return "rev"; if (s.transition_in || s.transition_out) return "trans";
  if (s.effects.length || s.zoom || s.crop || s.reframe || s.color) return "fx"; if (Math.abs(s.speed - 1) > 0.01) return "speed"; return "";
}
async function showPlan() {
  if (!requireFile()) return;
  $("planBtn").disabled = true; $("actionNote").textContent = "Working out the plan (the first time may take a moment while the sound or picture is analysed) ...";
  try {
    const p = await api("/api/plan", { method: "POST", json: { script: $("script").value, input: currentFile, options: options() } });
    $("planCard").hidden = false;
    $("planSummary").textContent = "Finished length: " + fmtTime(p.duration) + " (" + p.duration.toFixed(2) + " s), " + p.segments.length + " segment(s)\n" + p.notes.join("\n");
    const tl = $("timeline"); tl.replaceChildren();
    for (const s of p.segments) {
      const w = ((s.output_end - s.output_start) / Math.max(p.duration, 0.001)) * 100;
      const d = el("div", { class: "seg " + classFor(s), title: "#" + s.index + "  source " + s.source_start + "-" + s.source_end + " s  speed x" + s.speed });
      d.style.width = w + "%"; tl.append(d);
    }
    renderProblems($("planWarnings"), p.warnings.map((w) => ({ level: "warning", message: w })), "");
    const head = $("planTable").tHead; head.replaceChildren(el("tr", {}, ...["#", "from (source)", "to (source)", "at (result)", "speed", "notes"].map((h) => el("th", { text: h }))));
    const body = $("planTable").tBodies[0]; body.replaceChildren();
    for (const s of p.segments.slice(0, 300)) {
      const notes = [s.kind !== "clip" ? s.kind : "", s.reverse ? "reverse" : "", s.effects.join(","), s.zoom ? "zoom" : "", s.transition_in ? "transition in" : "", s.transition_out ? "transition out" : ""].filter(Boolean).join(", ");
      body.append(el("tr", {}, ...[s.index, s.source_start, s.source_end, s.output_start, "\u00d7" + s.speed, notes].map((v) => el("td", { text: String(v) }))));
    }
    $("actionNote").textContent = "";
  } catch (e) { $("planCard").hidden = true; $("actionNote").textContent = ""; showError($("problems"), e); }
  finally { $("planBtn").disabled = false; }
}
async function run() {
  if (!requireFile()) return;
  $("runBtn").disabled = true;
  try {
    await api("/api/jobs", { method: "POST", json: { script: $("script").value, input: currentFile, options: options() } });
    $("actionNote").textContent = "Started. Watch the progress below."; await refreshJobs();
  } catch (e) { $("actionNote").textContent = ""; showError($("problems"), e); }
  finally { $("runBtn").disabled = false; }
}

async function refreshJobs() {
  let data;
  try { data = await api("/api/jobs"); } catch (e) { return; }
  const box = $("jobs"); box.replaceChildren();
  if (!data.jobs.length) box.append(el("div", { class: "note", text: "No jobs yet." }));
  for (const j of data.jobs) {
    const pct = Math.round((j.progress || 0) * 100);
    const name = (j.output_path || "").split(/[\\/]/).pop() || "(starting)";
    const top = el("div", { class: "top" }, el("span", { text: name }), el("span", { class: "st " + j.status, text: j.status + (j.status === "running" ? " \u2013 " + pct + "%" : "") }));
    const node = el("div", { class: "job" }, top);
    if (j.status === "running" || j.status === "queued") {
      const pr = el("progress", { max: "100", value: String(pct) }); node.append(pr);
      const eta = j.eta ? " \u2013 about " + fmtTime(j.eta) + " left" : "";
      node.append(el("div", { class: "note", text: (j.message || "") + eta }));
    } else if (j.message) node.append(el("div", { class: "note", text: j.message }));
    if (j.error) node.append(el("div", { class: "err", text: j.error + (j.error_fix ? "  How to fix: " + j.error_fix : "") }));
    const acts = el("div", { class: "acts" });
    if (j.status === "running" || j.status === "queued") { const b = el("button", { text: "Cancel" }); b.addEventListener("click", async () => { await api("/api/jobs/" + j.id + "/cancel", { method: "POST", json: {} }); refreshJobs(); }); acts.append(b); }
    if (["interrupted", "failed", "cancelled"].includes(j.status)) { const b = el("button", { text: "Resume" }); b.addEventListener("click", async () => { await api("/api/jobs/" + j.id + "/resume", { method: "POST", json: {} }); refreshJobs(); }); acts.append(b); }
    if (j.status === "done") acts.append(el("a", { class: "button", href: "/download/" + j.id + "?t=" + encodeURIComponent(TOKEN), text: "Download" }));
    if (["done", "failed", "cancelled", "interrupted"].includes(j.status)) { const b = el("button", { class: "secondary", text: "Remove" }); b.addEventListener("click", async () => { await api("/api/jobs/" + j.id, { method: "DELETE" }); refreshJobs(); }); acts.append(b); }
    node.append(acts); box.append(node);
  }
}

$("file").addEventListener("change", (e) => { if (e.target.files[0]) upload(e.target.files[0]); });
const drop = $("drop");
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
$("fileList").addEventListener("change", (e) => { currentFile = e.target.value; });
$("examples").addEventListener("change", (e) => { if (e.target.value) { $("script").value = e.target.value; validate(); } });
$("script").addEventListener("input", scheduleValidate);
$("planBtn").addEventListener("click", showPlan);
$("runBtn").addEventListener("click", run);

(async function init() {
  try { await loadState(); await loadFiles(); await loadExamples(); } catch (e) { $("actionNote").textContent = e.message; }
  await refreshJobs(); setInterval(refreshJobs, 1000);
})();
