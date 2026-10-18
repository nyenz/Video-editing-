"use strict";
// Clips page: watch a video, mark a start and an end, collect clips, then make them (one each, or joined).
// The timeline here is the whole source video. Clips are kept in this browser between visits.

const C = {
  file: "", info: null, files: [],
  start: null, end: null,
  clips: [],            // [{start, end, name, on}]  on = ticked
  stopAt: null, pps: 10, quality: "best", muted: false,
  usingLight: false,
};
const video = $("player");
const dur = () => (C.info ? C.info.duration : 0);
const frameLen = () => (C.info && C.info.fps_float ? 1 / C.info.fps_float : 0.04);
const clampT = (t) => clamp(t, 0, dur());

// ------------------------------------------------------------------ remembering clips
const storeKey = () => "editforge.clips." + C.file;
function saveClips() { try { localStorage.setItem(storeKey(), JSON.stringify(C.clips)); } catch (e) { /* storage may be off */ } }
function loadClips() {
  try {
    const raw = JSON.parse(localStorage.getItem(storeKey()) || "[]");
    return Array.isArray(raw) ? raw.filter((c) => c && isFinite(c.start) && isFinite(c.end) && c.end > c.start).map((c) => Object.assign({ on: false }, c)) : [];
  } catch (e) { return []; }
}

// ------------------------------------------------------------------ videos list
async function loadFiles() {
  C.files = (await api("/api/files")).files.filter((f) => !f.subtitles && !f.audio);
  const box = $("fileList"); box.replaceChildren();
  if (!C.files.length) box.append(el("p", { class: "hint", text: "No videos yet." }));
  for (const f of C.files) box.append(el("div", { class: "item" + (f.id === C.file ? " on" : ""), onclick: () => openFile(f.id), title: f.name },
    el("img", { class: "thumb", alt: "", src: thumbUrl(f.id, 1), loading: "lazy" }),
    el("div", { class: "t" }, el("b", { text: f.name }), el("small", { text: fmtSize(f.size) + " · " + (f.linked ? "used where it is" : f.where), title: f.where }))));
}
/** Add a video: chosen in the computer's file window (used where it is), or dropped (a copy is sent). */
async function addFile(file) {
  const bar = $("uploadProg");
  const prog = (k) => { bar.hidden = false; bar.firstChild.style.width = Math.round(k * 100) + "%"; $("uploadNote").textContent = "Copying " + (file ? file.name : "the video") + " into the app… " + Math.round(k * 100) + "%"; };
  try {
    const d = file ? await uploadFile(file, prog) : await chooseFile("media", prog, "video/*,audio/*,.mkv,.mov,.avi,.ts");
    if (!d) return;
    $("uploadNote").textContent = d.linked ? "Used where it is: " + d.where : "";
    await loadFiles(); openFile(d.id);
  } catch (e) { $("uploadNote").textContent = errText(e); }
  finally { bar.hidden = true; }
}

// ------------------------------------------------------------------ open a video
function banner(text, k) {
  const b = $("banner"); if (!text) { b.hidden = true; return; }
  b.hidden = false; $("bannerText").textContent = text; $("bannerBar").style.width = Math.round((k || 0) * 100) + "%";
}
async function openFile(id) {
  video.pause();
  C.file = id; C.start = C.end = null; C.stopAt = null; C.usingLight = false;
  history.replaceState(null, "", id ? "/clips?open=" + encodeURIComponent(id) : "/clips");
  try { C.info = await api("/api/info?id=" + encodeURIComponent(id)); }
  catch (e) { $("uploadNote").textContent = errText(e); return; }
  for (const it of $("fileList").children) it.classList.toggle("on", it.title === id.split("/").pop());
  loadFiles().catch(() => {});
  $("empty").hidden = true;
  C.clips = loadClips();
  const i = C.info;
  $("tTotal").textContent = fmtTime(i.duration); $("iLen").textContent = fmtTime(i.duration, 0);
  sizeFrame();
  // The light copy plays everywhere and has normal sound (some videos have sound a browser cannot play, like 5.1 Dolby).
  banner(null);
  if (i.preview && i.preview.state === "ready") useLight();
  else {
    if (i.plays_in_browser) video.src = mediaUrl(id, false);
    else { video.removeAttribute("src"); }
    let quick = false;
    if (i.has_video) ensurePreview(id, i, (st) => {
      if (C.file !== id) return;
      if (st.state === "running" && !i.plays_in_browser) banner(quick ? "Playing a quick copy. Making a lighter one for smoother jumping… " + Math.round(st.progress * 100) + "%"
        : "This video's sound or picture cannot play in a browser (" + i.playback_note + "). Making a copy you can watch and hear… " + Math.round(st.progress * 100) + "%", st.progress);
      if (st.state === "failed") banner("A light copy could not be made: " + st.error, 0);
    }, !i.plays_in_browser, () => { if (C.file === id && !i.plays_in_browser) { quick = true; useQuick(); } }).then((ok) => { if (ok && C.file === id) { banner(null); useLight(); } });
  }
  fitZoom(); renderAll();
}
function useQuick() {
  const at = video.currentTime || 0;
  video.src = mediaUrl(C.file, "q");
  video.addEventListener("loadedmetadata", () => { video.currentTime = Math.min(at, dur()); }, { once: true });
}
function useLight() {
  const at = video.currentTime || 0;
  C.usingLight = true;
  video.src = mediaUrl(C.file, true);
  video.addEventListener("loadedmetadata", () => { video.currentTime = Math.min(at, dur()); }, { once: true });
}
video.addEventListener("error", () => {
  if (!C.file || !video.getAttribute("src")) return;
  if (C.usingLight && C.info.plays_in_browser) { C.usingLight = false; video.src = mediaUrl(C.file, false); return; }
  if (!C.usingLight) banner("This browser cannot play this video directly. Making a light copy…", 0);
});
function sizeFrame() {
  const st = $("stage"), f = $("frame");
  const a = C.info && C.info.width && C.info.height ? C.info.width / C.info.height : 16 / 9;
  const W = st.clientWidth - 36, H = st.clientHeight - 20;
  let w = W, h = W / a; if (h > H) { h = H; w = H * a; }
  f.style.width = Math.max(40, Math.floor(w)) + "px"; f.style.height = Math.max(40, Math.floor(h)) + "px";
}

// ------------------------------------------------------------------ playing
function seek(t) { if (C.info) { C.stopAt = null; video.currentTime = clampT(t); renderHead(); } }
function togglePlay() { if (!C.info) return; C.stopAt = null; if (video.paused) { const p = video.play(); if (p && p.catch) p.catch(() => {}); } else video.pause(); }
function playRange(a, b) { if (!C.info) return; video.currentTime = a; C.stopAt = b; const p = video.play(); if (p && p.catch) p.catch(() => {}); }
video.addEventListener("play", () => $("tPlay").replaceChildren(icon("pause")));
video.addEventListener("pause", () => $("tPlay").replaceChildren(icon("play")));
function loop() {
  if (C.stopAt !== null && !video.paused && video.currentTime >= C.stopAt) { video.pause(); video.currentTime = C.stopAt; C.stopAt = null; }
  if (!video.paused) { renderHead(); followHead(); }
  requestAnimationFrame(loop);
}
function renderHead() {
  const t = video.currentTime || 0;
  $("tNow").textContent = fmtTime(t);
  $("playhead").style.left = px(t) + "px";
  const live = C.start !== null && C.end === null && t > C.start;
  if (live) { $("selLen").textContent = fmtTime(t - C.start) + " so far"; }
}
function followHead() {
  const sc = $("tlScroll"), x = px(video.currentTime);
  if (x > sc.scrollLeft + sc.clientWidth - 30 || x < sc.scrollLeft) sc.scrollLeft = x - 60;
}

// ------------------------------------------------------------------ marks
function selOk() { return C.start !== null && C.end !== null && C.end > C.start; }
function renderMarks() {
  const s = C.start, e = C.end, ok = selOk();
  if (document.activeElement !== $("startIn")) $("startIn").value = s === null ? "" : fmtTime(s, 2);
  if (document.activeElement !== $("endIn")) $("endIn").value = e === null ? "" : fmtTime(e, 2);
  const len = ok ? fmtTime(e - s, 2) : "–";
  $("selLen").textContent = len; $("selLenTop").textContent = ok ? len : "";
  $("iSel").textContent = ok ? "Marked " + len : "";
  $("playSel").disabled = !ok; $("addClip").disabled = !ok;
  $("markNote").textContent = s !== null && e !== null && e <= s ? "The end must come after the start." : "";
  const m = $("markSel");
  m.hidden = s === null;
  if (s !== null) {
    const b = e !== null && e > s ? e : Math.max(s, video.currentTime || s);
    m.style.left = px(s) + "px"; m.style.width = Math.max(2, px(b) - px(s)) + "px";
    $("markLen").textContent = ok ? len : "";
  }
  const bd = $("selBadge"); bd.hidden = !ok; if (ok) bd.replaceChildren(icon("mark", "s"), el("span", { text: "Marked " + len }));
}
function setStart(t) { C.start = clampT(t); if (C.end !== null && C.end <= C.start) C.end = null; renderMarks(); }
function setEnd(t) { C.end = clampT(t); if (C.start === null) C.start = 0; renderMarks(); }
function typed(input, which) {
  if (!input.value.trim()) { C[which] = null; renderMarks(); return; }
  const t = parseTime(input.value);
  if (t === null) { $("markNote").textContent = "'" + input.value + "' is not a time. Write it like 1:23 or 1:02:03.5 or 75."; return; }
  if (which === "start") setStart(t); else setEnd(t);
}
function addClip() {
  if (!selOk()) { $("markNote").textContent = "Mark a start and an end first."; return; }
  C.clips.push({ start: +C.start.toFixed(3), end: +C.end.toFixed(3), name: "clip" + String(C.clips.length + 1).padStart(2, "0"), on: false });
  C.start = C.end = null;
  saveClips(); renderAll();
  toast("Clip added · " + C.clips.length + " clip" + (C.clips.length > 1 ? "s" : "") + ", " + fmtTime(totalOf(C.clips)) + " in all");
}
const totalOf = (arr) => arr.reduce((a, c) => a + (c.end - c.start), 0);

// ------------------------------------------------------------------ clip list
function chosen() { const on = C.clips.filter((c) => c.on); return on.length ? on : C.clips; }
function renderClips() {
  const box = $("clips"); box.replaceChildren();
  if (!C.clips.length) box.append(el("p", { class: "hint", text: C.info ? "No clips yet. Mark a start and an end, then Add clip. Or drag across the strip in the timeline." : "Choose a video first." }));
  C.clips.forEach((c, i) => {
    const tick = el("input", { type: "checkbox", class: "ck", "aria-label": "Tick " + c.name }); tick.checked = !!c.on;
    tick.addEventListener("change", () => { c.on = tick.checked; saveClips(); renderAll(); });
    const name = el("input", { class: "input slim", value: c.name, maxlength: "60", "aria-label": "Name of clip " + (i + 1) });
    name.addEventListener("change", () => { c.name = name.value.trim() || c.name; saveClips(); renderLane(); });
    const mv = (d, ic, title) => btn("", { icon: ic, cls: "ghost icon sm", title, onclick: () => { const j = i + d; if (j < 0 || j >= C.clips.length) return; [C.clips[i], C.clips[j]] = [C.clips[j], C.clips[i]]; saveClips(); renderAll(); } });
    box.append(el("div", { class: "item clip-row" + (c.on ? " on" : "") },
      tick,
      el("div", { class: "t" }, name, el("small", { class: "num", text: fmtTime(c.start) + " – " + fmtTime(c.end) + " · " + fmtTime(c.end - c.start, 2) })),
      el("div", { class: "acts" },
        btn("", { icon: "play", cls: "ghost icon sm", title: "Play", onclick: () => playRange(c.start, c.end) }),
        mv(-1, "up", "Earlier in the joined video"), mv(1, "down", "Later in the joined video"),
        btn("", { icon: "mark", cls: "ghost icon sm", title: "Change: put it back in the marks", onclick: () => { C.clips.splice(i, 1); C.start = c.start; C.end = c.end; saveClips(); renderAll(); seek(c.start); } }),
        btn("", { icon: "trash", cls: "ghost icon sm", title: "Delete", onclick: () => { C.clips.splice(i, 1); saveClips(); renderAll(); } }))));
  });
  const all = C.clips.length, on = C.clips.filter((c) => c.on).length, use = chosen();
  $("clipTotals").textContent = all ? all + " · " + fmtTime(totalOf(C.clips)) : "";
  $("iCount").textContent = String(all);
  $("makeBox").hidden = !all;
  $("makeWhat").textContent = on ? on + " ticked clip" + (on > 1 ? "s" : "") : "All " + all + " clip" + (all > 1 ? "s" : "");
  $("makeLen").textContent = fmtTime(totalOf(use));
  $("joinLabel").textContent = "Join " + use.length + " · " + fmtTime(totalOf(use), 0);
  $("sepLabel").textContent = use.length + " separate";
  $("joinBtn").disabled = use.length < 2;
}
async function make(join) {
  const use = chosen(); if (!use.length) return;
  $("makeNote").textContent = "Starting…";
  try {
    const r = await api("/api/clips", { method: "POST", json: { input: C.file, clips: use.map((c) => ({ start: c.start, end: c.end, name: c.name })), join, quality: C.quality } });
    $("makeNote").textContent = "";
    toast(join ? "Joining " + use.length + " clips into one video (" + fmtTime(totalOf(use)) + ")" : "Making " + r.jobs.length + " video" + (r.jobs.length > 1 ? "s" : ""));
    Exports.show();
  } catch (e) { $("makeNote").textContent = errText(e); }
}

// ------------------------------------------------------------------ timeline (the whole source video)
function px(t) { return Math.round(t * C.pps) + 16; }
function timeAt(x) { return (x - 16) / C.pps; }
function setZoom(pps, anchor) {
  const sc = $("tlScroll"), t = anchor === undefined ? video.currentTime || 0 : anchor, before = px(t) - sc.scrollLeft;
  C.pps = clamp(pps, 0.02, 200);
  $("zoom").value = String(clamp(Math.log(C.pps / 0.02) / Math.log(10000) * 100, 0, 100)); paintRange($("zoom"));
  renderLane(); sc.scrollLeft = px(t) - before;
}
function fitZoom() { if (!C.info) return; setZoom(($("tlScroll").clientWidth - 40) / Math.max(1, dur()), 0); $("tlScroll").scrollLeft = 0; }
function renderRuler() {
  const r = $("ruler"); r.replaceChildren();
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const major = steps.find((s) => s * C.pps >= 90) || 3600;
  const minor = steps.slice().reverse().find((s) => s < major && s * C.pps >= 12 && (major / s) % 1 === 0) || major;
  const sc = $("tlScroll"), a = Math.max(0, timeAt(sc.scrollLeft) - major), b = Math.min(dur(), timeAt(sc.scrollLeft + sc.clientWidth) + major);
  for (let t = Math.floor(a / minor) * minor; t <= b + 1e-6; t += minor) {
    const maj = Math.abs(t / major - Math.round(t / major)) < 1e-6;
    const tk = el("i", { class: maj ? "maj" : "" }); tk.style.left = px(t) + "px"; r.append(tk);
    if (maj) { const s = el("span", { text: fmtTime(t, 0) }); s.style.left = px(t) + "px"; r.append(s); }
  }
}
function renderLane() {
  const sc = $("tlScroll");
  $("tlInner").style.width = Math.max(sc.clientWidth, px(dur()) + 60) + "px";
  renderRuler();
  // thumbnail strip, only what is on screen
  const strip = $("stripAll"); strip.replaceChildren();
  if (C.info && C.info.has_video) {
    const h = 64, ar = C.info.width / C.info.height || 16 / 9, w = Math.round(h * ar);
    strip.style.left = px(0) + "px"; strip.style.width = (px(dur()) - px(0)) + "px";
    const first = Math.max(0, Math.floor((sc.scrollLeft - 16) / w)), last = Math.ceil((sc.scrollLeft + sc.clientWidth) / w);
    for (let k = first; k <= last; k++) {
      const t0 = k * w / C.pps; if (t0 >= dur()) break;
      const at = Math.min(dur() - 0.05, t0 + w / C.pps / 2);
      const img = el("img", { alt: "", draggable: "false", src: thumbUrl(C.file, Math.max(0, Math.floor(at * 2) / 2)) });
      img.style.left = (k * w) + "px"; img.style.width = w + "px"; strip.append(img);
    }
  }
  // clips lane
  const lc = $("laneC"); lc.replaceChildren();
  C.clips.forEach((c, i) => {
    const d = el("div", { class: "lane-item cliptag" + (c.on ? " on" : ""), "data-i": String(i), title: c.name + " · " + fmtTime(c.end - c.start, 2) },
      el("div", { class: "h l", "data-edge": "s" }), el("span", { text: c.name + " · " + fmtTime(c.end - c.start, 1) }), el("div", { class: "h r", "data-edge": "e" }));
    d.style.left = px(c.start) + "px"; d.style.width = Math.max(6, px(c.end) - px(c.start)) + "px"; lc.append(d);
  });
  renderMarks(); renderHead();
}
function renderAll() { renderClips(); renderLane(); }

function onTimelineDown(e) {
  if (!C.info || e.button !== 0) return;
  const sc = $("tlScroll"), rect = sc.getBoundingClientRect();
  const tAt = (ev) => clampT(timeAt(ev.clientX - rect.left + sc.scrollLeft));
  sc.setPointerCapture(e.pointerId);
  const t0 = tAt(e);
  let drag;
  const edge = e.target.dataset && e.target.dataset.edge;
  const tag = e.target.closest(".cliptag");
  if (e.target.closest("#ruler")) drag = { kind: "seek" };
  else if (tag) {
    const c = C.clips[+tag.dataset.i];
    drag = edge ? { kind: "clipEdge", c, edge } : { kind: "clip", c };
  } else if (edge && e.target.closest("#markSel")) drag = { kind: "markEdge", edge };
  else if (e.target.closest("#laneV")) drag = { kind: "mark", from: t0 };
  else drag = { kind: "seek" };
  drag.moved = false;
  if (drag.kind === "seek") seek(t0);
  const tip = (text, ev) => { let t = $("tlTip"); if (!t) { t = el("div", { class: "tip", id: "tlTip" }); document.body.append(t); } t.hidden = false; t.textContent = text; t.style.left = (ev.clientX + 14) + "px"; t.style.top = (ev.clientY - 34) + "px"; };
  const move = (ev) => {
    const t = tAt(ev);
    if (!drag.moved && Math.abs(ev.clientX - e.clientX) < 3) return;
    drag.moved = true;
    if (drag.kind === "seek") seek(t);
    else if (drag.kind === "mark") { C.start = Math.min(drag.from, t); C.end = Math.max(drag.from, t); renderMarks(); tip("Marked " + fmtTime(C.end - C.start, 2), ev); seek(t); }
    else if (drag.kind === "markEdge") {
      if (drag.edge === "s") C.start = Math.min(t, (C.end === null ? dur() : C.end) - 0.05); else C.end = Math.max(t, C.start + 0.05);
      renderMarks(); seek(t); tip((drag.edge === "s" ? "Start " : "End ") + fmtTime(t, 2) + " · " + (selOk() ? fmtTime(C.end - C.start, 2) : ""), ev);
    } else if (drag.kind === "clipEdge") {
      const c = drag.c;
      if (drag.edge === "s") c.start = +Math.min(t, c.end - 0.05).toFixed(3); else c.end = +Math.max(t, c.start + 0.05).toFixed(3);
      renderLane(); seek(t); tip(c.name + " · " + fmtTime(c.end - c.start, 2) + " · all clips " + fmtTime(totalOf(C.clips)), ev);
    }
  };
  const up = (ev) => {
    sc.removeEventListener("pointermove", move); sc.removeEventListener("pointerup", up); sc.removeEventListener("pointercancel", up);
    const t = $("tlTip"); if (t) t.hidden = true;
    if (drag.kind === "clipEdge" && drag.moved) { saveClips(); renderAll(); }
    if (drag.kind === "clip" && !drag.moved) { seek(drag.c.start); drag.c.on = !drag.c.on; saveClips(); renderAll(); }
    if (drag.kind === "mark" && !drag.moved) seek(tAt(ev));
  };
  sc.addEventListener("pointermove", move); sc.addEventListener("pointerup", up); sc.addEventListener("pointercancel", up);
}

// ------------------------------------------------------------------ wiring
function wire() {
  const drop = $("drop");
  drop.addEventListener("click", () => addFile(null));
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); drop.click(); } });
  dropTarget(drop, addFile);
  $("tPlay").addEventListener("click", togglePlay);
  $("frame").addEventListener("click", togglePlay);
  $("tBack").addEventListener("click", () => { video.pause(); seek(video.currentTime - frameLen()); });
  $("tFwd").addEventListener("click", () => { video.pause(); seek(video.currentTime + frameLen()); });
  $("tBack10").addEventListener("click", () => seek(video.currentTime - 10));
  $("tFwd10").addEventListener("click", () => seek(video.currentTime + 10));
  $("tMute").addEventListener("click", () => { video.muted = !video.muted; $("tMute").replaceChildren(icon(video.muted ? "mute" : "vol")); });
  $("toEditor").addEventListener("click", () => { if (C.file) openInEditor(C.file, 2); });
  $("setStart").addEventListener("click", () => setStart(video.currentTime));
  $("setEnd").addEventListener("click", () => setEnd(video.currentTime));
  $("startIn").addEventListener("change", () => typed($("startIn"), "start"));
  $("endIn").addEventListener("change", () => typed($("endIn"), "end"));
  $("playSel").addEventListener("click", () => playRange(C.start, C.end));
  $("addClip").addEventListener("click", addClip);
  $("quality").append(segmented([["best", "Best"], ["high", "High"], ["small", "Smaller"]], C.quality, (v) => { C.quality = v; }));
  $("joinBtn").addEventListener("click", () => make(true));
  $("sepBtn").addEventListener("click", () => make(false));
  video.addEventListener("seeked", renderHead);
  $("tlScroll").addEventListener("pointerdown", onTimelineDown);
  let raf = 0; $("tlScroll").addEventListener("scroll", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(renderLane); });
  $("tlScroll").addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); const r = $("tlScroll").getBoundingClientRect(); setZoom(C.pps * (e.deltaY < 0 ? 1.25 : 0.8), timeAt(e.clientX - r.left + $("tlScroll").scrollLeft)); }
    else if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.preventDefault(); $("tlScroll").scrollLeft += e.deltaY; }
  }, { passive: false });
  $("zoom").addEventListener("input", () => setZoom(0.02 * Math.pow(10000, parseFloat($("zoom").value) / 100)));
  $("bZoomIn").addEventListener("click", () => setZoom(C.pps * 1.6));
  $("bZoomOut").addEventListener("click", () => setZoom(C.pps / 1.6));
  $("bFit").addEventListener("click", fitZoom);
  $("tlResize").addEventListener("pointerdown", (e) => {
    const h0 = $("tl").offsetHeight, y0 = e.clientY; e.target.setPointerCapture(e.pointerId);
    const mv = (ev) => document.documentElement.style.setProperty("--tl", clamp(h0 - (ev.clientY - y0), 150, window.innerHeight * 0.6) + "px");
    const up = () => { e.target.removeEventListener("pointermove", mv); e.target.removeEventListener("pointerup", up); };
    e.target.addEventListener("pointermove", mv); e.target.addEventListener("pointerup", up);
  });
  new ResizeObserver(() => { if (C.info) { sizeFrame(); renderLane(); } }).observe($("stage"));
  document.addEventListener("keydown", (e) => {
    if (!C.info || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "button" || tag === "a" || tag === "summary" || document.querySelector(".dialog")) return;
    const k = e.key.toLowerCase();
    if (k === " ") { e.preventDefault(); togglePlay(); }
    else if (k === "i") setStart(video.currentTime);
    else if (k === "o") setEnd(video.currentTime);
    else if (k === "enter") addClip();
    else if (k === "arrowleft") { e.preventDefault(); seek(video.currentTime - 1); }
    else if (k === "arrowright") { e.preventDefault(); seek(video.currentTime + 1); }
    else if (k === ",") { video.pause(); seek(video.currentTime - frameLen()); }
    else if (k === ".") { video.pause(); seek(video.currentTime + frameLen()); }
    else if (k === "+" || k === "=") setZoom(C.pps * 1.6); else if (k === "-") setZoom(C.pps / 1.6);
  });
}

(async function init() {
  topBar("/clips");
  paintIcons(); wire();
  Exports.onFiles = () => loadFiles().catch(() => {});
  try {
    await loadFiles();
    const want = new URLSearchParams(location.search).get("open");
    if (want) openFile(want);
  } catch (e) { $("uploadNote").textContent = errText(e); }
  renderAll();
  requestAnimationFrame(loop);
})();
