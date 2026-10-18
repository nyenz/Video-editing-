"use strict";
// Advanced: write an edit as a short script, see the plan, make the video.
let currentFile = "", validateTimer = null, validateSeq = 0;

function showError(target, err) {
  target.replaceChildren(el("div", { class: "error" }, el("span", { text: (err.line ? "Line " + err.line + ": " : "") + err.message }), err.fix ? el("small", { text: "How to fix: " + err.fix }) : null));
}
async function loadState() {
  const st = await api("/api/state");
  const sel = $("preset");
  sel.replaceChildren(el("option", { value: "", text: "Same as the original" }));
  for (const p of st.presets) sel.append(el("option", { value: p.name, text: p.label }));
  $("features").textContent = "Captions: " + (st.features.captions ? "on" : "off (add-on not installed)") + " · Face tracking: " + (st.features.faces ? "on" : "off (add-on not installed)");
  $("mediaNote").textContent = "Choose file uses a video where it is on your computer: nothing is copied, even for very big files.";
}
async function loadFiles(select) {
  const { files } = await api("/api/files");
  const list = $("fileList");
  list.replaceChildren(el("option", { value: "", text: files.length ? "Choose a video" : "No files yet" }));
  for (const f of files) list.append(el("option", { value: f.id, text: f.name + " · " + fmtSize(f.size) }));
  if (select) { list.value = select; currentFile = select; }
}
async function loadExamples() {
  const { examples } = await api("/api/examples");
  for (const ex of examples) $("examples").append(el("option", { value: ex.script, text: ex.name }));
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
  const ta = $("script"), lines = ta.value.split("\n"); let pos = 0;
  for (let i = 0; i < n - 1 && i < lines.length; i++) pos += lines[i].length + 1;
  ta.focus(); ta.setSelectionRange(pos, pos + (lines[n - 1] || "").length);
}
function scheduleValidate() { clearTimeout(validateTimer); validateTimer = setTimeout(validate, 350); }
async function validate() {
  const seq = ++validateSeq, text = $("script").value;
  if (!text.trim()) { $("problems").replaceChildren(); return; }
  try {
    const r = await api("/api/validate", { method: "POST", json: { script: text } });
    if (seq === validateSeq) renderProblems($("problems"), r.problems, "The script looks good (" + r.info.steps + " instruction" + (r.info.steps === 1 ? "" : "s") + ").");
  } catch (e) { if (seq === validateSeq) showError($("problems"), e); }
}
function options() { return { preset: $("preset").value || undefined, preview: $("preview").checked, fast_cuts: $("fast").checked }; }
function requireFile() { if (!currentFile) { $("actionNote").textContent = "Choose or add a video first."; return false; } $("actionNote").textContent = ""; return true; }
function classFor(s) {
  if (s.kind === "freeze") return "freeze"; if (s.reverse) return "rev"; if (s.transition_in || s.transition_out) return "trans";
  if (s.effects.length || s.zoom || s.crop || s.reframe || s.color) return "fx"; if (Math.abs(s.speed - 1) > 0.01) return "speed"; return "";
}
async function showPlan() {
  if (!requireFile()) return;
  $("planBtn").disabled = true; $("actionNote").textContent = "Working out the plan…";
  try {
    const p = await api("/api/plan", { method: "POST", json: { script: $("script").value, input: currentFile, options: options() } });
    $("planCard").hidden = false;
    $("planLen").textContent = fmtTime(p.duration, 2); $("planCount").textContent = String(p.segments.length);
    $("planSummary").textContent = p.notes.join(" ");
    const tl = $("timeline"); tl.replaceChildren();
    for (const s of p.segments) {
      const d = el("div", { class: "seg " + classFor(s), title: "#" + s.index + " · from " + fmtTime(s.source_start, 2) + " to " + fmtTime(s.source_end, 2) + " · speed ×" + s.speed });
      d.style.width = ((s.output_end - s.output_start) / Math.max(p.duration, 0.001)) * 100 + "%"; tl.append(d);
    }
    renderProblems($("planWarnings"), p.warnings.map((w) => ({ level: "warning", message: w })), "");
    $("planTable").tHead.replaceChildren(el("tr", {}, ...["#", "From (original)", "To (original)", "At (result)", "Speed", "Notes"].map((h) => el("th", { text: h }))));
    const body = $("planTable").tBodies[0]; body.replaceChildren();
    for (const s of p.segments.slice(0, 300)) {
      const notes = [s.kind !== "clip" ? s.kind : "", s.reverse ? "backwards" : "", s.effects.join(", "), s.zoom ? "zoom" : "", s.transition_in ? "transition in" : "", s.transition_out ? "transition out" : ""].filter(Boolean).join(", ");
      body.append(el("tr", {}, ...[s.index, fmtTime(s.source_start, 2), fmtTime(s.source_end, 2), fmtTime(s.output_start, 2), "×" + s.speed, notes].map((v) => el("td", { text: String(v) }))));
    }
    $("actionNote").textContent = "";
  } catch (e) { $("planCard").hidden = true; $("actionNote").textContent = ""; showError($("problems"), e); }
  finally { $("planBtn").disabled = false; }
}
async function run() {
  if (!requireFile()) return;
  $("runBtn").disabled = true;
  try { await api("/api/jobs", { method: "POST", json: { script: $("script").value, input: currentFile, options: options() } }); toast("Making your video"); Exports.show(); }
  catch (e) { showError($("problems"), e); }
  finally { $("runBtn").disabled = false; }
}

(async function init() {
  topBar("/advanced");
  paintIcons();
  $("addFile").addEventListener("click", async () => {
    const bar = $("uploadProg");
    try {
      const d = await chooseFile("media", (k) => { bar.hidden = false; bar.firstChild.style.width = Math.round(k * 100) + "%"; $("uploadStatus").textContent = "Copying… " + Math.round(k * 100) + "%"; }, "");
      if (!d) return;
      $("uploadStatus").textContent = "Ready: " + d.name + (d.linked ? " (used where it is)" : ""); await loadFiles(d.id);
    }
    catch (e) { $("uploadStatus").textContent = errText(e); }
    finally { bar.hidden = true; }
  });
  $("fileList").addEventListener("change", (e) => { currentFile = e.target.value; });
  $("examples").addEventListener("change", (e) => { if (e.target.value) { $("script").value = e.target.value; validate(); } });
  $("script").addEventListener("input", scheduleValidate);
  $("planBtn").addEventListener("click", showPlan);
  $("runBtn").addEventListener("click", run);
  Exports.onFiles = () => loadFiles(currentFile).catch(() => {});
  try { await loadState(); await loadFiles(); await loadExamples(); } catch (e) { $("actionNote").textContent = errText(e); }
  enhanceAll();
})();
