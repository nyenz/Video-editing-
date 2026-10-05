"use strict";
// Helpers shared by the Editor and Combine pages.
// Everything shown on the page is added with textContent, never as HTML, so nothing you type can run as code.
const TOKEN = document.querySelector('meta[name="ef-token"]').content;
const $ = (id) => document.getElementById(id);

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
function note(id, text) { $(id).textContent = text || ""; }
function mediaUrl(id, preview) { return "/media?id=" + encodeURIComponent(id) + (preview ? "&preview=1" : "") + "&t=" + encodeURIComponent(TOKEN); }
function thumbUrl(id, at) { return "/thumb?id=" + encodeURIComponent(id) + "&at=" + at.toFixed(3) + "&t=" + encodeURIComponent(TOKEN); }
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

/** Send a file to EditForge with a progress bar. Calls done(data) or fail(message). */
function uploadFile(file, bar, statusId, done) {
  bar.hidden = false; bar.value = 0;
  note(statusId, "Adding " + file.name + " ...");
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload?name=" + encodeURIComponent(file.name));
  xhr.setRequestHeader("X-EditForge-Token", TOKEN);
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) { bar.value = (e.loaded / e.total) * 100; note(statusId, "Adding " + file.name + " ... " + Math.round(bar.value) + "%"); } };
  xhr.onerror = () => { bar.hidden = true; note(statusId, "The file could not be added. Check that EditForge is still running, then try again."); };
  xhr.onload = () => {
    bar.hidden = true;
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
    if (xhr.status >= 300) { note(statusId, (data.error || "The file could not be added.") + (data.fix ? " How to fix: " + data.fix : "")); return; }
    note(statusId, "Ready: " + data.name);
    done(data);
  };
  xhr.send(file);
}

/**
 * Show a video in a <video> tag. Browsers cannot play every kind of video, so when it fails a light
 * copy is made just for watching (finished videos are always made from the original).
 */
function showVideo(video, fileId, info, boxId, textId, barId) {
  let usingPreview = false, timer = null;
  const usePreview = () => {
    usingPreview = true;
    $(boxId).hidden = false; $(barId).hidden = true;
    note(textId, "Showing a light copy so it plays smoothly. Your finished video is still made from the original, in full quality.");
    video.src = mediaUrl(fileId, true);
  };
  const makePreview = async () => {
    if (usingPreview || !info.has_video) return;
    $(boxId).hidden = false; $(barId).hidden = false; $(barId).value = 0;
    note(textId, "Your browser cannot play this video directly, so I am making a light copy for watching. Please wait ...");
    try { await api("/api/preview", { method: "POST", json: { input: fileId } }); }
    catch (e) { note(textId, errText(e)); $(barId).hidden = true; return; }
    clearInterval(timer);
    timer = setInterval(async () => {
      let st;
      try { st = await api("/api/preview?id=" + encodeURIComponent(fileId)); } catch (e) { return; }
      $(barId).value = Math.round(st.progress * 100);
      if (st.state === "running") note(textId, "Making a light copy for watching ... " + Math.round(st.progress * 100) + "%");
      if (st.state === "ready") { clearInterval(timer); usePreview(); }
      if (st.state === "failed") { clearInterval(timer); $(barId).hidden = true; note(textId, "The light copy could not be made. " + st.error); }
    }, 1000);
  };
  video.addEventListener("error", () => {
    if (!video.getAttribute("src")) return;
    if (!usingPreview) { makePreview(); return; }
    $(boxId).hidden = false; $(barId).hidden = true;
    note(textId, "This browser cannot play video. Open this page in Google Chrome or Microsoft Edge. Everything else on the page still works.");
  });
  video.addEventListener("loadedmetadata", () => { if (info.has_video && !usingPreview && video.videoWidth === 0) makePreview(); });
  $(boxId).hidden = true;
  if (info.has_video && info.preview && info.preview.state === "ready") usePreview(); else video.src = mediaUrl(fileId, false);
}

/** The list of finished videos (and ones being made). Rebuilt only when something changes, so an open player keeps playing. */
function startJobList(boxId, onFiles) {
  const open = new Set();
  let signature = "";
  async function refresh() {
    let data;
    try { data = await api("/api/jobs"); } catch (e) { return; }
    const sig = JSON.stringify(data.jobs.map((j) => [j.id, j.status, Math.round((j.progress || 0) * 100), j.message, Math.round(j.eta || 0)])) + [...open].join();
    if (sig === signature) return;
    signature = sig;
    const box = $(boxId); box.replaceChildren();
    if (!data.jobs.length) box.append(el("div", { class: "empty", text: "Nothing here yet. Your finished videos will appear here." }));
    for (const j of data.jobs) {
      const pct = Math.round((j.progress || 0) * 100);
      const name = (j.output_path || "").split(/[\\/]/).pop() || "(waiting to start)";
      const label = { queued: "waiting", running: "working - " + pct + "%", done: "done", failed: "failed", cancelled: "stopped", interrupted: "interrupted" }[j.status] || j.status;
      const node = el("div", { class: "job" }, el("div", { class: "top" }, el("span", { text: name + (j.preview ? "  (quick preview)" : "") }), el("span", { class: "st " + j.status, text: label })));
      if (j.status === "running" || j.status === "queued") {
        node.append(el("progress", { max: "100", value: String(pct) }));
        node.append(el("div", { class: "note", text: (j.message || "") + (j.eta ? " - about " + fmtTime(j.eta, true) + " left" : "") }));
      }
      const v = j.result && j.result.verify;
      if (j.status === "done" && j.result) node.append(el("div", { class: "facts", text: fmtTime(j.result.duration) + " long" + (v && v.width ? " - " + v.width + "x" + v.height : "") + (v && v.size_bytes ? " - " + fmtSize(v.size_bytes) : "") }));
      if (j.error) node.append(el("div", { class: "err", text: j.error + (j.error_fix ? "  How to fix: " + j.error_fix : "") }));
      const acts = el("div", { class: "acts" });
      const act = (text, cls, fn) => { const b = el("button", { text, class: cls || "" }); b.addEventListener("click", fn); acts.append(b); };
      if (j.status === "running" || j.status === "queued") act("Stop", "", async () => { await api("/api/jobs/" + j.id + "/cancel", { method: "POST", json: {} }); refresh(); });
      if (["interrupted", "failed", "cancelled"].includes(j.status)) act("Try again", "", async () => { await api("/api/jobs/" + j.id + "/resume", { method: "POST", json: {} }); refresh(); });
      if (j.status === "done" && j.file_id) {
        act(open.has(j.id) ? "Hide" : "Watch", "primary", () => { if (open.has(j.id)) open.delete(j.id); else open.add(j.id); refresh(); });
        acts.append(el("a", { class: "button", href: "/download/" + j.id + "?t=" + encodeURIComponent(TOKEN), text: "Save to my computer" }));
        if (!j.preview) {
          act("Slice and edit this", "secondary", () => newProject(j.file_id));
          acts.append(el("a", { class: "button ghost", href: "/?open=" + encodeURIComponent(j.file_id), text: "Cut clips from this" }));
        }
      }
      if (["done", "failed", "cancelled", "interrupted"].includes(j.status)) act("Remove", "secondary", async () => {
        if (!window.confirm("Remove '" + name + "'? The video file will be deleted.")) return;
        open.delete(j.id); await api("/api/jobs/" + j.id, { method: "DELETE" }); if (onFiles) onFiles(); refresh();
      });
      node.append(acts);
      if (j.status === "done" && j.file_id && open.has(j.id)) node.append(el("video", { controls: "", playsinline: "", preload: "metadata", src: mediaUrl(j.file_id, false) }));
      box.append(node);
    }
  }
  refresh(); setInterval(refresh, 1000);
  return refresh;
}

/** Make a new project from a video and open it in the editor. */
async function newProject(fileId, interval) {
  try {
    const r = await api("/api/projects", { method: "POST", json: { source: fileId, interval: interval || 0 } });
    window.location.href = "/edit?p=" + r.project.id;
  } catch (e) { window.alert(errText(e)); }
}
