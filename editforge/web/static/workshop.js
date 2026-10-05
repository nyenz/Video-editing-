"use strict";
// Clip workshop: watch a video, mark a start and an end, collect clips, and make them in high quality.
// Everything shown on the page is added with textContent, never as HTML, so nothing you type can run as code.
const TOKEN = document.querySelector('meta[name="ef-token"]').content;
const $ = (id) => document.getElementById(id);
const video = $("player");

const state = {
  file: "",          // id of the chosen video
  info: null,        // facts about it (duration, fps ...)
  usingPreview: false,
  start: null,       // marked start in seconds (or null)
  end: null,
  clips: [],         // [{start, end, name}]
  stopAt: null,      // when "Play this part" should pause
  openJobs: new Set(), // finished videos whose player is open
  previewTimer: null,
};

// ---------------------------------------------------------------- small helpers
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
  if (!res.ok) { const err = new Error(data.error || ("Request failed (" + res.status + ")")); err.fix = data.fix; throw err; }
  return data;
}
function errText(e) { return e.message + (e.fix ? " How to fix: " + e.fix : ""); }
function mediaUrl(id, preview) { return "/media?id=" + encodeURIComponent(id) + (preview ? "&preview=1" : "") + "&t=" + encodeURIComponent(TOKEN); }
function fmtSize(b) { return b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(1) + " MB"; }

/** 3725.5 -> "1:02:05.500"; short=true drops the thousandths. */
function fmtTime(s, short) {
  if (s === null || s === undefined || !isFinite(s)) return "-";
  const ms = Math.max(0, Math.round(s * 1000));
  const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000), sec = Math.floor((ms % 60000) / 1000), frac = ms % 1000;
  let out = (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(sec).padStart(2, "0");
  if (!short) out += "." + String(frac).padStart(3, "0");
  return out;
}
/** Accepts 75, 75.5, 1:15, 1:02:05.5 and returns seconds, or null if it is not a time. */
function parseTime(text) {
  const t = String(text).trim().replace(",", ".");
  if (!t) return null;
  const parts = t.split(":");
  if (parts.length > 3) return null;
  let total = 0;
  for (const p of parts) {
    if (!/^\d+(\.\d+)?$/.test(p)) return null;
    total = total * 60 + parseFloat(p);
  }
  return total;
}
function duration() { return state.info ? state.info.duration : 0; }
function frameLen() { return state.info && state.info.fps_float ? 1 / state.info.fps_float : 0.04; }
function clamp(t) { return Math.max(0, Math.min(duration(), t)); }
function note(id, text) { $(id).textContent = text || ""; }

// ---------------------------------------------------------------- remembering clips between visits
function storeKey() { return "editforge.clips." + state.file; }
function saveClips() {
  try { localStorage.setItem(storeKey(), JSON.stringify(state.clips)); } catch (e) { /* storage may be off; the page still works */ }
}
function loadClips() {
  try {
    const raw = JSON.parse(localStorage.getItem(storeKey()) || "[]");
    return Array.isArray(raw) ? raw.filter((c) => c && isFinite(c.start) && isFinite(c.end) && c.end > c.start) : [];
  } catch (e) { return []; }
}

// ---------------------------------------------------------------- step 1: choose a video
async function loadFiles(select) {
  const { files } = await api("/api/files");
  const list = $("fileList");
  list.replaceChildren(el("option", { value: "", text: files.length ? "(choose)" : "(none yet)" }));
  for (const f of files) list.append(el("option", { value: f.id, text: f.name + "  -  " + fmtSize(f.size) + "  (" + f.where + ")" }));
  if (select) list.value = select;
}
function upload(file) {
  const bar = $("uploadBar");
  bar.hidden = false; bar.value = 0;
  note("uploadStatus", "Adding " + file.name + " ...");
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload?name=" + encodeURIComponent(file.name));
  xhr.setRequestHeader("X-EditForge-Token", TOKEN);
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) { bar.value = (e.loaded / e.total) * 100; note("uploadStatus", "Adding " + file.name + " ... " + Math.round(bar.value) + "%"); } };
  xhr.onerror = () => { bar.hidden = true; note("uploadStatus", "The file could not be added. Check that EditForge is still running, then try again."); };
  xhr.onload = async () => {
    bar.hidden = true;
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
    if (xhr.status >= 300) { note("uploadStatus", (data.error || "The file could not be added.") + (data.fix ? " How to fix: " + data.fix : "")); return; }
    note("uploadStatus", "Ready: " + data.name);
    await loadFiles(data.id);
    openFile(data.id);
  };
  xhr.send(file);
}

async function openFile(id) {
  clearInterval(state.previewTimer);
  state.file = id; state.info = null; state.start = null; state.end = null; state.stopAt = null; state.usingPreview = false;
  $("step2").hidden = !id; $("step3").hidden = !id;
  video.removeAttribute("src"); video.load();
  if (!id) return;
  try { state.info = await api("/api/info?id=" + encodeURIComponent(id)); }
  catch (e) { note("uploadStatus", errText(e)); $("step2").hidden = true; $("step3").hidden = true; return; }
  const i = state.info;
  note("fileFacts", fmtTime(i.duration, true) + " long" + (i.has_video ? " - " + i.width + "x" + i.height + ", " + i.fps_float + " frames a second" : " - sound only"));
  note("totalTime", fmtTime(i.duration, true));
  state.clips = loadClips();
  $("previewBox").hidden = true;
  if (i.has_video && i.preview.state === "ready") usePreview(); else video.src = mediaUrl(id, false);
  renderMarks(); renderClips();
  $("step2").scrollIntoView({ behavior: "smooth", block: "start" });
}

// The browser cannot play every kind of video. If it fails, make a light copy just for watching.
function usePreview() {
  const at = video.currentTime || 0;
  state.usingPreview = true;
  $("previewBox").hidden = false; $("previewBar").hidden = true;
  note("previewText", "Showing a light copy so it plays smoothly. Your finished clips are still made from the original, in full quality.");
  video.src = mediaUrl(state.file, true);
  video.addEventListener("loadedmetadata", () => { video.currentTime = Math.min(at, duration()); }, { once: true });
}
async function makePreview() {
  if (!state.file || !state.info || !state.info.has_video || state.usingPreview) return;
  const id = state.file;
  $("previewBox").hidden = false; $("previewBar").hidden = false; $("previewBar").value = 0;
  note("previewText", "Your browser cannot play this video directly, so I am making a light copy for watching. Please wait ...");
  try { await api("/api/preview", { method: "POST", json: { input: id } }); }
  catch (e) { note("previewText", errText(e)); $("previewBar").hidden = true; return; }
  clearInterval(state.previewTimer);
  state.previewTimer = setInterval(async () => {
    if (state.file !== id) { clearInterval(state.previewTimer); return; }
    let st;
    try { st = await api("/api/preview?id=" + encodeURIComponent(id)); } catch (e) { return; }
    $("previewBar").value = Math.round(st.progress * 100);
    if (st.state === "running") note("previewText", "Making a light copy for watching ... " + Math.round(st.progress * 100) + "%");
    if (st.state === "ready") { clearInterval(state.previewTimer); usePreview(); }
    if (st.state === "failed") { clearInterval(state.previewTimer); $("previewBar").hidden = true; note("previewText", "The light copy could not be made. " + st.error); }
  }, 1000);
}
video.addEventListener("error", () => {
  if (!state.file || !video.getAttribute("src")) return;
  if (!state.usingPreview) { makePreview(); return; }
  $("previewBox").hidden = false; $("previewBar").hidden = true;
  note("previewText", "This browser cannot play video. Open this page in Google Chrome or Microsoft Edge. You can still type the start and end times and make clips.");
});
video.addEventListener("loadedmetadata", () => {
  // Some files load but show no picture (the browser knows the sound but not the picture format).
  if (state.info && state.info.has_video && !state.usingPreview && video.videoWidth === 0) makePreview();
});

// ---------------------------------------------------------------- step 2: player and marks
function seek(t) { if (state.info) video.currentTime = clamp(t); }
function renderHead() {
  const d = duration();
  note("nowTime", fmtTime(video.currentTime || 0));
  $("trackHead").style.left = d ? (Math.min(1, (video.currentTime || 0) / d) * 100) + "%" : "0";
}
function renderMarks() {
  const d = duration(), s = state.start, e = state.end;
  if (document.activeElement !== $("startIn")) $("startIn").value = s === null ? "" : fmtTime(s);
  if (document.activeElement !== $("endIn")) $("endIn").value = e === null ? "" : fmtTime(e);
  const ok = s !== null && e !== null && e > s;
  note("selLen", ok ? fmtTime(e - s) : "-");
  const sel = $("trackSel");
  sel.hidden = s === null || !d;
  if (!sel.hidden) {
    sel.style.left = (s / d) * 100 + "%";
    sel.style.width = (((ok ? e : s) - s) / d) * 100 + "%";
  }
  $("playSel").disabled = !ok; $("addClip").disabled = !ok;
  if (s !== null && e !== null && e <= s) note("markNote", "The end must come after the start. Move one of them."); else note("markNote", "");
  renderHead();
}
function setStart(t) { state.start = clamp(t); renderMarks(); }
function setEnd(t) { state.end = clamp(t); renderMarks(); }
function typedMark(input, setter) {
  if (!input.value.trim()) { if (setter === setStart) state.start = null; else state.end = null; renderMarks(); return; }
  const t = parseTime(input.value);
  if (t === null) { note("markNote", "'" + input.value + "' is not a time. Write it like 1:23 or 1:02:03.5 or 75."); return; }
  setter(t); input.value = fmtTime(setter === setStart ? state.start : state.end);
}
function playRange(a, b) {
  state.stopAt = b; video.currentTime = a;
  const p = video.play(); if (p && p.catch) p.catch(() => {});
}
function watchStop() {
  if (state.stopAt !== null && !video.paused && video.currentTime >= state.stopAt) { video.pause(); video.currentTime = state.stopAt; state.stopAt = null; }
  renderHead();
  requestAnimationFrame(watchStop);
}
function addClip() {
  const s = state.start, e = state.end;
  if (s === null || e === null || e <= s) { note("markNote", "Set a start and an end first (the end must come after the start)."); return; }
  state.clips.push({ start: s, end: e, name: "clip" + String(state.clips.length + 1).padStart(2, "0") });
  state.start = null; state.end = null;
  saveClips(); renderMarks(); renderClips();
  note("markNote", "Clip added. Mark the next one, or go to step 3.");
}

// ---------------------------------------------------------------- step 3: the clip list
function renderClips() {
  const box = $("clips"), d = duration();
  box.replaceChildren();
  const bars = $("trackClips"); bars.replaceChildren();
  if (!state.clips.length) box.append(el("div", { class: "empty", text: "No clips yet. In step 2, press 'Set START here', then 'Set END here', then '+ Add clip'." }));
  let total = 0;
  state.clips.forEach((c, i) => {
    total += c.end - c.start;
    const name = el("input", { type: "text", value: c.name, "aria-label": "Name of clip " + (i + 1), maxlength: "60" });
    name.addEventListener("input", () => { c.name = name.value; saveClips(); });
    const play = el("button", { class: "small", text: "Play" }); play.addEventListener("click", () => { playRange(c.start, c.end); video.scrollIntoView({ behavior: "smooth", block: "center" }); });
    const edit = el("button", { class: "small", text: "Change", title: "Put this clip back into step 2 to move its start or end" });
    edit.addEventListener("click", () => { state.clips.splice(i, 1); state.start = c.start; state.end = c.end; saveClips(); renderClips(); renderMarks(); seek(c.start); $("step2").scrollIntoView({ behavior: "smooth", block: "start" }); });
    const del = el("button", { class: "small", text: "Delete" }); del.addEventListener("click", () => { state.clips.splice(i, 1); saveClips(); renderClips(); });
    box.append(el("div", { class: "clipRow" }, el("span", { class: "n", text: String(i + 1) }), name,
      el("span", { class: "times", text: fmtTime(c.start) + " to " + fmtTime(c.end) + "  (" + fmtTime(c.end - c.start) + ")" }),
      el("div", { class: "acts" }, play, edit, del)));
    if (d) { const b = el("div", { class: "clip", title: c.name }); b.style.left = (c.start / d) * 100 + "%"; b.style.width = ((c.end - c.start) / d) * 100 + "%"; bars.append(b); }
  });
  note("clipTotals", state.clips.length ? state.clips.length + " clip" + (state.clips.length === 1 ? "" : "s") + ", " + fmtTime(total, true) + " in total" : "");
  $("makeBtn").disabled = !state.clips.length; $("clearBtn").disabled = !state.clips.length;
}
async function makeClips() {
  if (!state.clips.length) return;
  $("makeBtn").disabled = true; note("makeNote", "Starting ...");
  const join = document.querySelector('input[name="mode"]:checked').value === "join";
  try {
    const r = await api("/api/clips", { method: "POST", json: { input: state.file, clips: state.clips, join, quality: $("quality").value } });
    note("makeNote", "Started " + r.jobs.length + " video" + (r.jobs.length === 1 ? "" : "s") + ". Watch the progress in step 4 below.");
    await refreshJobs();
  } catch (e) { note("makeNote", errText(e)); }
  finally { $("makeBtn").disabled = !state.clips.length; }
}

// ---------------------------------------------------------------- step 4: finished videos
let jobsSignature = "";
async function refreshJobs() {
  let data;
  try { data = await api("/api/jobs"); } catch (e) { return; }
  // Only rebuild the list when something changed, so an open player keeps playing.
  const sig = JSON.stringify(data.jobs.map((j) => [j.id, j.status, Math.round((j.progress || 0) * 100), j.message, Math.round(j.eta || 0)])) + [...state.openJobs].join();
  if (sig === jobsSignature) return;
  jobsSignature = sig;
  const box = $("jobs"); box.replaceChildren();
  if (!data.jobs.length) box.append(el("div", { class: "empty", text: "Nothing here yet. Your finished clips will appear here." }));
  for (const j of data.jobs) {
    const pct = Math.round((j.progress || 0) * 100);
    const name = (j.output_path || "").split(/[\\/]/).pop() || "(waiting to start)";
    const label = { queued: "waiting", running: "working - " + pct + "%", done: "done", failed: "failed", cancelled: "stopped", interrupted: "interrupted" }[j.status] || j.status;
    const node = el("div", { class: "job" }, el("div", { class: "top" }, el("span", { text: name }), el("span", { class: "st " + j.status, text: label })));
    if (j.status === "running" || j.status === "queued") {
      node.append(el("progress", { max: "100", value: String(pct) }));
      node.append(el("div", { class: "note", text: (j.message || "") + (j.eta ? " - about " + fmtTime(j.eta, true) + " left" : "") }));
    }
    if (j.status === "done" && j.result) node.append(el("div", { class: "facts", text: fmtTime(j.result.duration) + " long" + (j.result.verify && j.result.verify.width ? " - " + j.result.verify.width + "x" + j.result.verify.height : "") + (j.result.verify && j.result.verify.size_bytes ? " - " + fmtSize(j.result.verify.size_bytes) : "") }));
    if (j.error) node.append(el("div", { class: "err", text: j.error + (j.error_fix ? "  How to fix: " + j.error_fix : "") }));
    const acts = el("div", { class: "acts" });
    const act = (text, cls, fn) => { const b = el("button", { text, class: cls || "" }); b.addEventListener("click", fn); acts.append(b); };
    if (j.status === "running" || j.status === "queued") act("Stop", "", async () => { await api("/api/jobs/" + j.id + "/cancel", { method: "POST", json: {} }); refreshJobs(); });
    if (["interrupted", "failed", "cancelled"].includes(j.status)) act("Try again", "", async () => { await api("/api/jobs/" + j.id + "/resume", { method: "POST", json: {} }); refreshJobs(); });
    if (j.status === "done" && j.file_id) {
      act(state.openJobs.has(j.id) ? "Hide" : "Watch", "primary", () => { if (state.openJobs.has(j.id)) state.openJobs.delete(j.id); else state.openJobs.add(j.id); refreshJobs(); });
      acts.append(el("a", { class: "button", href: "/download/" + j.id + "?t=" + encodeURIComponent(TOKEN), text: "Save to my computer" }));
      act("Edit this video", "secondary", async () => { await loadFiles(j.file_id); openFile(j.file_id); });
    }
    if (["done", "failed", "cancelled", "interrupted"].includes(j.status)) act("Remove", "secondary", async () => {
      if (!window.confirm("Remove '" + name + "'? The video file will be deleted.")) return;
      state.openJobs.delete(j.id); await api("/api/jobs/" + j.id, { method: "DELETE" }); await loadFiles(state.file); refreshJobs();
    });
    node.append(acts);
    if (j.status === "done" && j.file_id && state.openJobs.has(j.id)) node.append(el("video", { controls: "", playsinline: "", preload: "metadata", src: mediaUrl(j.file_id, false) }));
    box.append(node);
  }
}

// ---------------------------------------------------------------- wiring
$("file").addEventListener("change", (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; });
const drop = $("drop");
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
$("fileList").addEventListener("change", (e) => openFile(e.target.value));

for (const b of document.querySelectorAll("[data-step]")) b.addEventListener("click", () => { state.stopAt = null; seek(video.currentTime + parseFloat(b.dataset.step)); });
for (const b of document.querySelectorAll("[data-frame]")) b.addEventListener("click", () => { state.stopAt = null; video.pause(); seek(video.currentTime + parseInt(b.dataset.frame, 10) * frameLen()); });
function togglePlay() { state.stopAt = null; if (video.paused) { const p = video.play(); if (p && p.catch) p.catch(() => {}); } else video.pause(); }
$("playBtn").addEventListener("click", togglePlay);
video.addEventListener("play", () => note("playBtn", "Pause"));
video.addEventListener("pause", () => note("playBtn", "Play"));
video.addEventListener("seeked", renderHead);
$("setStart").addEventListener("click", () => setStart(video.currentTime));
$("setEnd").addEventListener("click", () => setEnd(video.currentTime));
$("goStart").addEventListener("click", () => { if (state.start !== null) seek(state.start); });
$("goEnd").addEventListener("click", () => { if (state.end !== null) seek(state.end); });
$("startIn").addEventListener("change", () => typedMark($("startIn"), setStart));
$("endIn").addEventListener("change", () => typedMark($("endIn"), setEnd));
$("playSel").addEventListener("click", () => playRange(state.start, state.end));
$("addClip").addEventListener("click", addClip);
$("makeBtn").addEventListener("click", makeClips);
$("clearBtn").addEventListener("click", () => { if (window.confirm("Remove all " + state.clips.length + " clips from the list?")) { state.clips = []; saveClips(); renderClips(); } });

// Click or drag on the bar under the video to jump around.
const track = $("track");
function trackSeek(e) { const r = track.getBoundingClientRect(); state.stopAt = null; seek(((e.clientX - r.left) / r.width) * duration()); }
track.addEventListener("pointerdown", (e) => { track.setPointerCapture(e.pointerId); trackSeek(e); });
track.addEventListener("pointermove", (e) => { if (e.buttons) trackSeek(e); });

document.addEventListener("keydown", (e) => {
  if (!state.file || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea" || tag === "button" || tag === "a" || tag === "summary") return;
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); togglePlay(); }
  else if (k === "i") setStart(video.currentTime);
  else if (k === "o") setEnd(video.currentTime);
  else if (k === "enter") addClip();
  else if (k === "arrowleft") { e.preventDefault(); seek(video.currentTime - 1); }
  else if (k === "arrowright") { e.preventDefault(); seek(video.currentTime + 1); }
  else if (k === ",") { video.pause(); seek(video.currentTime - frameLen()); }
  else if (k === ".") { video.pause(); seek(video.currentTime + frameLen()); }
});

(async function init() {
  try {
    const st = await api("/api/state");
    note("mediaNote", "Copy the video into this folder on your computer, then reload this page and pick it from the list above: " + st.media_dir);
    await loadFiles();
  } catch (e) { note("uploadStatus", errText(e)); }
  renderClips(); renderMarks();
  await refreshJobs(); setInterval(refreshJobs, 1000);
  requestAnimationFrame(watchStop);
})();
