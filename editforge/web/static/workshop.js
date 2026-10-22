"use strict";
// Clips page, phase 1: one movie, many clips. A clip can have several parts from different places in the movie.
// Each clip has its own basic picture and sound settings. "Save & make clips" turns every clip into a small video
// file; after that the big movie is no longer needed ("Free up the movie").
// The project is saved in the app (not in this browser), a moment after every change.

const C = {
  id: "", cs: null, info: null, states: {}, sel: "",      // sel: the chosen clip's id
  start: null, end: null, queue: null, stopAt: null,      // queue: parts still to play for "Play clip"
  pps: 10, usingLight: false, scenes: [], sceneState: "none",
  saveTimer: 0, saving: null, pollTimer: 0, tab: "parts",
};
const video = $("player");
const dur = () => (C.info ? C.info.duration : 0);
const frameLen = () => (C.info && C.info.fps_float ? 1 / C.info.fps_float : 0.04);
const clampT = (t) => clamp(t, 0, dur());
const clips = () => (C.cs ? C.cs.clips : []);
const selClip = () => clips().find((c) => c.id === C.sel) || null;
const clipLen = (c) => c.parts.reduce((a, p) => a + p.end - p.start, 0) / (c.speed || 1);
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, "0")).join("");
const canEdit = () => !!C.info;

// ------------------------------------------------------------------ saving (a moment after each change)
function changed() {
  clearTimeout(C.saveTimer);
  C.saveTimer = setTimeout(save, 600);
  renderClips();
}
async function save() {
  clearTimeout(C.saveTimer); C.saveTimer = 0;
  if (!C.cs) return;
  const body = { name: C.cs.name, clips: C.cs.clips };
  const run = (async () => {
    try {
      const v = await api("/api/clipsets/" + C.id, { method: "PUT", json: body });
      // keep what the person typed since; take the app's states (made / out of date)
      C.states = v.states;
      for (const c of v.clipset.clips) { const mine = clips().find((x) => x.id === c.id); if (mine) mine.made = c.made; }
      $("makeNote").textContent = "";
      renderClips();
    } catch (e) { $("makeNote").textContent = "Not saved: " + errText(e); }
  })();
  C.saving = run;
  await run;
}
async function flush() { if (C.saveTimer) { await save(); } else if (C.saving) await C.saving; }
// Leaving the page right after a change: send it anyway (keepalive lets the request finish after the page is gone).
window.addEventListener("pagehide", () => {
  if (!C.saveTimer || !C.cs) return;
  clearTimeout(C.saveTimer); C.saveTimer = 0;
  try {
    fetch("/api/clipsets/" + C.id, { method: "PUT", keepalive: true, headers: { "X-EditForge-Token": TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify({ name: C.cs.name, clips: C.cs.clips }) });
  } catch (e) { /* nothing more to do */ }
});

// ------------------------------------------------------------------ opening the project
function banner(text, k) {
  const b = $("banner"); if (!text) { b.hidden = true; return; }
  b.hidden = false; $("bannerText").textContent = text; $("bannerBar").style.width = Math.round((k || 0) * 100) + "%";
}
function emptyView(title, hint, buttons) {
  $("empty").hidden = false; $("emptyTitle").textContent = title; $("emptyHint").textContent = hint || "";
  $("emptyBtns").replaceChildren(...(buttons || []));
}
async function openClipset(id) {
  C.id = id;
  let v;
  try { v = await api("/api/clipsets/" + id); } catch (e) { emptyView("This clip project could not be opened.", errText(e), [el("a", { class: "btn", href: "/" }, icon("home", "s"), el("span", { text: "Home" }))]); return; }
  C.cs = v.clipset; C.info = v.info; C.states = v.states;
  document.title = C.cs.name + " · Clips · Nyenz Edit";
  $("csName").value = C.cs.name;
  if (C.cs.clips.length) C.sel = C.cs.clips[0].id;
  if (!C.info) {
    $("suite").classList.add("no-movie");
    requestAnimationFrame(sizeFrame);
    emptyView(C.cs.freed ? "The movie was freed to save space." : "The movie of this project is not where it was.",
      C.cs.freed ? "Your made clips are on the left: watch them, or open one in Edit." : "It was moved or deleted. Your made clips still work.", []);
  } else {
    $("empty").hidden = true;
    const i = C.info;
    $("tTotal").textContent = fmtTime(i.duration); $("iLen").textContent = fmtTime(i.duration, 0);
    sizeFrame();
    if (i.preview && i.preview.state === "ready") useLight();
    else {
      if (i.plays_in_browser) video.src = mediaUrl(C.cs.source, false);
      let quick = false;
      if (i.has_video) ensurePreview(C.cs.source, i, (st) => {
        if (st.state === "running" && !i.plays_in_browser) banner(quick ? "Playing a quick copy. Making a lighter one for smoother jumping… " + Math.round(st.progress * 100) + "%"
          : "This movie's sound or picture cannot play in a browser (" + i.playback_note + "). Making a copy you can watch… " + Math.round(st.progress * 100) + "%", st.progress);
        if (st.state === "failed") banner("A light copy could not be made: " + st.error, 0);
      }, !i.plays_in_browser, () => { if (!i.plays_in_browser) { quick = true; useQuick(); } }).then((ok) => { if (ok) { banner(null); useLight(); } });
    }
    sceneStatus(false);
  }
  for (const b of ["setStart", "setEnd", "startIn", "endIn", "addNew", "addPart", "playSel", "sceneBtn", "playClip", "newClip"]) $(b).disabled = !canEdit();
  fitZoom(); renderAll(); poll();
}
function useQuick() {
  const at = video.currentTime || 0;
  video.src = mediaUrl(C.cs.source, "q");
  video.addEventListener("loadedmetadata", () => { video.currentTime = Math.min(at, dur()); }, { once: true });
}
function useLight() {
  const at = video.currentTime || 0;
  C.usingLight = true;
  video.src = mediaUrl(C.cs.source, true);
  video.addEventListener("loadedmetadata", () => { video.currentTime = Math.min(at, dur()); }, { once: true });
}
video.addEventListener("error", () => {
  if (!C.info || !video.getAttribute("src")) return;
  if (C.usingLight && C.info.plays_in_browser) { C.usingLight = false; video.src = mediaUrl(C.cs.source, false); return; }
  if (!C.usingLight) banner("This browser cannot play this movie directly. Making a light copy…", 0);
});
function sizeFrame() {
  const st = $("stage"), f = $("frame");
  const a = C.info && C.info.width && C.info.height ? C.info.width / C.info.height : 16 / 9;
  const W = st.clientWidth - 36, H = st.clientHeight - 20;
  let w = W, h = W / a; if (h > H) { h = H; w = H * a; }
  f.style.width = Math.max(40, Math.floor(w)) + "px"; f.style.height = Math.max(40, Math.floor(h)) + "px";
}

// ------------------------------------------------------------------ no project yet: the list of clip projects
async function chooser() {
  emptyView("Clip projects", "Open one, or start a new one from a movie.", []);
  let list = [];
  try { list = (await api("/api/clipsets")).clipsets.filter((p) => !p.trashed); } catch (e) { /* shown below */ }
  const box = $("clips"); box.replaceChildren();
  $("csName").closest(".field").hidden = true; $("newClip").hidden = true; $("makeBox").hidden = true;
  box.append(btn("New clip project", { icon: "plus", cls: "primary wide", onclick: newProject }));
  for (const p of list) box.append(el("div", { class: "item", onclick: () => { location.href = "/clips?c=" + p.id; } },
    el("div", { class: "t" }, el("b", { text: p.name }), el("small", { class: "num", text: p.clips + " clips · " + p.made + " made" }))));
}
async function newProject(fileId) {
  try {
    const d = typeof fileId === "string" ? { id: fileId } : await chooseFile("video");
    if (!d) return;
    const r = await api("/api/clipsets", { method: "POST", json: { source: d.id } });
    location.href = "/clips?c=" + r.clipset.id;
  } catch (e) { toast(errText(e), true); }
}

// ------------------------------------------------------------------ playing
function seek(t) { if (C.info) { C.stopAt = null; C.queue = null; video.currentTime = clampT(t); renderHead(); } }
function togglePlay() { if (!C.info) return; C.stopAt = null; C.queue = null; if (video.paused) { const p = video.play(); if (p && p.catch) p.catch(() => {}); } else video.pause(); }
function playRange(a, b) { if (!C.info) return; C.queue = null; video.currentTime = a; C.stopAt = b; const p = video.play(); if (p && p.catch) p.catch(() => {}); }
/** Play the chosen clip: its parts one after the other, with its speed, look and volume. */
function playClip() {
  const c = selClip();
  if (!c || !c.parts.length) { $("markNote").textContent = "Choose a clip on the left first."; return; }
  C.queue = c.parts.slice(1);
  video.currentTime = c.parts[0].start; C.stopAt = c.parts[0].end;
  const p = video.play(); if (p && p.catch) p.catch(() => {});
}
video.addEventListener("play", () => $("tPlay").replaceChildren(icon("pause")));
video.addEventListener("pause", () => $("tPlay").replaceChildren(icon("play")));
function loop() {
  if (C.stopAt !== null && !video.paused && video.currentTime >= C.stopAt) {
    if (C.queue && C.queue.length) { const nx = C.queue.shift(); video.currentTime = nx.start; C.stopAt = nx.end; }
    else { video.pause(); video.currentTime = C.stopAt; C.stopAt = null; C.queue = null; }
  }
  if (!video.paused) { renderHead(); followHead(); }
  requestAnimationFrame(loop);
}
function renderHead() {
  const t = video.currentTime || 0;
  $("tNow").textContent = fmtTime(t);
  $("playhead").style.left = px(t) + "px";
  if (C.start !== null && C.end === null && t > C.start) $("selLen").textContent = fmtTime(t - C.start) + " so far";
}
function followHead() {
  const sc = $("tlScroll"), x = px(video.currentTime);
  if (x > sc.scrollLeft + sc.clientWidth - 30 || x < sc.scrollLeft) sc.scrollLeft = x - 60;
}
/** The chosen clip's look and sound, live in the viewer (what a browser can show). */
function applyLive() {
  const c = selClip();
  const col = c ? c.color : { brightness: 0, contrast: 1, saturation: 1 };
  video.style.filter = c ? "brightness(" + (1 + col.brightness).toFixed(3) + ") contrast(" + col.contrast + ") saturate(" + col.saturation + ")" : "";
  video.playbackRate = c ? c.speed : 1;
  video.volume = c ? clamp(c.volume, 0, 1) : 1;
  video.muted = c ? c.mute || $("tMute").dataset.off === "1" : $("tMute").dataset.off === "1";
}

// ------------------------------------------------------------------ marks and scenes
function selOk() { return C.start !== null && C.end !== null && C.end > C.start; }
function snapT(t) {
  if (!$("snap").checked || !C.scenes.length) return t;
  const tol = Math.max(0.25, 8 / C.pps);
  let best = t, bd = tol;
  for (const s of [0, ...C.scenes, dur()]) { const d = Math.abs(s - t); if (d < bd) { bd = d; best = s; } }
  return best;
}
function sceneAt(t) {
  const edges = [0, ...C.scenes.filter((s) => s > 0 && s < dur()), dur()];
  for (let i = 0; i < edges.length - 1; i++) if (t >= edges[i] && t < edges[i + 1]) return [edges[i], edges[i + 1]];
  return [0, dur()];
}
function renderMarks() {
  const s = C.start, e = C.end, ok = selOk(), c = selClip();
  if (document.activeElement !== $("startIn")) $("startIn").value = s === null ? "" : fmtTime(s, 2);
  if (document.activeElement !== $("endIn")) $("endIn").value = e === null ? "" : fmtTime(e, 2);
  const len = ok ? fmtTime(e - s, 2) : "–";
  $("selLen").textContent = len;
  $("iSel").textContent = ok ? "Marked " + len : "";
  $("playSel").disabled = !ok; $("addNew").disabled = !ok || !canEdit();
  $("addPart").disabled = !ok || !c || !canEdit();
  $("addPartLabel").textContent = c ? "Add part to " + c.name : "Add part to this clip";
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
function setStart(t) { C.start = snapT(clampT(t)); if (C.end !== null && C.end <= C.start) C.end = null; renderMarks(); }
function setEnd(t) { C.end = snapT(clampT(t)); if (C.start === null) C.start = 0; renderMarks(); }
function typed(input, which) {
  if (!input.value.trim()) { C[which] = null; renderMarks(); return; }
  const t = parseTime(input.value);
  if (t === null) { $("markNote").textContent = "'" + input.value + "' is not a time. Write it like 1:23 or 1:02:03.5 or 75."; return; }
  if (which === "start") { C.start = clampT(t); if (C.end !== null && C.end <= C.start) C.end = null; } else { C.end = clampT(t); if (C.start === null) C.start = 0; }
  renderMarks();
}
function takeMarks() { const p = { start: +C.start.toFixed(3), end: +C.end.toFixed(3) }; C.start = C.end = null; return p; }
function addNewClip() {
  if (!selOk()) { $("markNote").textContent = "Mark a start and an end first."; return; }
  const n = clips().length + 1;
  const c = { id: newId(), name: "clip" + String(n).padStart(2, "0"), parts: [takeMarks()], color: { brightness: 0, contrast: 1, saturation: 1 },
    speed: 1, volume: 1, mute: false, settings: { shape: "original", fade_in: 0, fade_out: 0, noise: "off", voice_boost: false, compress: false, bass: 0, treble: 0 }, made: null };
  C.cs.clips.push(c); C.sel = c.id;
  changed(); renderAll();
  toast("Clip added · " + clips().length + " clip" + (clips().length > 1 ? "s" : "") + ". Add more parts to it, or mark the next clip.");
}
function addPartTo() {
  const c = selClip();
  if (!c) { addNewClip(); return; }
  if (!selOk()) { $("markNote").textContent = "Mark a start and an end first."; return; }
  c.parts.push(takeMarks());
  changed(); renderAll();
  toast(c.name + " now has " + c.parts.length + " parts · " + fmtTime(clipLen(c), 1));
}
async function sceneStatus(start) {
  if (!C.info || !C.info.has_video) { $("sceneBtn").hidden = true; return; }
  try {
    const st = start ? await api("/api/scenes", { method: "POST", json: { id: C.cs.source } }) : await api("/api/scenes?id=" + encodeURIComponent(C.cs.source));
    C.sceneState = st.state;
    if (st.state === "ready") { C.scenes = st.scenes; $("sceneLabel").textContent = st.scenes.length + 1 + " scenes"; $("sceneBtn").disabled = true; renderLane(); }
    else if (st.state === "running") { $("sceneLabel").textContent = "Finding scenes… " + Math.round(st.progress * 100) + "%"; $("sceneBtn").disabled = true; setTimeout(() => sceneStatus(false), 1000); }
    else if (st.state === "failed") { $("sceneLabel").textContent = "Find scenes"; $("sceneBtn").disabled = false; toast("Scenes could not be found: " + st.error, true); }
    else { $("sceneLabel").textContent = "Find scenes"; $("sceneBtn").disabled = false; }
  } catch (e) { toast(errText(e), true); }
}

// ------------------------------------------------------------------ the clips list (left)
const STATE_TEXT = { none: "Not made", queued: "Waiting", running: "Making", done: "Made", failed: "Failed", old: "Changed: make again" };
function stateOf(c) { return C.states[c.id] || { state: "none", file: "", progress: 0 }; }
function renderClips() {
  const box = $("clips"); box.replaceChildren();
  if (!clips().length) box.append(el("p", { class: "hint", text: canEdit() ? "No clips yet. Mark a part of the movie (drag on the strip below), then press Add as new clip." : "This project has no clips." }));
  clips().forEach((c, i) => {
    const st = stateOf(c);
    const chip = el("span", { class: "st-chip " + st.state, text: STATE_TEXT[st.state] + (st.state === "running" ? " " + Math.round(st.progress * 100) + "%" : "") });
    const acts = el("div", { class: "acts" });
    if (st.file && (st.state === "done" || st.state === "old")) {
      acts.append(btn("", { icon: "eye", cls: "ghost icon sm", title: "Watch the made clip", onclick: (e) => { e.stopPropagation(); watch(c, st.file); } }),
        btn("", { icon: "layers", cls: "ghost icon sm", title: "Edit this clip (phase 2)", onclick: async (e) => { e.stopPropagation(); await flush(); openInEditor(st.file, 2); } }));
    }
    if (canEdit()) {
      acts.append(btn("", { icon: "up", cls: "ghost icon sm", title: "Move up", onclick: (e) => { e.stopPropagation(); move(i, -1); } }),
        btn("", { icon: "trash", cls: "ghost icon sm", title: "Delete this clip", onclick: async (e) => {
          e.stopPropagation();
          if (!(await askYes("Delete the clip '" + c.name + "'?" + (st.file ? " Its made file stays in Exports." : ""), "Delete", true))) return;
          C.cs.clips.splice(i, 1); if (C.sel === c.id) C.sel = clips()[0] ? clips()[0].id : ""; changed(); renderAll();
        } }));
    }
    const pic = el("div", { class: "cthumb" });
    const src = st.file && !C.info ? thumbUrl(st.file, 0.3) : C.info && C.info.has_video ? thumbUrl(C.cs.source, Math.floor((c.parts[0].start + 0.3) * 2) / 2) : "";
    if (src) pic.append(el("img", { alt: "", loading: "lazy", src }));
    box.append(el("div", { class: "item clip-row" + (c.id === C.sel ? " on" : ""), onclick: () => select(c.id) },
      pic, el("div", { class: "t" }, el("b", { text: c.name }), el("small", { class: "num", text: fmtTime(clipLen(c), 1) + " · " + c.parts.length + " part" + (c.parts.length > 1 ? "s" : "") }), chip), acts));
  });
  renderMake();
  $("iCount").textContent = String(clips().length);
}
function move(i, d) { const j = i + d; if (j < 0 || j >= clips().length) return; [C.cs.clips[i], C.cs.clips[j]] = [C.cs.clips[j], C.cs.clips[i]]; changed(); }
function select(id) { C.sel = id; C.queue = null; renderAll(); const c = selClip(); if (c && C.info) seek(c.parts[0].start); }
function watch(c, file) {
  dialogBox(c.name, [el("div", { class: "watch" }, miniPlayer(mediaUrl(file, false)))], [["Close", null, "ghost"]], (done, dlg) => dlg.classList.add("wide"));
}
function renderMake() {
  const list = clips(), states = list.map((c) => stateOf(c).state);
  const made = states.filter((s) => s === "done").length, busy = states.filter((s) => s === "queued" || s === "running").length;
  const todo = list.length - made - busy;
  $("clipTotals").textContent = list.length ? list.length + " · " + fmtTime(list.reduce((a, c) => a + clipLen(c), 0)) : "";
  $("makeWhat").textContent = !list.length ? "No clips yet" : made === list.length ? "All " + list.length + " made" : made + " of " + list.length + " made";
  $("makeLen").textContent = busy ? "making " + busy : "";
  const bar = $("makeProg");
  bar.hidden = !busy;
  if (busy) {
    const p = list.reduce((a, c) => { const s = stateOf(c); return a + (s.state === "done" ? 1 : s.state === "running" ? s.progress : 0); }, 0) / Math.max(1, made + busy);
    bar.firstChild.style.width = Math.round(p * 100) + "%";
  }
  $("makeBtn").disabled = !canEdit() || !list.length || todo === 0;
  $("makeLabel").textContent = todo > 0 && made + busy > 0 ? "Save & make " + todo + " more" : "Save & make clips";
  $("freeBtn").hidden = !(list.length && made === list.length && C.info && !C.cs.freed);
  $("makeBox").hidden = !C.cs;
}
async function makeClips() {
  $("makeNote").textContent = "Starting…";
  try {
    await flush();
    const v = await api("/api/clipsets/" + C.id + "/make", { method: "POST", json: {} });
    C.states = v.states;
    $("makeNote").textContent = v.started.length ? "Making " + v.started.length + " clip" + (v.started.length > 1 ? "s" : "") + ". You can keep working." : "Nothing new to make.";
    renderClips(); poll();
  } catch (e) { $("makeNote").textContent = errText(e); }
}
async function freeMovie() {
  try {
    await flush();
    let r = await api("/api/clipsets/" + C.id + "/free", { method: "POST", json: {} });
    if (r.ask) {
      const ok = await askYes("All clips are made. Delete the movie file from your computer to free " + fmtSize(r.size) + "?\n\n" + r.path +
        "\n\nThis cannot be undone. Your made clips are kept.", "Delete the movie", true);
      if (!ok) return;
      r = await api("/api/clipsets/" + C.id + "/free", { method: "POST", json: { delete_original: true } });
    }
    toast("Freed " + fmtSize(r.freed || 0) + ". Your clips are kept.");
    location.reload();
  } catch (e) { toast(errText(e), true); }
}
/** While clips are being made, ask for their state every second or so. */
async function poll() {
  clearTimeout(C.pollTimer);
  if (!C.cs) return;
  const busy = () => clips().some((c) => ["queued", "running"].includes(stateOf(c).state));
  if (!busy()) return;
  C.pollTimer = setTimeout(async () => {
    try {
      const v = await api("/api/clipsets/" + C.id);
      C.states = v.states;
      for (const c of v.clipset.clips) { const mine = clips().find((x) => x.id === c.id); if (mine) mine.made = c.made; }
      renderClips();
      if (!busy()) toast("All clips are made. Find them in Exports, or press the layers button to edit one.");
    } catch (e) { /* try again */ }
    poll();
  }, 1200);
}

// ------------------------------------------------------------------ the chosen clip (right)
function renderTabs() {
  const tabs = $("clipTabs"); tabs.replaceChildren();
  for (const [k, label] of [["parts", "Parts"], ["video", "Video"], ["sound", "Sound"]]) {
    tabs.append(el("button", { class: "tab" + (C.tab === k ? " on" : ""), type: "button", role: "tab", onclick: () => { C.tab = k; renderSide(); } }, el("span", { text: label })));
  }
  for (const p of document.querySelectorAll("[data-tab]")) p.hidden = p.dataset.tab !== C.tab;
}
function renderSide() {
  renderTabs();
  const c = selClip();
  $("clipHead").textContent = c ? c.name : "Mark a part";
  $("clipLen").textContent = c ? fmtTime(clipLen(c), 1) : "";
  $("clipNameBox").hidden = !c;
  if (c && document.activeElement !== $("clipName")) $("clipName").value = c.name;
  // parts
  const box = $("parts"); box.replaceChildren();
  $("partsBox").hidden = !c;
  if (c) c.parts.forEach((p, i) => {
    const mv = (d, ic, title) => dis(btn("", { icon: ic, cls: "ghost icon sm", title, onclick: () => {
      const j = i + d; if (j < 0 || j >= c.parts.length) return; [c.parts[i], c.parts[j]] = [c.parts[j], c.parts[i]]; changed(); renderAll(); } }), !canEdit());
    box.append(el("div", { class: "item part-row" },
      el("span", { class: "part-n num", text: String(i + 1) }),
      el("div", { class: "t" }, el("b", { class: "num", text: fmtTime(p.start) + " – " + fmtTime(p.end) }), el("small", { class: "num", text: fmtTime(p.end - p.start, 2) })),
      el("div", { class: "acts" },
        btn("", { icon: "play", cls: "ghost icon sm", title: "Play this part", onclick: () => playRange(p.start, p.end) }),
        mv(-1, "up", "Earlier in the clip"), mv(1, "down", "Later in the clip"),
        dis(btn("", { icon: "mark", cls: "ghost icon sm", title: "Change it: put it back in the marks", onclick: () => {
          c.parts.splice(i, 1); C.start = p.start; C.end = p.end;
          if (!c.parts.length) { C.cs.clips = clips().filter((x) => x !== c); C.sel = ""; }
          changed(); renderAll(); seek(p.start); } }), !canEdit()),
        dis(btn("", { icon: "trash", cls: "ghost icon sm", title: "Remove this part", onclick: () => { c.parts.splice(i, 1); changed(); renderAll(); } }), !canEdit() || c.parts.length < 2))));
  });
  // video and sound
  $("noClipV").hidden = !!c; $("videoBox").hidden = !c; $("noClipS").hidden = !!c; $("soundBox").hidden = !c;
  if (c) {
    setRange("vBri", c.color.brightness, (v) => (v > 0 ? "+" : "") + Math.round(v * 100));
    setRange("vCon", c.color.contrast, (v) => Math.round(v * 100) + "%");
    setRange("vSat", c.color.saturation, (v) => (v === 0 ? "grey" : Math.round(v * 100) + "%"));
    $("vSpdV").textContent = c.speed + "×";
    Speed.set(String(c.speed));
    $("vShape").value = c.settings.shape || "original";
    $("aMute").checked = !!c.mute;
    setRange("aVol", c.volume, (v) => Math.round(v * 100) + "%");
    if (document.activeElement !== $("aFin")) $("aFin").value = String(c.settings.fade_in || 0);
    if (document.activeElement !== $("aFout")) $("aFout").value = String(c.settings.fade_out || 0);
    Noise.set(c.settings.noise || "off");
    $("aVoice").checked = !!c.settings.voice_boost; $("aComp").checked = !!c.settings.compress;
    setRange("aBass", c.settings.bass || 0, (v) => (v > 0 ? "+" : "") + v + " dB");
    setRange("aTreb", c.settings.treble || 0, (v) => (v > 0 ? "+" : "") + v + " dB");
  }
  for (const id of ["vBri", "vCon", "vSat", "vShape", "aMute", "aVol", "aFin", "aFout", "aVoice", "aComp", "aBass", "aTreb", "vReset", "clipName"]) $(id).disabled = !canEdit();
  applyLive();
}
function setRange(id, v, fmt) { const r = $(id); r.value = String(v); paintRange(r); $(id + "V").textContent = fmt(+v); }
/** A segmented control that can be set from outside. */
function seg(host, options, onpick) { const node = segmented(options, "", onpick); host.append(node); return node; }
const dis = (b, off) => { b.disabled = !!off; return b; };
const Speed = seg($("vSpeed"), [["0.5", "0.5×"], ["0.75", "0.75×"], ["1", "1×"], ["1.5", "1.5×"], ["2", "2×"]], (v) => edit((c) => { c.speed = +v; }));
const Noise = seg($("aNoise"), [["off", "Off"], ["light", "Light"], ["medium", "Medium"], ["strong", "Strong"]], (v) => edit((c) => { c.settings.noise = v; }));
function edit(fn) { const c = selClip(); if (!c || !canEdit()) return; fn(c); changed(); renderSide(); renderLane(); }

// ------------------------------------------------------------------ timeline (the whole movie)
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
  const strip = $("stripAll"); strip.replaceChildren();
  if (C.info && C.info.has_video) {
    const h = 64, ar = C.info.width / C.info.height || 16 / 9, w = Math.round(h * ar);
    strip.style.left = px(0) + "px"; strip.style.width = (px(dur()) - px(0)) + "px";
    const first = Math.max(0, Math.floor((sc.scrollLeft - 16) / w)), last = Math.ceil((sc.scrollLeft + sc.clientWidth) / w);
    for (let k = first; k <= last; k++) {
      const t0 = k * w / C.pps; if (t0 >= dur()) break;
      const at = Math.min(dur() - 0.05, t0 + w / C.pps / 2);
      const img = el("img", { alt: "", draggable: "false", src: thumbUrl(C.cs.source, Math.max(0, Math.floor(at * 2) / 2)) });
      img.style.left = (k * w) + "px"; img.style.width = w + "px"; strip.append(img);
    }
  }
  // scenes lane: one block per scene (only what is on screen)
  const ls = $("laneS"); ls.replaceChildren();
  if (C.scenes.length) {
    const edges = [0, ...C.scenes, dur()], a = timeAt(sc.scrollLeft), b = timeAt(sc.scrollLeft + sc.clientWidth);
    for (let i = 0; i < edges.length - 1; i++) {
      if (edges[i + 1] < a || edges[i] > b) continue;
      const d = el("div", { class: "scene" + (i % 2 ? " odd" : ""), "data-a": String(edges[i]), "data-b": String(edges[i + 1]),
        title: "Scene " + (i + 1) + " · " + fmtTime(edges[i + 1] - edges[i], 1) + " · click to mark it, double-click to add it" }, el("span", { text: String(i + 1) }));
      d.style.left = px(edges[i]) + "px"; d.style.width = Math.max(2, px(edges[i + 1]) - px(edges[i]) - 1) + "px"; ls.append(d);
    }
  } else ls.append(el("span", { class: "lane-note", text: C.sceneState === "running" ? "Finding scenes…" : "Scenes: press Find scenes" }));
  // parts lane: every clip's parts; the chosen clip's are bright and numbered
  const lc = $("laneC"); lc.replaceChildren();
  clips().forEach((c, ci) => c.parts.forEach((p, pi) => {
    const on = c.id === C.sel;
    const d = el("div", { class: "lane-item cliptag" + (on ? " on" : ""), "data-c": c.id, "data-p": String(pi), title: c.name + " · part " + (pi + 1) + " · " + fmtTime(p.end - p.start, 2) },
      on && canEdit() ? el("div", { class: "h l", "data-edge": "s" }) : null, el("span", { text: (on ? (pi + 1) + " · " : "") + c.name }),
      on && canEdit() ? el("div", { class: "h r", "data-edge": "e" }) : null);
    d.style.left = px(p.start) + "px"; d.style.width = Math.max(6, px(p.end) - px(p.start)) + "px"; lc.append(d);
  }));
  renderMarks(); renderHead();
}
function renderAll() { renderClips(); renderSide(); renderLane(); }

function onTimelineDown(e) {
  if (!C.info || e.button !== 0) return;
  const sc = $("tlScroll"), rect = sc.getBoundingClientRect();
  const tAt = (ev) => clampT(timeAt(ev.clientX - rect.left + sc.scrollLeft));
  sc.setPointerCapture(e.pointerId);
  const t0 = tAt(e);
  let drag;
  const edge = e.target.dataset && e.target.dataset.edge;
  const tag = e.target.closest(".cliptag"), scene = e.target.closest(".scene");
  if (e.target.closest("#ruler")) drag = { kind: "seek" };
  else if (scene) drag = { kind: "scene", a: +scene.dataset.a, b: +scene.dataset.b };
  else if (tag) {
    const c = clips().find((x) => x.id === tag.dataset.c), p = c.parts[+tag.dataset.p];
    drag = edge ? { kind: "partEdge", c, p, edge } : { kind: "part", c, p };
  } else if (edge && e.target.closest("#markSel")) drag = { kind: "markEdge", edge };
  else if (e.target.closest("#laneV")) drag = { kind: "mark", from: snapT(t0) };
  else drag = { kind: "seek" };
  drag.moved = false;
  if (drag.kind === "seek") seek(t0);
  const tip = (text, ev) => { let t = $("tlTip"); if (!t) { t = el("div", { class: "tip", id: "tlTip" }); document.body.append(t); } t.hidden = false; t.textContent = text; t.style.left = (ev.clientX + 14) + "px"; t.style.top = (ev.clientY - 34) + "px"; };
  const move = (ev) => {
    const t = tAt(ev);
    if (!drag.moved && Math.abs(ev.clientX - e.clientX) < 3) return;
    drag.moved = true;
    if (drag.kind === "seek") seek(t);
    else if (drag.kind === "mark") { const s = snapT(t); C.start = Math.min(drag.from, s); C.end = Math.max(drag.from, s); renderMarks(); tip("Marked " + fmtTime(C.end - C.start, 2), ev); seek(t); }
    else if (drag.kind === "markEdge") {
      const s = snapT(t);
      if (drag.edge === "s") C.start = Math.min(s, (C.end === null ? dur() : C.end) - 0.05); else C.end = Math.max(s, C.start + 0.05);
      renderMarks(); seek(t); tip((drag.edge === "s" ? "Start " : "End ") + fmtTime(s, 2) + (selOk() ? " · " + fmtTime(C.end - C.start, 2) : ""), ev);
    } else if (drag.kind === "partEdge") {
      const p = drag.p, s = snapT(t);
      if (drag.edge === "s") p.start = +Math.min(s, p.end - 0.05).toFixed(3); else p.end = +Math.max(s, p.start + 0.05).toFixed(3);
      renderLane(); seek(t); tip(drag.c.name + " · part " + (drag.c.parts.indexOf(p) + 1) + " · " + fmtTime(p.end - p.start, 2), ev);
    }
  };
  const up = (ev) => {
    sc.removeEventListener("pointermove", move); sc.removeEventListener("pointerup", up); sc.removeEventListener("pointercancel", up);
    const t = $("tlTip"); if (t) t.hidden = true;
    if (drag.kind === "partEdge" && drag.moved) { changed(); renderAll(); }
    if (drag.kind === "part" && !drag.moved) { if (C.sel !== drag.c.id) select(drag.c.id); seek(drag.p.start); }
    if (drag.kind === "mark" && !drag.moved) seek(tAt(ev));
    if (drag.kind === "scene" && !drag.moved) { C.start = drag.a; C.end = drag.b; renderMarks(); seek(drag.a); }
  };
  sc.addEventListener("pointermove", move); sc.addEventListener("pointerup", up); sc.addEventListener("pointercancel", up);
}

// ------------------------------------------------------------------ wiring
function wire() {
  $("tPlay").addEventListener("click", togglePlay);
  $("frame").addEventListener("click", (e) => { if (!e.target.closest(".empty")) togglePlay(); });
  $("tBack").addEventListener("click", () => { video.pause(); seek(video.currentTime - frameLen()); });
  $("tFwd").addEventListener("click", () => { video.pause(); seek(video.currentTime + frameLen()); });
  $("tBack10").addEventListener("click", () => seek(video.currentTime - 10));
  $("tFwd10").addEventListener("click", () => seek(video.currentTime + 10));
  $("tMute").addEventListener("click", () => { const off = $("tMute").dataset.off === "1"; $("tMute").dataset.off = off ? "0" : "1"; $("tMute").replaceChildren(icon(off ? "vol" : "mute")); applyLive(); });
  $("playClip").addEventListener("click", playClip);
  $("setStart").addEventListener("click", () => setStart(video.currentTime));
  $("setEnd").addEventListener("click", () => setEnd(video.currentTime));
  $("startIn").addEventListener("change", () => typed($("startIn"), "start"));
  $("endIn").addEventListener("change", () => typed($("endIn"), "end"));
  $("playSel").addEventListener("click", () => playRange(C.start, C.end));
  $("addNew").addEventListener("click", addNewClip);
  $("addPart").addEventListener("click", addPartTo);
  $("newClip").addEventListener("click", () => { C.sel = ""; C.start = C.end = null; C.tab = "parts"; renderAll(); $("markNote").textContent = "Mark the first part of the new clip, then press Add as new clip."; });
  $("makeBtn").addEventListener("click", makeClips);
  $("freeBtn").addEventListener("click", freeMovie);
  $("sceneBtn").addEventListener("click", () => sceneStatus(true));
  $("snap").addEventListener("change", () => {});
  $("csName").addEventListener("change", () => { const n = $("csName").value.trim(); if (n && C.cs) { C.cs.name = n; changed(); } });
  $("clipName").addEventListener("change", () => edit((c) => { c.name = $("clipName").value.trim().slice(0, 60) || c.name; }));
  // picture
  const rng = (id, fn) => { $(id).addEventListener("input", () => { paintRange($(id)); const c = selClip(); if (c) { fn(c, +$(id).value); applyLive(); renderSideValues(); } });
    $(id).addEventListener("change", () => changed()); };
  rng("vBri", (c, v) => { c.color.brightness = v; });
  rng("vCon", (c, v) => { c.color.contrast = v; });
  rng("vSat", (c, v) => { c.color.saturation = v; });
  rng("aVol", (c, v) => { c.volume = v; });
  rng("aBass", (c, v) => { c.settings.bass = v; });
  rng("aTreb", (c, v) => { c.settings.treble = v; });
  $("vShape").addEventListener("change", () => edit((c) => { c.settings.shape = $("vShape").value; }));
  $("vReset").addEventListener("click", () => edit((c) => { c.color = { brightness: 0, contrast: 1, saturation: 1 }; c.speed = 1; c.settings.shape = "original"; }));
  $("aMute").addEventListener("change", () => edit((c) => { c.mute = $("aMute").checked; }));
  $("aVoice").addEventListener("change", () => edit((c) => { c.settings.voice_boost = $("aVoice").checked; }));
  $("aComp").addEventListener("change", () => edit((c) => { c.settings.compress = $("aComp").checked; }));
  const sec = (id, key) => $(id).addEventListener("change", () => edit((c) => { const v = parseFloat($(id).value); c.settings[key] = isFinite(v) ? clamp(v, 0, 10) : 0; }));
  sec("aFin", "fade_in"); sec("aFout", "fade_out");
  enhanceAll($("suite"));
  video.addEventListener("seeked", renderHead);
  $("tlScroll").addEventListener("pointerdown", onTimelineDown);
  $("tlScroll").addEventListener("dblclick", (e) => {
    const s = e.target.closest(".scene"); if (!s || !canEdit()) return;
    C.start = +s.dataset.a; C.end = +s.dataset.b; if (selClip()) addPartTo(); else addNewClip();
  });
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
    else if (k === "enter") addNewClip();
    else if (k === "a") addPartTo();
    else if (k === "p") playClip();
    else if (k === "arrowleft") { e.preventDefault(); seek(video.currentTime - 1); }
    else if (k === "arrowright") { e.preventDefault(); seek(video.currentTime + 1); }
    else if (k === ",") { video.pause(); seek(video.currentTime - frameLen()); }
    else if (k === ".") { video.pause(); seek(video.currentTime + frameLen()); }
    else if (k === "+" || k === "=") setZoom(C.pps * 1.6); else if (k === "-") setZoom(C.pps / 1.6);
  });
}
/** Only the numbers next to the sliders (while dragging). */
function renderSideValues() {
  const c = selClip(); if (!c) return;
  $("vBriV").textContent = (c.color.brightness > 0 ? "+" : "") + Math.round(c.color.brightness * 100);
  $("vConV").textContent = Math.round(c.color.contrast * 100) + "%";
  $("vSatV").textContent = c.color.saturation === 0 ? "grey" : Math.round(c.color.saturation * 100) + "%";
  $("aVolV").textContent = Math.round(c.volume * 100) + "%";
  $("aBassV").textContent = (c.settings.bass > 0 ? "+" : "") + c.settings.bass + " dB";
  $("aTrebV").textContent = (c.settings.treble > 0 ? "+" : "") + c.settings.treble + " dB";
}

(async function init() {
  topBar("/clips");
  paintIcons(); wire();
  requestAnimationFrame(loop);
  const params = new URLSearchParams(location.search);
  const id = params.get("c"), open = params.get("open");
  if (id) await openClipset(id);
  else if (open) await newProject(open);           // "cut clips from this video" (from Exports)
  else { renderTabs(); await chooser(); }
})();
