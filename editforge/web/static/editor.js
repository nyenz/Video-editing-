"use strict";
// The Editor: a video cut into pieces on a timeline, played live as you edit.
// Model: S.proj.pieces is the list the server renders. Pieces with off=true are not in the timeline
// (they are "removed" and can be put back). The timeline shows the other pieces, in order.
// Steps: S.proj.steps are pinned (locked) earlier steps; S.proj.pieces is the newest step, the one you edit.
// Every piece is a range of the PARENT video, so nothing is rendered between steps (see steps.js).

const S = {
  id: new URLSearchParams(location.search).get("p") || "",
  proj: null, info: null, files: [], recipes: [],
  sel: new Set(), anchor: null,             // selected piece objects
  undo: [], redo: [], saveTimer: null, saving: Promise.resolve(),
  pps: 40,                                   // timeline pixels per second of the finished video
  t: 0,                                      // playhead, seconds of the finished video
  playing: false, useLight: false, mutedAll: false,
  panel: "media",
  viewStep: null,                            // null: the newest step (editable); a number: an older, locked step shown
};
const MIN_PIECE = 0.1;
const LOOKS = [["hflip", "Flip", "fliph"], ["mirror", "Mirror", "mirror"], ["vflip", "Upside down", "flipv"], ["grayscale", "Black & white", "drop"],
  ["invert", "Invert", "palette"], ["sepia", "Sepia", "palette"], ["blur", "Blur", "drop"], ["sharpen", "Sharpen", "sparkles"], ["vignette", "Dark corners", "eye"]];
const LOOK_NAME = Object.fromEntries(LOOKS.map(([k, n]) => [k, n]));
const SHAPES = { original: null, vertical: 9 / 16, square: 1 };
const TEXT_SIZES = { small: 40, medium: 64, large: 96, huge: 140 };
const TEXT_COLORS = { white: "#ffffff", black: "#000000", yellow: "#ffe600", red: "#ff3030", blue: "#3090ff", green: "#30d060" };

// ================================================================== small model helpers
function blankPiece(start, end) {
  return { start: +start.toFixed(4), end: +end.toFixed(4), off: false, speed: 1, reverse: false, mute: false, volume: 1, fx: [],
    zoom: { mode: "none", amount: 1.3 }, color: { brightness: 0, contrast: 1, saturation: 1 }, follow: { mode: "none", zoom: 1.6, path: [] } };
}
function fixPiece(p) {
  if (!p.follow) p.follow = { mode: "none", zoom: 1.6, path: [] };
  if (!p.zoom) p.zoom = { mode: "none", amount: 1.3 };
  if (!p.color) p.color = { brightness: 0, contrast: 1, saturation: 1 };
  if (!p.fx) p.fx = [];
  return p;
}
const clone = (x) => JSON.parse(JSON.stringify(x));
const outLen = (p) => (p.end - p.start) / p.speed;
/** The pieces on screen: the newest step, or an older step being looked at. */
function cur() { return S.viewStep === null ? S.proj.pieces : S.proj.steps[S.viewStep].pieces; }
function kept() { return cur().filter((p) => !p.off); }
function locked() { return S.viewStep !== null; }
function markHand() { S.proj.rule = Object.assign({ interval: 0, group: 1, picks: [], action: "" }, S.proj.rule || {}, { hand: true }); }
/** The timeline: kept pieces with their place in the finished video. */
function layout() {
  let t = 0;
  return kept().map((p) => { const d = outLen(p); const r = { p, out0: t, out1: t + d }; t += d; return r; });
}
function rawTotal() { return kept().reduce((a, p) => a + outLen(p), 0); }
/** Length of the finished video, as the export will make it (transitions overlap neighbours a little). */
function finalTotal() {
  const L = layout(), st = S.proj.settings;
  let t = L.length ? L[L.length - 1].out1 : 0;
  if (st.transition !== "none") for (let i = 0; i + 1 < L.length; i++) {
    const d = Math.min(st.transition_s, (L[i].out1 - L[i].out0) / 2, (L[i + 1].out1 - L[i + 1].out0) / 2);
    if (d >= 0.08) t -= d;
  }
  return Math.max(0, t);
}
function segAt(t) {
  const L = layout();
  for (let i = 0; i < L.length; i++) if (t < L[i].out1 - 1e-6) return { i, s: L[i], L };
  return L.length ? { i: L.length - 1, s: L[L.length - 1], L, end: true } : null;
}
function selList() { return kept().filter((p) => S.sel.has(p)); }

// ================================================================== saving and undo
function snapshot() { return JSON.stringify({ pieces: S.proj.pieces, settings: S.proj.settings, name: S.proj.name, texts: S.proj.texts, steps: S.proj.steps, rule: S.proj.rule }); }
function remember() { S.undo.push(snapshot()); if (S.undo.length > 80) S.undo.shift(); S.redo = []; }
function restore(json) {
  const d = JSON.parse(json);
  const keepSel = selList().map((p) => S.proj.pieces.indexOf(p));
  Object.assign(S.proj, d); S.proj.pieces.forEach(fixPiece);
  if (S.viewStep !== null && S.viewStep >= S.proj.steps.length) S.viewStep = null;
  S.sel = new Set(keepSel.map((i) => S.proj.pieces[i]).filter((p) => p && !p.off));
  refreshAll(); scheduleSave();
}
function undo() { if (!S.undo.length) return; S.redo.push(snapshot()); restore(S.undo.pop()); }
function redo() { if (!S.redo.length) return; S.undo.push(snapshot()); restore(S.redo.pop()); }
function scheduleSave() { $("saveState").textContent = "Saving…"; clearTimeout(S.saveTimer); S.saveTimer = setTimeout(saveNow, 500); }
function saveNow() {
  clearTimeout(S.saveTimer); S.saveTimer = null;
  const body = { name: S.proj.name, pieces: S.proj.pieces, settings: S.proj.settings, texts: S.proj.texts, steps: S.proj.steps, rule: S.proj.rule };
  S.saving = S.saving.then(() => api("/api/projects/" + S.id, { method: "PUT", json: body }))
    .then(() => { if (!S.saveTimer) $("saveState").textContent = "Saved"; })
    .catch((e) => { $("saveState").textContent = "Not saved"; toast("Not saved: " + errText(e), true); });
  return S.saving;
}
/** Change the project: remember for Undo, apply, redraw, save. */
function change(fn, opts) {
  if (locked() && !(opts && (opts.any || opts.keepVideo))) { lockedNote(); return false; }      // settings, words and music stay editable
  remember(); fn(); refreshAll(opts); scheduleSave();
  return true;
}
function lockedNote() { toast("This is an older step, so it is locked. Click the newest step (in Steps) to change things.", true); }
function refreshAll(opts) {
  S.sel = new Set([...S.sel].filter((p) => !p.off && cur().includes(p)));
  renderTimeline(); renderInspector(); renderLengths(); renderSteps();
  if (!(opts && opts.keepVideo)) showFrame();
}

// ================================================================== lengths shown everywhere
function renderLengths() {
  const total = finalTotal(), sel = selList();
  $("iLen").textContent = fmtTime(total);
  $("tTotal").textContent = fmtTime(total);
  $("topLen").textContent = fmtTime(total);
  $("iCount").textContent = String(kept().length);
  const sl = sel.reduce((a, p) => a + outLen(p), 0);
  $("iSel").textContent = sel.length ? sel.length + " selected · " + fmtTime(sl) : "";
  $("bUndo").disabled = !S.undo.length; $("bRedo").disabled = !S.redo.length;
  $("bDel").disabled = $("bDup").disabled = !sel.length;
}

// ================================================================== live viewer (plays the edit without making a video)
const V = { a: $("vA"), b: $("vB"), seg: null, raf: 0, src: "" };
function active() { return V.a.classList.contains("back") ? V.b : V.a; }
function other() { return active() === V.a ? V.b : V.a; }
function setSource(url) {
  if (V.src === url) return;
  V.src = url;
  for (const v of [V.a, V.b]) { v.src = url; v.load(); }
}
// If this browser cannot play the light copy, fall back to the original file when that plays here.
function onVideoError() {
  if (!S.proj || !V.src.includes("preview=1")) return;
  if (S.info.plays_in_browser) { V.src = ""; setSource(mediaUrl(S.proj.source, false)); V.a.addEventListener("loadeddata", () => showFrame(), { once: true }); }
  else banner("This browser cannot play this video here. The export still works. Try Microsoft Edge or Chrome for the live view.", 0);
}
V.a.addEventListener("error", onVideoError); V.b.addEventListener("error", onVideoError);
function frameAspect() {
  const sh = S.proj.settings.shape || "original";
  const base = sh.split("_")[0];
  return SHAPES[base] || (S.info.width && S.info.height ? S.info.width / S.info.height : 16 / 9);
}
function sizeFrame() {
  const st = $("stage"), f = $("frame");
  const W = st.clientWidth - 36, H = st.clientHeight - 20, a = frameAspect();
  let w = W, h = W / a;
  if (h > H) { h = H; w = H * a; }
  f.style.width = Math.max(40, Math.floor(w)) + "px"; f.style.height = Math.max(40, Math.floor(h)) + "px";
  const sh = S.proj.settings.shape || "original";
  const fit = sh === "original" || sh.endsWith("_blur") ? "contain" : "cover";
  for (const v of [V.a, V.b]) v.style.objectFit = fit;
  $("shapeTag").textContent = { original: "Original shape", vertical: "Tall 9:16", vertical_follow: "Tall 9:16 · follows faces", vertical_blur: "Tall 9:16 · blurred edges", square: "Square", square_follow: "Square · follows faces", square_blur: "Square · blurred edges" }[sh] || "";
  renderOverlay();
}
/** Show a piece's look on a video element with CSS (the export draws it properly). */
function dress(v, p) {
  if (!p) { v.style.transform = ""; v.style.filter = ""; return; }
  const sx = p.fx.includes("hflip") ? -1 : 1, sy = p.fx.includes("vflip") ? -1 : 1;
  let z = 1;
  if (p.follow.mode !== "none") z = p.follow.zoom; else if (p.zoom.mode === "hold") z = p.zoom.amount; else if (p.zoom.mode !== "none") z = 1 + (p.zoom.amount - 1) / 2;
  v.style.transform = "scale(" + sx * z + "," + sy * z + ")";
  const f = [];
  if (p.fx.includes("grayscale")) f.push("grayscale(1)");
  if (p.fx.includes("invert")) f.push("invert(1)");
  if (p.fx.includes("sepia")) f.push("sepia(.9)");
  if (p.fx.includes("blur")) f.push("blur(4px)");
  if (p.fx.includes("sharpen")) f.push("contrast(1.08)");
  if (p.color.brightness) f.push("brightness(" + (1 + p.color.brightness) + ")");
  if (p.color.contrast !== 1) f.push("contrast(" + p.color.contrast + ")");
  if (p.color.saturation !== 1) f.push("saturate(" + p.color.saturation + ")");
  v.style.filter = f.join(" ");
  v.muted = S.mutedAll || p.mute || !S.proj.settings.original_sound || Layers.on();     // with the three layers on, they carry the sound
  v.volume = clamp(p.volume, 0, 1);
  v.playbackRate = clamp(p.speed, 0.0625, 16);
}
function badge(p) {
  const b = $("badge"), bits = [];
  if (p && p.reverse) bits.push("Plays backwards in the export");
  if (p && p.fx.includes("mirror")) bits.push("Mirror shows in the export");
  if (p && p.follow.mode === "face") bits.push("Follows the face in the export");
  b.hidden = !bits.length; b.replaceChildren(icon("info", "s"), el("span", { text: bits.join(" · ") }));
}
/** Put the paused picture at the playhead. */
function showFrame() {
  if (!S.proj) return;
  const at = segAt(S.t);
  if (!at) { V.a.removeAttribute("src"); return; }
  const { s } = at, v = active();
  V.seg = s;
  dress(v, s.p); badge(s.p);
  const want = s.p.start + Math.max(0, S.t - s.out0) * s.p.speed;
  if (Math.abs(v.currentTime - want) > 0.03) v.currentTime = clamp(want, 0, S.info.duration - 0.01);
  renderPlayhead(); renderOverlay();
}
function prepNext(after) {
  const L = layout(), i = L.indexOf(after);
  const nx = L[i + 1], o = other();
  if (!nx) return;
  dress(o, nx.p);
  o.dataset.forStart = String(nx.p.start);
  if (Math.abs(o.currentTime - nx.p.start) > 0.05) o.currentTime = nx.p.start;
}
function play() {
  if (!S.proj || !kept().length) return;
  if (S.t >= rawTotal() - 0.02) S.t = 0;
  S.playing = true; setPlayIcon();
  const at = segAt(S.t); V.seg = at.s;
  const v = active(); dress(v, at.s.p); badge(at.s.p);
  v.currentTime = at.s.p.start + (S.t - at.s.out0) * at.s.p.speed;
  const pr = v.play(); if (pr && pr.catch) pr.catch(() => {});
  prepNext(at.s); musicPlay(); Layers.play();
  cancelAnimationFrame(V.raf); V.raf = requestAnimationFrame(tick);
}
function pause() {
  S.playing = false; setPlayIcon();
  V.a.pause(); V.b.pause(); $("music").pause(); Layers.pause();
  cancelAnimationFrame(V.raf);
}
function togglePlay() { if (S.playing) pause(); else play(); }
function setPlayIcon() { $("tPlay").replaceChildren(icon(S.playing ? "pause" : "play")); }
function tick() {
  if (!S.playing) return;
  const v = active(), s = V.seg;
  if (!s || !S.proj.pieces.includes(s.p) || s.p.off) { const at = segAt(S.t); if (!at) { pause(); return; } V.seg = at.s; }
  const cur = V.seg;
  S.t = cur.out0 + Math.max(0, v.currentTime - cur.p.start) / cur.p.speed;
  if (v.currentTime >= cur.p.end - 0.02 || v.ended) {
    const L = layout(), i = L.findIndex((r) => r.p === cur.p), nx = L[i + 1];
    if (!nx) { S.t = L.length ? L[L.length - 1].out1 : 0; pause(); renderPlayhead(); return; }
    V.seg = nx; S.t = nx.out0;
    const o = other();
    if (Math.abs(nx.p.start - cur.p.end) < 0.06 && nx.p.speed === cur.p.speed) { dress(v, nx.p); }              // continuous footage: keep playing
    else if (Math.abs(o.currentTime - nx.p.start) < 0.08 && o.readyState >= 2) {                                // the other player is waiting at the right spot
      v.pause(); o.classList.remove("back"); v.classList.add("back"); dress(o, nx.p);
      const pr = o.play(); if (pr && pr.catch) pr.catch(() => {});
    } else { dress(v, nx.p); v.currentTime = nx.p.start; }
    badge(nx.p); prepNext(nx);
  }
  Layers.follow(active(), V.seg.p);
  renderPlayhead(); renderOverlay();
  if (S.followPlay) scrollToPlayhead();
  V.raf = requestAnimationFrame(tick);
}
function seek(t) {
  S.t = clamp(t, 0, Math.max(0, rawTotal() - 0.001));
  if (S.playing) { pause(); play(); } else showFrame();
  musicSync();
}
// music in the viewer
function musicPlay() {
  const m = $("music"), st = S.proj.settings;
  if (!st.music) { m.pause(); return; }
  const url = mediaUrl(st.music, false);
  if (m.dataset.src !== url) { m.src = url; m.dataset.src = url; }
  m.volume = clamp(st.music_volume, 0, 1); m.muted = S.mutedAll;
  musicSync();
  const pr = m.play(); if (pr && pr.catch) pr.catch(() => {});
}
function musicSync() { const m = $("music"); if (m.duration) m.currentTime = S.t % m.duration; }
// words on the picture, drawn over the viewer
function renderOverlay() {
  const box = $("over"); box.replaceChildren();
  const H = $("frame").clientHeight;
  for (const t of S.proj.texts || []) {
    const end = t.end > 0 ? t.end : 1e9;
    if (S.t < t.start || S.t >= end) continue;
    const d = el("div", { class: "wtext", text: t.text });
    d.style.fontSize = Math.max(8, TEXT_SIZES[t.size] * H / 1080) + "px";
    d.style.color = TEXT_COLORS[t.color] || "#fff";
    if (t.box) d.style.background = "rgba(0,0,0,.5)"; else d.style.textShadow = "0 0 3px #000, 0 0 2px #000";
    const pos = t.position || "bottom";
    if (pos.startsWith("top")) d.style.top = "5%"; else if (pos === "center") { d.style.top = "50%"; d.style.transform = "translate(-50%,-50%)"; } else d.style.bottom = "6%";
    if (pos.endsWith("left")) { d.style.left = "4%"; d.style.transform = "none"; }
    if (pos.endsWith("right")) { d.style.left = "auto"; d.style.right = "4%"; d.style.transform = "none"; }
    box.append(d);
  }
}

// ================================================================== timeline
const T = { thumbW: 100, h: 64, drag: null };
function px(t) { return Math.round(t * S.pps) + 16; }
function timeAt(x) { return (x - 16) / S.pps; }
function renderPlayhead() {
  $("playhead").style.left = px(S.t) + "px";
  $("tNow").textContent = fmtTime(S.t);
}
function scrollToPlayhead() {
  const sc = $("tlScroll"), x = px(S.t);
  if (x < sc.scrollLeft + 40 || x > sc.scrollLeft + sc.clientWidth - 40) sc.scrollLeft = x - sc.clientWidth * 0.3;
}
function setZoomFromSlider() {
  // slider 0..100 maps to 2 .. 400 pixels per second on a log scale
  const v = parseFloat($("zoom").value);
  S.pps = 2 * Math.pow(200, v / 100);
}
function sliderFromZoom() { $("zoom").value = String(clamp(Math.log(S.pps / 2) / Math.log(200) * 100, 0, 100)); paintRange($("zoom")); }
function zoomTo(pps, anchorT) {
  const sc = $("tlScroll"), t = anchorT === undefined ? S.t : anchorT, before = px(t) - sc.scrollLeft;
  S.pps = clamp(pps, 2, 400); sliderFromZoom(); renderTimeline();
  sc.scrollLeft = px(t) - before;
}
function fitZoom() { const w = $("tlScroll").clientWidth - 48; S.pps = clamp(w / Math.max(1, rawTotal()), 2, 400); sliderFromZoom(); renderTimeline(); $("tlScroll").scrollLeft = 0; }
function renderRuler(total) {
  const r = $("ruler"); r.replaceChildren();
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const major = steps.find((s) => s * S.pps >= 90) || 3600;
  const minor = steps.slice().reverse().find((s) => s < major && s * S.pps >= 12 && (major / s) % 1 === 0) || major;
  const sc = $("tlScroll"), a = Math.max(0, timeAt(sc.scrollLeft) - major), b = Math.min(total + major * 2, timeAt(sc.scrollLeft + sc.clientWidth) + major);
  for (let t = Math.floor(a / minor) * minor; t <= b; t += minor) {
    const isMaj = Math.abs(t / major - Math.round(t / major)) < 1e-6;
    const tick = el("i", { class: isMaj ? "maj" : "" }); tick.style.left = px(t) + "px"; r.append(tick);
    if (isMaj) { const lab = el("span", { text: fmtTime(t, major < 1 ? 1 : 0) }); lab.style.left = px(t) + "px"; r.append(lab); }
  }
}
function tagsOf(p) {
  const t = [];
  if (p.speed !== 1) t.push(p.speed + "×");
  if (p.reverse) t.push("backwards");
  for (const f of p.fx) t.push(LOOK_NAME[f] || f);
  if (p.follow.mode !== "none") t.push("follow");
  else if (p.zoom.mode !== "none") t.push("zoom");
  if (p.mute) t.push("muted");
  return t.join(" · ");
}
function renderTimeline() {
  const L = layout(), total = L.length ? L[L.length - 1].out1 : 0;
  const inner = $("tlInner"), sc = $("tlScroll");
  inner.style.width = Math.max(sc.clientWidth, px(total) + 400) + "px";
  renderRuler(total);
  const lane = $("laneV"); lane.replaceChildren();
  const left = sc.scrollLeft - 300, right = sc.scrollLeft + sc.clientWidth + 300;
  const ar = S.info.width && S.info.height ? S.info.width / S.info.height : 16 / 9;
  T.thumbW = Math.round(T.h * ar);
  for (const r of L) {
    const x0 = px(r.out0), x1 = px(r.out1);
    if (x1 < left || x0 > right) continue;                    // only what is on screen is drawn, so 5,000 pieces stay quick
    const w = Math.max(4, x1 - x0 - 2);
    const p = r.p;
    const b = el("div", { class: "clipb" + (S.sel.has(p) ? " sel" : "") + (w < 70 ? " narrow" : "") + (locked() ? " locked" : ""), "data-k": String(cur().indexOf(p)) });
    b.style.left = x0 + "px"; b.style.width = w + "px";
    if (S.info.has_video) {
      const strip = el("div", { class: "strip" });
      const n = Math.max(1, Math.min(12, Math.ceil(w / T.thumbW)));
      for (let k = 0; k < n; k++) {
        const ot = (k * T.thumbW + T.thumbW / 2) / S.pps;
        const st = clamp(p.reverse ? p.end - ot * p.speed : p.start + ot * p.speed, p.start, p.end - 0.04);
        const img = el("img", { alt: "", src: thumbUrl(S.proj.source, Math.min(Math.round(st * 2) / 2, Math.floor((S.info.duration - 0.05) * 2) / 2)), draggable: "false" });
        img.style.width = T.thumbW + "px";
        if (p.fx.includes("hflip")) img.style.transform = "scaleX(-1)";
        if (p.fx.includes("grayscale")) img.style.filter = "grayscale(1)";
        strip.append(img);
      }
      b.append(strip);
    }
    b.append(el("div", { class: "meta" }, el("span", { class: "num", text: fmtTime(r.out1 - r.out0) }), el("span", { class: "tags", text: tagsOf(p) })));
    b.append(el("div", { class: "h l", "data-edge": "l", title: "Drag to change where this piece starts" }), el("div", { class: "h r", "data-edge": "r", title: "Drag to make this piece longer or shorter" }));
    lane.append(b);
  }
  // words lane
  const lt = $("laneT"); lt.replaceChildren(); lt.hidden = !(S.proj.texts || []).length;
  (S.proj.texts || []).forEach((t, i) => {
    const end = t.end > 0 ? t.end : total;
    const d = el("div", { class: "lane-item text", title: t.text, onclick: () => { openPanel("text"); } }, icon("type", "s"), el("span", { text: t.text }));
    d.style.left = px(t.start) + "px"; d.style.width = Math.max(20, px(end) - px(t.start) - 2) + "px"; lt.append(d);
  });
  // music lane
  const lm = $("laneM"); lm.replaceChildren(); lm.hidden = !S.proj.settings.music;
  if (S.proj.settings.music) {
    const name = (S.files.find((f) => f.id === S.proj.settings.music) || { name: S.proj.settings.music.split("/").pop() }).name;
    const d = el("div", { class: "lane-item music", onclick: () => openPanel("audio") }, icon("music", "s"), el("span", { text: name }));
    d.style.left = px(0) + "px"; d.style.width = Math.max(40, px(total) - px(0)) + "px"; lm.append(d);
  }
  renderPlayhead();
}

// ---- pointer work on the timeline: select, drag to move, drag edges to trim, marquee, scrub
function pieceFromEvent(e) {
  const b = e.target.closest(".clipb");
  return b ? cur()[+b.dataset.k] : null;
}
function showTip(text, x, y) {
  let t = $("tlTip"); if (!t) { t = el("div", { class: "tip", id: "tlTip" }); document.body.append(t); }
  t.textContent = text; t.style.left = (x + 14) + "px"; t.style.top = (y - 34) + "px"; t.hidden = false;
}
function hideTip() { const t = $("tlTip"); if (t) t.hidden = true; }
function selectClick(p, e) {
  const K = kept();
  if (e.shiftKey && S.anchor && K.includes(S.anchor)) {
    const a = K.indexOf(S.anchor), b = K.indexOf(p);
    if (!(e.ctrlKey || e.metaKey)) S.sel.clear();
    for (let k = Math.min(a, b); k <= Math.max(a, b); k++) S.sel.add(K[k]);
  } else if (e.ctrlKey || e.metaKey) {
    if (S.sel.has(p)) S.sel.delete(p); else S.sel.add(p);
    S.anchor = p;
  } else if (!S.sel.has(p)) { S.sel = new Set([p]); S.anchor = p; }
}
function onLaneDown(e) {
  if (e.button !== 0) return;
  const sc = $("tlScroll"), rect = sc.getBoundingClientRect();
  const xIn = (ev) => ev.clientX - rect.left + sc.scrollLeft;
  const p = pieceFromEvent(e);
  const edge = e.target.dataset && e.target.dataset.edge;
  sc.setPointerCapture(e.pointerId);
  if (p && edge && locked()) { lockedNote(); return; }
  if (p && edge) {                                             // trim an edge: it may grow into any footage no other piece uses
    if (!S.sel.has(p)) { S.sel = new Set([p]); S.anchor = p; }
    T.drag = { kind: "trim", p, edge, x0: xIn(e), start: p.start, end: p.end, snap: snapshot(), moved: false, lim: Steps.extendLimits(S.proj.pieces, p, S.info.duration) };
  } else if (p) {                                              // select, maybe move
    const wasSel = S.sel.has(p) && !(e.ctrlKey || e.metaKey || e.shiftKey) && S.sel.size > 1;
    selectClick(p, e);
    T.drag = { kind: "move", p, x0: xIn(e), moved: locked(), wasSel, frozen: locked() };
    renderTimeline(); renderInspector(); renderLengths();
  } else {                                                     // empty space: marquee or seek
    T.drag = { kind: "box", x0: xIn(e), y0: e.clientY, moved: false, add: e.ctrlKey || e.metaKey || e.shiftKey, base: new Set(S.sel) };
  }
  const move = (ev) => {
    const d = T.drag; if (!d) return;
    const x = xIn(ev), dx = x - d.x0;
    if (d.frozen) return;                                      // older steps: click to look, no moving
    if (!d.moved && Math.abs(dx) < 4 && (d.kind !== "box" || Math.abs(ev.clientY - d.y0) < 4)) return;
    d.moved = true;
    if (d.kind === "trim") {
      const ds = dx / S.pps * d.p.speed;
      let stop = false;
      if (d.edge === "l") { const want = d.start + ds; d.p.start = +clamp(want, d.lim.lo, d.end - MIN_PIECE).toFixed(3); stop = want < d.lim.lo - 0.01; }
      else { const want = d.end + ds; d.p.end = +clamp(want, d.start + MIN_PIECE, d.lim.hi).toFixed(3); stop = want > d.lim.hi + 0.01; }
      renderTimeline(); renderLengths();
      showTip((d.edge === "l" ? "Starts at " + fmtTime(d.p.start, 2) : "Ends at " + fmtTime(d.p.end, 2)) + " · piece " + fmtTime(outLen(d.p), 2) + " · video " + fmtTime(finalTotal()) + (stop ? " · stops at the next piece" : ""), ev.clientX, ev.clientY);
    } else if (d.kind === "move") {
      const L = layout().filter((r) => !S.sel.has(r.p));
      let gap = L.length;
      for (let k = 0; k < L.length; k++) if (x < (px(L[k].out0) + px(L[k].out1)) / 2) { gap = k; break; }
      d.gap = gap;
      let mark = $("dropMark"); if (!mark) { mark = el("div", { class: "drop-mark", id: "dropMark" }); $("laneV").append(mark); }
      // where the moved pieces would land, measured on the timeline without them
      let edgeT = 0; for (let k = 0; k < gap; k++) edgeT += L[k].out1 - L[k].out0;
      const firstOut = layout().find((r) => S.sel.has(r.p));
      mark.style.left = (gap === 0 ? px(0) : (L[gap - 1] ? px(L[gap - 1].out1) : px(edgeT))) + "px";
      for (const b of $("laneV").querySelectorAll(".clipb.sel")) b.classList.add("drag");
      showTip("Move " + S.sel.size + " piece" + (S.sel.size > 1 ? "s" : "") + " here", ev.clientX, ev.clientY);
      void firstOut;
    } else {
      const a = Math.min(d.x0, x), b = Math.max(d.x0, x);
      let m = $("marquee"); if (!m) { m = el("div", { class: "marquee", id: "marquee" }); $("tlInner").append(m); }
      const lr = $("laneV").getBoundingClientRect(), ir = $("tlInner").getBoundingClientRect();
      m.style.left = a + "px"; m.style.width = (b - a) + "px"; m.style.top = (lr.top - ir.top) + "px"; m.style.height = lr.height + "px";
      const t0 = timeAt(a), t1 = timeAt(b);
      S.sel = new Set(d.base);
      for (const r of layout()) if (r.out1 > t0 && r.out0 < t1) S.sel.add(r.p);
      for (const bl of $("laneV").querySelectorAll(".clipb")) bl.classList.toggle("sel", S.sel.has(cur()[+bl.dataset.k]));
      renderLengths();
    }
  };
  const up = (ev) => {
    sc.removeEventListener("pointermove", move); sc.removeEventListener("pointerup", up); sc.removeEventListener("pointercancel", up);
    hideTip(); const mark = $("dropMark"); if (mark) mark.remove(); const m = $("marquee"); if (m) m.remove();
    const d = T.drag; T.drag = null; if (!d) return;
    if (d.kind === "trim") {
      if (d.moved) { S.undo.push(d.snap); S.redo = []; markHand(); refreshAll(); scheduleSave(); }
    } else if (d.kind === "move") {
      if (d.moved && !d.frozen && d.gap !== undefined) moveSelected(d.gap);
      else {
        if (d.wasSel) { S.sel = new Set([d.p]); S.anchor = d.p; }
        const r = layout().find((q) => q.p === d.p);                   // a click on a piece also moves the playhead there
        if (r) { const tt = timeAt(xIn(ev)); seek(clamp(tt, r.out0, r.out1 - 0.01)); }
        refreshAll({ keepVideo: true });
      }
    } else {
      if (!d.moved) { if (!d.add) S.sel.clear(); seek(timeAt(d.x0)); }
      refreshAll({ keepVideo: true });
    }
  };
  sc.addEventListener("pointermove", move); sc.addEventListener("pointerup", up); sc.addEventListener("pointercancel", up);
}
function moveSelected(gap) {
  change(() => {
    const K = kept(), moving = K.filter((p) => S.sel.has(p)), rest = K.filter((p) => !S.sel.has(p));
    const order = rest.slice(0, gap).concat(moving, rest.slice(gap));
    S.proj.pieces = order.concat(S.proj.pieces.filter((p) => p.off)); markHand();
  });
}
function onRulerDown(e) {
  const sc = $("tlScroll"), rect = sc.getBoundingClientRect();
  const go = (ev) => seek(timeAt(ev.clientX - rect.left + sc.scrollLeft));
  e.stopPropagation(); sc.setPointerCapture(e.pointerId); go(e);
  const move = (ev) => go(ev);
  const up = () => { sc.removeEventListener("pointermove", move); sc.removeEventListener("pointerup", up); };
  sc.addEventListener("pointermove", move); sc.addEventListener("pointerup", up);
}

// ---- timeline actions
function removeSelected() {
  const sel = selList(); if (!sel.length) return;
  if (change(() => { for (const p of sel) p.off = true; S.sel.clear(); markHand(); }))
    toast(sel.length + " piece" + (sel.length > 1 ? "s" : "") + " removed · video now " + fmtTime(finalTotal()));
}
function duplicateSelected() {
  const sel = selList(); if (!sel.length) return;
  change(() => {
    const out = [];
    const copies = new Set();
    for (const p of S.proj.pieces) { out.push(p); if (S.sel.has(p)) { const c = clone(p); out.push(c); copies.add(c); } }
    S.proj.pieces = out; S.sel = copies; markHand();
  });
}
function splitAtPlayhead() {
  if (locked()) { lockedNote(); return; }
  const at = segAt(S.t); if (!at || at.end) return;
  const { s } = at, p = s.p;
  const cut = p.reverse ? p.end - (S.t - s.out0) * p.speed : p.start + (S.t - s.out0) * p.speed;
  if (cut - p.start < MIN_PIECE || p.end - cut < MIN_PIECE) { toast("Too close to the edge of a piece to split here."); return; }
  change(() => {
    const a = clone(p), b = clone(p);
    if (p.reverse) { a.start = +cut.toFixed(4); b.end = +cut.toFixed(4); } else { a.end = +cut.toFixed(4); b.start = +cut.toFixed(4); }
    const i = S.proj.pieces.indexOf(p);
    S.proj.pieces.splice(i, 1, a, b); S.sel = new Set([b]); S.anchor = b; markHand();
  });
}
function restorePiece(p) {
  change(() => {
    p.off = false;
    // put it back where it belongs in time, among the pieces that are in the timeline
    const arr = S.proj.pieces.filter((q) => q !== p);
    const K = arr.filter((q) => !q.off);
    const after = K.find((q) => q.start >= p.start);
    arr.splice(after ? arr.indexOf(after) : K.length ? arr.indexOf(K[K.length - 1]) + 1 : 0, 0, p);
    S.proj.pieces = arr; S.sel = new Set([p]); markHand();
  });
}

// ================================================================== left panels
function openPanel(name) {
  S.panel = name;
  for (const b of document.querySelectorAll(".rail button")) b.classList.toggle("on", b.dataset.panel === name);
  for (const s of document.querySelectorAll("#left > section")) s.hidden = s.dataset.for !== name;
  if (name === "pattern") renderPattern();
  if (name === "text") renderTexts();
  if (name === "sound") Layers.open();
}
// --- Cut
/** Step 1: the whole parent video cut into pieces of `sec` seconds. */
function parentPieces(sec) {
  const d = S.info.duration;
  const n = Math.ceil(d / sec - 1e-9);
  if (n > 5000) throw new Error("That makes " + n + " pieces; the most is 5,000. Choose longer pieces.");
  const edges = []; for (let k = 0; k < n; k++) edges.push(k * sec); edges.push(d);
  if (edges.length > 2 && edges[edges.length - 1] - edges[edges.length - 2] < 0.08) edges.splice(edges.length - 2, 1);
  const pieces = []; for (let k = 0; k + 1 < edges.length; k++) pieces.push(blankPiece(edges[k], edges[k + 1]));
  return pieces;
}
/** The pieces the newest step starts from: the whole video (step 1) or the pinned step before it, laid end to end. */
function stepBase() { const st = S.proj.steps[S.proj.steps.length - 1]; return st ? st.pieces : null; }
function cutInto(sec) {
  if (!(sec >= MIN_PIECE)) { $("cutNote").textContent = "Write a length of at least 0.1 seconds, for example 2."; return; }
  let pieces;
  try { pieces = stepBase() ? Steps.recut(stepBase(), sec).map(fixPiece) : parentPieces(sec); }
  catch (e) { $("cutNote").textContent = e.message; return; }
  if (!change(() => { S.proj.pieces = pieces; S.proj.rule = { interval: sec, group: 1, picks: [], action: "", hand: false }; S.sel.clear(); S.t = 0; })) return;
  $("cutNote").textContent = Steps.countUnits(pieces) + " pieces of " + sec + " s" + (stepBase() ? ", cut from step " + S.proj.steps.length + "." : ".") + " Undo brings back the old cut.";
  fitZoom();
}
// --- Pattern
const P = { group: 3, picks: new Set([1]) };
function renderPattern() {
  const g = $("patGroup");
  if (!g.firstChild) {
    const box = el("div", { class: "chips" });
    for (const n of [2, 3, 4, 5, 6]) box.append(el("button", { class: "chip", type: "button", "data-n": String(n), text: String(n), onclick: () => { P.group = n; P.picks = new Set([...P.picks].filter((k) => k <= n)); if (!P.picks.size) P.picks.add(1); renderPattern(); } }));
    const custom = el("input", { class: "input short", id: "patCustom", inputmode: "numeric", "aria-label": "Other group size", placeholder: "other" });
    custom.addEventListener("change", () => { const n = Math.round(parseFloat(custom.value)); if (n >= 2 && n <= 50) { P.group = n; P.picks = new Set([...P.picks].filter((k) => k <= n)); if (!P.picks.size) P.picks.add(1); renderPattern(); } });
    box.append(custom); g.append(box);
  }
  for (const c of g.querySelectorAll(".chip")) c.classList.toggle("on", +c.dataset.n === P.group);
  const picks = $("patPicks"); picks.replaceChildren();
  for (let k = 1; k <= P.group; k++) picks.append(el("button", { class: "chip" + (P.picks.has(k) ? " on" : ""), type: "button", text: String(k), "aria-pressed": P.picks.has(k) ? "true" : "false",
    onclick: () => { if (P.picks.has(k)) P.picks.delete(k); else P.picks.add(k); renderPattern(); } }));
  const prev = $("patPreview"); prev.replaceChildren();
  for (let k = 0; k < Math.min(18, P.group * 3); k++) { const on = P.picks.has((k % P.group) + 1); prev.append(el("i", { class: (on ? "on" : "") + (k % P.group === 0 && k ? " gap" : "") })); }
  const K = kept(), hits = patternHits(), hs = new Set(hits);
  const left = K.filter((p) => !hs.has(p));
  const L = (arr) => fmtTime(arr.reduce((a, p) => a + outLen(p), 0));
  $("patNote").textContent = P.picks.size ? Steps.countUnits(hits) + " of " + Steps.countUnits(K) + " pieces match. Removing them leaves " + L(left) + "; keeping only them leaves " + L(hits) + "." : "Tap at least one number.";
  renderRecipes();
}
function patternHits() { return Steps.patternHits(kept(), P.group, [...P.picks]); }
function setRule(action) {
  S.proj.rule = Object.assign({ interval: 0, hand: false }, S.proj.rule || {}, { group: P.group, picks: [...P.picks].sort((a, b) => a - b), action });
}
// --- Steps: stack patterns on the same parent video, nothing rendered in between
function renderSteps() {
  if (!S.proj) return;
  const steps = S.proj.steps, n = steps.length, viewing = S.viewStep === null ? n : S.viewStep;
  for (const box of document.querySelectorAll("[data-steps]")) {
    box.replaceChildren();
    // summary first: which step, its length, and the main action; the list is folded away
    const head = el("div", { class: "steps-head" },
      el("span", { class: "n", text: String(viewing + 1) }),
      el("div", { class: "t" }, el("b", { text: "Step " + (viewing + 1) + (locked() ? " (locked)" : n ? " of " + (n + 1) : "") }),
        el("small", { text: Steps.ruleText(S.viewStep === null ? S.proj.rule : steps[S.viewStep].rule) })),
      el("b", { class: "num len", text: fmtTime(Steps.stepLength(cur())) }),
      locked() ? btn("Edit last", { icon: "edit", cls: "sm", onclick: () => showStep(null) })
        : btn("Pin", { icon: "pin", cls: "sm primary", title: "Pin and add step: lock this result (with your hand edits) and start the next step from it. Nothing is rendered.", onclick: pinStep }));
    box.append(head);
    if (!n) continue;
    const list = el("div", { class: "steps-list" });
    const row = (i, rule, pieces, isNew) => list.append(el("button", { type: "button", class: "step" + ((isNew ? S.viewStep === null : S.viewStep === i) ? " on" : ""),
      title: isNew ? "The step you are editing" : "Look at this step (locked)", onclick: () => showStep(isNew ? null : i) },
      el("span", { class: "n", text: String(i + 1) }), el("span", { class: "t", text: Steps.ruleText(rule) }), el("b", { class: "num", text: fmtTime(Steps.stepLength(pieces)) }), icon(isNew ? "edit" : "lock", "s")));
    steps.forEach((st, i) => row(i, st.rule, st.pieces, false));
    row(n, S.proj.rule, S.proj.pieces, true);
    const det = el("details", { class: "steps-all", open: S.stepsOpen }, el("summary", {}, el("span", { text: "All steps (" + (n + 1) + ")" }), el("span", { class: "note", text: "same original video" }), icon("chev", "chev")),
      list, el("div", { class: "btns" },
        btn("Undo step", { icon: "undo", cls: "ghost sm", title: "Throw away the newest step and edit the one before again", onclick: undoStep }),
        btn("Save steps", { icon: "bookmark", cls: "ghost sm", title: "Keep these steps to use on another video", onclick: saveSteps })));
    det.addEventListener("toggle", () => { S.stepsOpen = det.open; });
    box.append(det);
  }
}
function showStep(i) {
  if (S.viewStep === i) return;
  pause(); S.viewStep = i; S.sel.clear(); S.t = 0;
  refreshAll(); fitZoom(); if (S.panel === "pattern") renderPattern();
}
function pinStep() {
  if (locked()) { showStep(null); return; }
  if (!kept().length) { toast("The timeline is empty: put a piece back first.", true); return; }
  const n = S.proj.steps.length + 1;
  change(() => {
    S.proj.steps.push({ pieces: clone(S.proj.pieces), rule: S.proj.rule || null, length: +Steps.stepLength(S.proj.pieces).toFixed(3) });
    S.proj.pieces = clone(kept()).map((p) => { delete p.unit; return fixPiece(p); });
    S.proj.rule = null; S.sel.clear(); S.t = 0;
  });
  fitZoom(); openPanel("cut");
  $("cutNote").textContent = "Step " + n + " is pinned. Step " + (n + 1) + " starts from it: choose a piece length and press Cut, then use Pattern.";
  toast("Step " + n + " pinned · step " + (n + 1) + " starts at " + fmtTime(finalTotal()));
}
async function undoStep() {
  if (!S.proj.steps.length) return;
  if (S.viewStep !== null) S.viewStep = null;
  if (!(await askYes("Throw away step " + (S.proj.steps.length + 1) + " and edit step " + S.proj.steps.length + " again? (Ctrl+Z brings it back.)", "Undo step"))) return;
  change(() => { const st = S.proj.steps.pop(); S.proj.pieces = st.pieces.map(fixPiece); S.proj.rule = st.rule; S.sel.clear(); S.t = 0; });
  fitZoom();
}
async function saveSteps() {
  const rules = S.proj.steps.map((st) => st.rule).concat([S.proj.rule]).filter((r) => r && (r.interval || r.action));
  if (!rules.length) { toast("These steps have only hand edits, which cannot be repeated on another video.", true); return; }
  const name = await askText("Name for these steps", "", "for example: 2 s, remove 1 of 3, then 1 s keep 1 of 2");
  if (!name) return;
  S.recipes.push({ name, type: "steps", steps: rules });
  try { await storeRecipes(); toast("Saved '" + name + "'. Hand edits are not part of it."); } catch (e) { S.recipes.pop(); toast(errText(e), true); }
}
/** Repeat saved steps on this video, from the whole video: cut, pattern, pin, cut, pattern ... */
async function useSteps(r) {
  if (locked()) { lockedNote(); return; }
  if (!(await askYes("Start this video's steps over with '" + r.name + "'? (Ctrl+Z brings back what you have now.)", "Use"))) return;
  let pieces, steps = [], last = null;
  try {
    r.steps.forEach((rule, i) => {
      if (i === 0) pieces = parentPieces(rule.interval || S.info.duration + 1);
      else {
        steps.push({ pieces, rule: last, length: +Steps.stepLength(pieces).toFixed(3) });
        const base = clone(pieces.filter((p) => !p.off)).map((p) => { delete p.unit; return p; });
        pieces = rule.interval ? Steps.recut(base, rule.interval) : base;
      }
      if (rule.action) {
        const hits = new Set(Steps.patternHits(pieces.filter((p) => !p.off), rule.group, rule.picks));
        for (const p of pieces.filter((q) => !q.off)) if (rule.action === "remove" ? hits.has(p) : !hits.has(p)) p.off = true;
      }
      last = Object.assign({}, rule, { hand: false });
    });
  } catch (e) { toast(e.message, true); return; }
  change(() => { S.proj.steps = steps; S.proj.pieces = pieces.map(fixPiece); S.proj.rule = last; S.sel.clear(); S.t = 0; });
  fitZoom(); toast("Used '" + r.name + "' · video now " + fmtTime(finalTotal()));
}

// --- saved patterns
function renderRecipes() {
  const box = $("recipeList"); box.replaceChildren();
  if (!S.recipes.length) box.append(el("p", { class: "hint", text: "None saved yet." }));
  S.recipes.forEach((r, i) => box.append(el("div", { class: "item" },
    el("div", { class: "t" }, el("b", { text: r.name }), el("small", { text: r.type === "steps" ? r.steps.length + " steps: " + r.steps.map(Steps.ruleText).join(" → ") : "Pieces " + r.picks.join(", ") + " of every " + r.group + (r.look ? " · with effects" : "") })),
    el("div", { class: "acts" }, btn("Use", { onclick: () => useRecipe(r) }), btn("", { icon: "trash", cls: "ghost", title: "Delete", onclick: async () => {
      if (!(await askYes("Delete the saved pattern '" + r.name + "'?", "Delete", true))) return;
      S.recipes.splice(i, 1); await storeRecipes();
    } })))));
}
async function storeRecipes() { S.recipes = (await api("/api/recipes", { method: "PUT", json: S.recipes })).recipes; renderRecipes(); }
function useRecipe(r) {
  if (r.type === "steps") { useSteps(r); return; }
  if (locked()) { lockedNote(); return; }
  P.group = r.group; P.picks = new Set(r.picks); renderPattern();
  const hits = patternHits();
  if (r.look) change(() => { for (const p of hits) Object.assign(p, clone(r.look), { start: p.start, end: p.end, off: p.off }); S.sel = new Set(hits); });
  else { S.sel = new Set(hits); refreshAll({ keepVideo: true }); }
  toast("Used '" + r.name + "' on " + hits.length + " pieces");
}

// --- Audio
function fillFileSelects() {
  const m = $("sMusic"), cur = S.proj.settings.music;
  m.replaceChildren(el("option", { value: "", text: "No music" }));
  for (const f of S.files.filter((x) => x.audio)) m.append(el("option", { value: f.id, text: f.name }));
  if (cur && ![...m.options].some((o) => o.value === cur)) m.append(el("option", { value: cur, text: cur.split("/").pop() + " (missing)" }));
  m.value = cur;
  const c = $("sCap"), cc = S.proj.settings.captions;
  c.replaceChildren(el("option", { value: "off", text: "Off" }), el("option", { value: "auto", text: "Automatic (listen to the speech)" }));
  for (const f of S.files.filter((x) => x.subtitles)) c.append(el("option", { value: f.id, text: "From " + f.name }));
  if (![...c.options].some((o) => o.value === cc)) c.append(el("option", { value: cc, text: cc.split("/").pop() + " (missing)" }));
  c.value = cc;
}
const BINDS = [["sOrig", "original_sound", "check"], ["sDuck", "duck", "check"], ["sLoud", "even_loudness", "check"], ["sMusicVol", "music_volume", "num"],
  ["sMusic", "music", "str"], ["sTrans", "transition", "str"], ["sTransS", "transition_s", "num"], ["sFadeIn", "fade_in", "num"], ["sFadeOut", "fade_out", "num"],
  ["sCap", "captions", "str"], ["sCapStyle", "caption_style", "str"], ["sCapPos", "caption_position", "str"], ["sCapSize", "caption_size", "str"],
  ["sNoise", "noise", "str"], ["sVocals", "vocals", "str"], ["sBoost", "voice_boost", "check"], ["sComp", "compress", "check"], ["sBass", "bass", "num"], ["sTreble", "treble", "num"]];
function soundLabels() {
  const db = (v) => (v > 0 ? "+" : "") + Math.round(v) + " dB";
  $("sBassV").textContent = db(+$("sBass").value); $("sTrebleV").textContent = db(+$("sTreble").value);
}
function fillSettings() {
  const st = S.proj.settings;
  for (const [id, key, kind] of BINDS) { const n = $(id); if (kind === "check") n.checked = !!st[key]; else n.value = String(st[key] === undefined ? 0 : st[key]); if (n.classList.contains("rng")) paintRange(n); }
  soundLabels();
  renderShape();
}
function bindSettings() {
  for (const [id, key, kind] of BINDS) {
    const n = $(id);
    n.addEventListener("change", () => {
      const v = kind === "check" ? n.checked : kind === "num" ? parseFloat(n.value) : n.value;
      soundLabels();
      change(() => { S.proj.settings[key] = v; }, { keepVideo: true });
      if (key === "music" || key === "music_volume") { if (S.playing) musicPlay(); }
      if (key === "original_sound") showFrame();
    });
  }
}
async function addFileThen(kind, progId, done) {
  const bar = $(progId);
  try {
    const data = await chooseFile(kind, (k) => { bar.hidden = false; bar.firstChild.style.width = Math.round(k * 100) + "%"; });
    if (!data) return;
    await loadFiles(); done(data);
  } catch (e) { toast(errText(e), true); }
  finally { bar.hidden = true; }
}
async function loadFiles() { S.files = (await api("/api/files")).files; fillFileSelects(); }
async function beatCut() {
  if (locked()) { lockedNote(); return; }
  if (S.proj.steps.length) { $("beatNote").textContent = "Cutting on the beat works in step 1 (on the whole video). Undo the steps first, or cut by time here."; return; }
  const every = Math.round(parseFloat($("beatEvery").value)), music = S.proj.settings.music;
  if (!music) { $("beatNote").textContent = "Choose the music first, above."; return; }
  if (!(every >= 1)) { $("beatNote").textContent = "Write a whole number of beats, for example 4."; return; }
  $("beatNote").textContent = "Listening for the beat…";
  let b;
  try { b = await api("/api/beats", { method: "POST", json: { input: music } }); } catch (e) { $("beatNote").textContent = errText(e); return; }
  if (!b.beats.length) { $("beatNote").textContent = "No clear beat was found in this music."; return; }
  const d = S.info.duration, edges = [0];
  for (let lap = 0; lap * b.duration < d && lap < 2000; lap++) for (let i = every; i < b.beats.length; i += every) {
    const t = lap * b.duration + b.beats[i];
    if (t - edges[edges.length - 1] >= 0.08 && d - t >= 0.08) edges.push(t);
  }
  edges.push(d);
  const pieces = []; for (let k = 0; k + 1 < edges.length; k++) pieces.push(blankPiece(edges[k], edges[k + 1]));
  change(() => { S.proj.pieces = pieces; S.proj.rule = null; S.sel.clear(); S.t = 0; });
  fitZoom();
  $("beatNote").textContent = pieces.length + " pieces, one every " + every + " beats (about " + Math.round(b.tempo) + " beats a minute)." + (S.proj.settings.transition !== "none" ? " Use a straight cut between pieces to stay on the beat." : "");
}

// --- Format
const SHAPE_CARDS = [["original", "Original", null], ["vertical", "Tall 9:16", 9 / 16], ["square", "Square", 1]];
function renderShape() {
  const sh = S.proj.settings.shape || "original", base = sh.split("_")[0], fill = sh.includes("_") ? sh.split("_")[1] : "middle";
  const box = $("shapeCards"); box.replaceChildren();
  for (const [k, name, a] of SHAPE_CARDS) {
    const ar = a || (S.info.width / S.info.height) || 16 / 9;
    const pic = el("i", { class: "shape-pic" }); pic.style.aspectRatio = String(ar);
    box.append(el("button", { type: "button", class: "mode" + (base === k ? " on" : ""), onclick: () => setShape(k, fill) }, el("span", { class: "pic-box" }, pic), el("b", { text: name })));
  }
  const fb = $("shapeFill"); fb.replaceChildren();
  if (base !== "original") fb.append(segmented([["middle", "Crop"], ["blur", "Whole + blur"], ["follow", "Follow face"]], fill, (v) => setShape(base, v)));
  else fb.append(el("p", { class: "hint", text: "The original keeps the whole picture." }));
}
function setShape(base, fill) {
  const v = base === "original" ? "original" : fill === "middle" ? base : base + "_" + fill;
  change(() => { S.proj.settings.shape = v; }, { keepVideo: true });
  renderShape(); sizeFrame();
}

// --- Text
function renderTexts() {
  const box = $("texts"); box.replaceChildren();
  if (!S.proj.texts.length) box.append(el("p", { class: "hint", text: "No words yet. Add a title or a line of text." }));
  S.proj.texts.forEach((t, i) => {
    const words = el("input", { class: "input", value: t.text, "aria-label": "Words" });
    words.addEventListener("change", () => { const v = words.value.trim(); if (v) change(() => { t.text = v.slice(0, 200); }, { keepVideo: true }); renderOverlay(); });
    const tin = (val, label, set) => { const n = el("input", { class: "input short", value: val, "aria-label": label, inputmode: "decimal" }); n.addEventListener("change", () => { const s = parseTime(n.value); change(() => set(s === null ? 0 : s), { keepVideo: true }); renderTexts(); renderOverlay(); }); return n; };
    const pick = (val, opts, label, set) => { const s = el("select", { "aria-label": label }); for (const [v, n] of opts) s.append(el("option", { value: v, text: n })); s.value = val; s.addEventListener("change", () => { change(() => set(s.value), { keepVideo: true }); renderOverlay(); }); return s; };
    const boxSw = el("input", { type: "checkbox", class: "sw" }); boxSw.checked = t.box;
    boxSw.addEventListener("change", () => { change(() => { t.box = boxSw.checked; }, { keepVideo: true }); renderOverlay(); });
    const card = el("div", { class: "box" },
      el("div", { class: "row" }, words, btn("", { icon: "trash", cls: "ghost", title: "Delete", onclick: () => { change(() => S.proj.texts.splice(i, 1), { keepVideo: true }); renderTexts(); renderOverlay(); } })),
      el("div", { class: "row" }, el("span", { class: "lbl", text: "From" }), tin(fmtTime(t.start), "From", (v) => { t.start = v; }), el("span", { class: "lbl", text: "to" }), tin(t.end > 0 ? fmtTime(t.end) : "", "To (empty = until the end)", (v) => { t.end = v; }), btn("", { icon: "clock", cls: "ghost", title: "Start at the playhead", onclick: () => { change(() => { t.start = +S.t.toFixed(2); }, { keepVideo: true }); renderTexts(); } })),
      el("div", { class: "grid2" },
        pick(t.position, [["top", "Top"], ["center", "Middle"], ["bottom", "Bottom"], ["top_left", "Top left"], ["top_right", "Top right"], ["bottom_left", "Bottom left"], ["bottom_right", "Bottom right"]], "Place", (v) => { t.position = v; }),
        pick(t.size, [["small", "Small"], ["medium", "Medium"], ["large", "Large"], ["huge", "Huge"]], "Size", (v) => { t.size = v; })),
      el("div", { class: "row between" }, pick(t.color, [["white", "White"], ["yellow", "Yellow"], ["black", "Black"], ["red", "Red"], ["blue", "Blue"], ["green", "Green"]], "Colour", (v) => { t.color = v; }), el("label", { class: "swl" }, el("span", { text: "Dark box" }), boxSw)));
    box.append(card);
  });
  enhanceAll(box);
}

// ================================================================== right panel: the selected pieces
function sec(title, ic, open, ...kids) {
  return el("details", { class: "sec", open }, el("summary", {}, icon(ic), el("span", { text: title }), icon("chev", "chev")), el("div", { class: "pad" }, ...kids));
}
function onSel(fn) { change(() => { selList().forEach(fn); markHand(); }, { keepVideo: false }); }
function renderInspector() {
  const R = $("right"), sel = selList();
  const open = new Set([...R.querySelectorAll("details[open] > summary span")].map((s) => s.textContent));
  R.replaceChildren();
  if (!sel.length) { renderOverview(R); return; }
  const len = sel.reduce((a, p) => a + outLen(p), 0);
  R.append(el("div", { class: "side-h" }, el("h2", { text: sel.length === 1 ? "Piece" : sel.length + " pieces" }), el("span", { class: "note num", text: fmtTime(len) + " long" })));
  const all = (f) => sel.every(f);
  // basics first: speed, sound, length
  const speeds = [0.25, 0.5, 1, 1.5, 2, 3, 4];
  const cur = all((p) => p.speed === sel[0].speed) ? sel[0].speed : null;
  const chipsBox = el("div", { class: "chips" });
  for (const v of speeds) chipsBox.append(el("button", { class: "chip" + (cur === v ? " on" : ""), type: "button", text: v + "×", onclick: () => onSel((p) => { p.speed = v; }) }));
  const vol = el("input", { type: "range", class: "rng", min: "0", max: "2", step: "0.05", value: String(sel[0].volume), "aria-label": "Loudness" });
  vol.addEventListener("change", () => onSel((p) => { p.volume = parseFloat(vol.value); p.mute = false; }));
  const muteSw = el("input", { type: "checkbox", class: "sw" }); muteSw.checked = all((p) => p.mute);
  muteSw.addEventListener("change", () => onSel((p) => { p.mute = muteSw.checked; }));
  const basics = el("div", { class: "pad" },
    el("div", { class: "field" }, el("span", { text: "Speed" }), chipsBox),
    el("div", { class: "field" }, el("span", { text: "Loudness" }), vol),
    el("label", { class: "swl" }, el("span", { text: "Mute" }), muteSw));
  if (sel.length === 1) basics.append(trimFields(sel[0]));
  basics.append(el("div", { class: "btns" },
    btn("Split", { icon: "split", onclick: splitAtPlayhead, title: "Split at the playhead (S)" }),
    btn("Duplicate", { icon: "copy", onclick: duplicateSelected }),
    btn("Remove", { icon: "trash", cls: "danger", onclick: removeSelected })));
  R.append(basics);
  // extras, closed until wanted
  const looks = el("div", { class: "looks" });
  for (const [k, name, ic] of LOOKS) {
    const on = all((p) => p.fx.includes(k));
    looks.append(el("button", { type: "button", class: "btn" + (on ? " on" : ""), onclick: () => onSel((p) => { p.fx = p.fx.filter((f) => f !== k); if (!on) p.fx.push(k); }) }, icon(ic, "s"), el("span", { text: name })));
  }
  const back = el("input", { type: "checkbox", class: "sw" }); back.checked = all((p) => p.reverse);
  back.addEventListener("change", () => onSel((p) => { p.reverse = back.checked; }));
  R.append(sec("Effects", "sparkles", open.has("Effects"), looks, el("label", { class: "swl" }, el("span", { text: "Play backwards" }), back)));
  const c0 = sel[0].color;
  const slider = (label, key, min, max, step) => {
    const r = el("input", { type: "range", class: "rng", min: String(min), max: String(max), step: String(step), value: String(c0[key]), "aria-label": label });
    r.addEventListener("change", () => onSel((p) => { p.color = Object.assign({}, p.color, { [key]: parseFloat(r.value) }); }));
    return el("div", { class: "field" }, el("span", { text: label }), r);
  };
  R.append(sec("Colour", "palette", open.has("Colour"), slider("Brightness", "brightness", -0.5, 0.5, 0.05), slider("Contrast", "contrast", 0.5, 2, 0.05), slider("Colour strength", "saturation", 0, 2, 0.05),
    btn("Reset colour", { cls: "ghost", onclick: () => onSel((p) => { p.color = { brightness: 0, contrast: 1, saturation: 1 }; }) })));
  const z = sel[0].zoom;
  const zMode = segmented([["none", "Off"], ["in", "In"], ["out", "Out"], ["hold", "Hold"]], z.mode, (v) => onSel((p) => { p.zoom = { mode: v, amount: p.zoom.amount || 1.3 }; }));
  const zAmt = el("input", { type: "range", class: "rng", min: "1.1", max: "2.5", step: "0.05", value: String(z.amount), "aria-label": "Zoom amount" });
  zAmt.addEventListener("change", () => onSel((p) => { p.zoom = { mode: p.zoom.mode === "none" ? "hold" : p.zoom.mode, amount: parseFloat(zAmt.value) }; }));
  R.append(sec("Zoom", "zoom", open.has("Zoom"), zMode, el("div", { class: "field" }, el("span", { text: "How much" }), zAmt)));
  const f = sel[0].follow;
  const fZoom = el("input", { type: "range", class: "rng", min: "1.2", max: "2.5", step: "0.1", value: String(f.zoom || 1.6), "aria-label": "How close" });
  fZoom.addEventListener("change", () => onSel((p) => { p.follow = Object.assign({}, p.follow, { zoom: parseFloat(fZoom.value) }); }));
  R.append(sec("Follow", "target", open.has("Follow"),
    el("p", { class: "hint", text: "Zoom in and keep a face, or a thing you draw a box around, in the middle. Works with the original shape." }),
    el("div", { class: "btns" },
      btn("Face", { icon: "face", cls: all((p) => p.follow.mode === "face") ? "on" : "", onclick: () => onSel((p) => { p.follow = { mode: "face", zoom: parseFloat(fZoom.value), path: [] }; }) }),
      btn("A thing", { icon: "target", cls: all((p) => p.follow.mode === "object") ? "on" : "", onclick: startDrawBox }),
      btn("Off", { cls: "ghost", onclick: () => onSel((p) => { p.follow = { mode: "none", zoom: p.follow.zoom, path: [] }; }) })),
    el("div", { class: "field" }, el("span", { text: "How close" }), fZoom)));
  enhanceAll(R);
  for (const r of R.querySelectorAll("input.rng")) paintRange(r);
}
function trimFields(p) {
  const tin = (label, val, set) => {
    const n = el("input", { class: "input", value: fmtTime(val, 2), "aria-label": label, inputmode: "decimal" });
    n.addEventListener("change", () => { const v = parseTime(n.value); if (v === null) { n.value = fmtTime(val, 2); return; } change(() => set(v)); });
    return el("label", { class: "field" }, el("span", { text: label }), n);
  };
  const lim = () => Steps.extendLimits(S.proj.pieces, p, S.info.duration);
  return el("div", { class: "grid2" },
    tin("Starts at", p.start, (v) => { p.start = +clamp(v, lim().lo, p.end - MIN_PIECE).toFixed(3); markHand(); }),
    tin("Ends at", p.end, (v) => { p.end = +clamp(v, p.start + MIN_PIECE, lim().hi).toFixed(3); markHand(); }));
}
function renderOverview(R) {
  const K = kept(), off = cur().filter((p) => p.off);
  R.append(el("div", { class: "side-h" }, el("h2", { text: "Your video" })));
  R.append(el("div", { class: "pad" },
    el("div", { class: "box" },
      el("div", { class: "stat" }, el("span", { class: "lbl", text: "Length" }), el("b", { text: fmtTime(finalTotal()) })),
      el("div", { class: "stat" }, el("span", { class: "lbl", text: "Pieces in the timeline" }), el("span", { class: "num", text: String(K.length) })),
      el("div", { class: "stat" }, el("span", { class: "lbl", text: "Removed pieces" }), el("span", { class: "num", text: String(off.length) })),
      el("div", { class: "stat" }, el("span", { class: "lbl", text: "Original" }), el("span", { class: "num", text: fmtTime(S.info.duration) }))),
    el("p", { class: "hint", text: "Click a piece in the timeline to change it. Ctrl+click or drag across empty space to pick several." })));
  const list = el("div", { class: "list" });
  for (const p of off.slice(0, 200)) list.append(el("div", { class: "item" },
    el("img", { class: "thumb", alt: "", src: thumbUrl(S.proj.source, Math.round((p.start + 0.1) * 2) / 2) }),
    el("div", { class: "t" }, el("b", { class: "num", text: fmtTime(p.start) + " – " + fmtTime(p.end) }), el("small", { text: fmtTime(outLen(p)) + " long" })),
    btn("", { icon: "restore", cls: "ghost", title: "Put back", onclick: () => restorePiece(p) })));
  if (off.length > 200) list.append(el("p", { class: "hint", text: "…and " + (off.length - 200) + " more." }));
  const all = btn("Put all back", { icon: "restore", onclick: () => change(() => { for (const p of off) p.off = false; S.proj.pieces.sort((a, b) => a.start - b.start); markHand(); }) });
  R.append(sec("Removed pieces (" + off.length + ")", "trash", false, off.length ? list : el("p", { class: "hint", text: "Nothing removed yet." }), off.length ? all : null));
  R.append(sec("Shortcuts", "info", false,
    ...[["Space", "Play / pause"], ["S", "Split at the playhead"], ["Delete", "Remove selected"], ["Ctrl+Z / Ctrl+Y", "Undo / redo"], ["Ctrl+D", "Duplicate"], ["Ctrl+A", "Select all"], ["← →", "One frame (Shift: one second)"], ["+ / −", "Zoom the timeline"]]
      .map(([k, v]) => el("div", { class: "row between" }, el("span", { class: "kbd", text: k }), el("span", { class: "note", text: v })))));
}

// ---- follow a thing: draw a box on the viewer
function startDrawBox() {
  const sel = selList();
  if (S.proj.settings.shape !== "original") { toast("Follow works with the original shape. Change the shape in Format first, or choose a 'Follow faces' shape there.", true); return; }
  if (!S.info.has_video) return;
  pause();
  const L = layout(), firstOut = L.find((r) => S.sel.has(r.p));
  if (firstOut && !(S.t >= firstOut.out0 && S.t < firstOut.out1)) { S.t = firstOut.out0 + 0.01; showFrame(); }
  $("drawLayer").hidden = false;
  toast("Drag a box tightly around the thing to follow.");
  const layer = $("drawLayer"), bx = $("drawBox");
  let x0 = 0, y0 = 0, r = null;
  const down = (e) => { layer.setPointerCapture(e.pointerId); x0 = e.clientX; y0 = e.clientY; r = null; bx.hidden = false; };
  const move = (e) => {
    if (!layer.hasPointerCapture(e.pointerId)) return;
    const lr = layer.getBoundingClientRect();
    r = { x: Math.min(x0, e.clientX), y: Math.min(y0, e.clientY), w: Math.abs(e.clientX - x0), h: Math.abs(e.clientY - y0) };
    bx.style.left = (r.x - lr.left) + "px"; bx.style.top = (r.y - lr.top) + "px"; bx.style.width = r.w + "px"; bx.style.height = r.h + "px";
  };
  const up = async () => {
    layer.removeEventListener("pointerdown", down); layer.removeEventListener("pointermove", move); layer.removeEventListener("pointerup", up);
    layer.hidden = true; bx.hidden = true;
    if (!r || r.w < 8 || r.h < 8) { toast("That box was too small. Try again."); return; }
    const v = active(), vr = v.getBoundingClientRect(), k = Math.min(vr.width / v.videoWidth, vr.height / v.videoHeight);
    const pw = v.videoWidth * k, ph = v.videoHeight * k, pl = vr.left + (vr.width - pw) / 2, pt = vr.top + (vr.height - ph) / 2;
    const box = { x: clamp((r.x - pl) / pw, 0, 1), y: clamp((r.y - pt) / ph, 0, 1), w: r.w / pw, h: r.h / ph };
    const at = segAt(S.t), srcT = at.s.p.start + (S.t - at.s.out0) * at.s.p.speed;
    const lo = Math.min(...sel.map((p) => p.start)), hi = Math.max(...sel.map((p) => p.end));
    toast("Following it through the selected pieces…");
    try {
      const res = await api("/api/track", { method: "POST", json: { input: S.proj.source, at: clamp(srcT, lo, hi), box, start: lo, end: hi } });
      const zoom = sel[0].follow.zoom || 1.6;
      change(() => sel.forEach((p) => { p.follow = { mode: "object", zoom, path: res.path.filter((pt) => pt[0] >= p.start - 0.5 && pt[0] <= p.end + 0.5) }; }));
      toast("Found it " + Math.round(res.found * 100) + "% of the time" + (res.found < 0.7 ? ". Try a tighter box or fewer pieces." : "."), res.found < 0.7);
    } catch (e) { toast(errText(e), true); }
  };
  layer.addEventListener("pointerdown", down); layer.addEventListener("pointermove", move); layer.addEventListener("pointerup", up);
}

// ================================================================== export
function exportDialog() {
  if (!kept().length) { toast("The timeline is empty: put a piece back first.", true); return; }
  let quality = S.proj.settings.quality || "best";
  const close = () => { scrim.remove(); dlg.remove(); };
  const scrim = el("div", { class: "scrim", onclick: close });
  const sh = S.proj.settings.shape || "original";
  const size = sh.startsWith("vertical") ? "1080×1920" : sh.startsWith("square") ? "1080×1080" : S.info.width + "×" + S.info.height;
  const go = async (preview) => {
    try {
      if (quality !== S.proj.settings.quality) { S.proj.settings.quality = quality; }
      await saveNow();
      await api("/api/projects/" + S.id + "/render", { method: "POST", json: { preview } });
      close(); toast(preview ? "Making a quick preview" : "Exporting your video"); Exports.show();
    } catch (e) { toast(errText(e), true); }
  };
  const dlg = el("div", { class: "dialog", role: "dialog", "aria-label": "Export" },
    el("div", { class: "side-h" }, el("h2", { text: "Export video" }), btn("", { icon: "x", cls: "ghost", title: "Close", onclick: close })),
    el("div", { class: "pad" },
      el("div", { class: "box" },
        el("div", { class: "stat" }, el("span", { class: "lbl", text: "Length" }), el("b", { text: fmtTime(finalTotal()) })),
        el("div", { class: "stat" }, el("span", { class: "lbl", text: "Size" }), el("span", { class: "num", text: size })),
        el("div", { class: "stat" }, el("span", { class: "lbl", text: "Pieces" }), el("span", { class: "num", text: String(kept().length) }))),
      el("div", { class: "field" }, el("span", { text: "Quality" }), segmented([["best", "Best"], ["high", "High"], ["small", "Smaller file"]], quality, (v) => { quality = v; })),
      el("p", { class: "hint", text: "The export is made from your original video, exact to the frame. A quick preview is small and fast, to check everything first." }),
      el("div", { class: "btns" }, btn("Quick preview", { icon: "eye", onclick: () => go(true) }), el("span", { class: "grow" }), btn("Export", { icon: "export", cls: "primary big", onclick: () => go(false) }))));
  document.body.append(scrim, dlg);
}

// ================================================================== other projects (Media panel)
function projItem(p) {
  return el("div", { class: "item", onclick: () => { location.href = "/edit?p=" + p.id; } },
    icon("layers"), el("div", { class: "t" }, el("b", { text: p.name }), el("small", { text: fmtTime(p.length, 0) + " · from " + p.source_name })), icon("chevr", "s"));
}

// ================================================================== open a project
async function openProject() {
  let r;
  try { r = await api("/api/projects/" + S.id); }
  catch (e) { location.replace("/?msg=" + encodeURIComponent(errText(e))); return; }
  S.proj = r.project; S.info = r.info;
  S.proj.pieces.forEach(fixPiece); if (!Array.isArray(S.proj.texts)) S.proj.texts = [];
  if (!Array.isArray(S.proj.steps)) S.proj.steps = [];
  for (const st of S.proj.steps) st.pieces.forEach(fixPiece);
  $("suite").hidden = false;
  $("projName").value = S.proj.name;
  $("srcName").textContent = S.proj.source.split("/").pop();
  $("srcFacts").textContent = fmtTime(S.info.duration, 0) + (S.info.has_video ? " · " + S.info.width + "×" + S.info.height : " · sound only");
  $("srcWhere").textContent = S.info.where ? "Used where it is (not copied): " + S.info.where : "A copy kept by the app.";
  if (S.info.has_video) $("srcThumb").src = thumbUrl(S.proj.source, 1);
  document.title = S.proj.name + " · " + APP_NAME;
  // the live viewer jumps around a lot, so it uses the light copy (fast to seek, plays everywhere)
  const ready = S.info.preview && S.info.preview.state === "ready";
  if (ready) setSource(mediaUrl(S.proj.source, true));
  else {
    if (S.info.plays_in_browser) setSource(mediaUrl(S.proj.source, false));
    banner("Preparing smooth playback…", 0);
    const swap = (url) => { const t = S.t; V.src = ""; setSource(url); S.t = t; for (const v of [V.a, V.b]) v.addEventListener("loadeddata", () => showFrame(), { once: true }); };
    let quick = false;
    ensurePreview(S.proj.source, S.info, (st) => {
      if (st.state === "running") banner((S.info.plays_in_browser || quick ? "Preparing smoother playback… " : "This file needs a light copy to play here (" + S.info.playback_note + "). Preparing… ") + Math.round(st.progress * 100) + "%", st.progress);
      if (st.state === "failed") banner("A light copy could not be made: " + st.error, 0);
    }, true, () => { if (!S.info.plays_in_browser) { quick = true; swap(mediaUrl(S.proj.source, "q")); } }).then((ok) => { if (ok) { const t = S.t; V.src = ""; setSource(mediaUrl(S.proj.source, true)); S.t = t; for (const v of [V.a, V.b]) v.addEventListener("loadeddata", () => showFrame(), { once: true }); banner(null); } });
  }
  bindSettings(); fillSettings(); Layers.init();
  sizeFrame(); new ResizeObserver(() => { sizeFrame(); renderTimeline(); }).observe($("stage"));
  setTimeout(fitZoom, 0);
  refreshAll();
  loadFiles().catch(() => {});
  api("/api/recipes").then((x) => { S.recipes = x.recipes; renderRecipes(); }).catch(() => {});
  api("/api/projects").then(({ projects }) => { const pl = $("projList"); for (const p of projects.filter((q) => q.id !== S.id && !q.trashed && !q.missing).slice(0, 30)) pl.append(projItem(p)); if (!pl.children.length) pl.append(el("p", { class: "hint", text: "No other projects." })); }).catch(() => {});
  openPanel(kept().length > 1 ? "pattern" : "cut");
  renderSteps();
  $("saveState").textContent = "Saved";
}
function banner(text, k) {
  const b = $("banner"); if (!text) { b.hidden = true; return; }
  b.hidden = false; $("bannerText").textContent = text; $("bannerBar").style.width = Math.round((k || 0) * 100) + "%";
}

// ================================================================== wiring
function wire() {
  for (const b of document.querySelectorAll(".rail button")) b.addEventListener("click", () => openPanel(b.dataset.panel));
  $("projName").addEventListener("change", () => { const v = $("projName").value.trim(); if (v) change(() => { S.proj.name = v; }, { keepVideo: true }); });
  // cut
  const chips = segmented([[1, "1 s"], [2, "2 s"], [3, "3 s"], [5, "5 s"], [10, "10 s"]], 2, (v) => { $("cutSec").value = String(v); });
  $("cutChips").append(chips);
  $("cutBtn").addEventListener("click", async () => {
    if (locked()) { lockedNote(); return; }
    const sec = parseFloat(String($("cutSec").value).replace(",", "."));
    const edited = S.proj.pieces.length > 1 && (S.proj.pieces.some((p) => p.off || p.speed !== 1 || p.fx.length) || S.undo.length);
    if (edited && !(await askYes("Cutting again starts the pieces of this step over. Undo brings back what you have now.", "Cut again"))) return;
    cutInto(sec);
  });
  // pattern
  $("patRemove").addEventListener("click", () => { const h = patternHits(); if (!h.length) return; if (!change(() => { for (const p of h) p.off = true; S.sel.clear(); setRule("remove"); })) return; renderPattern(); toast(Steps.countUnits(h) + " pieces removed · video now " + fmtTime(finalTotal())); });
  $("patKeep").addEventListener("click", () => { const h = new Set(patternHits()); if (!h.size) return; const gone = kept().filter((p) => !h.has(p)); if (!change(() => { for (const p of gone) p.off = true; S.sel.clear(); setRule("keep"); })) return; renderPattern(); toast(Steps.countUnits(gone) + " pieces removed · video now " + fmtTime(finalTotal())); });
  $("patSelect").addEventListener("click", () => { S.sel = new Set(patternHits()); refreshAll({ keepVideo: true }); });
  $("recipeSave").addEventListener("click", async () => {
    const name = await askText("Name for this pattern", "", "for example: remove 1st of every 3");
    if (!name) return;
    const first = selList()[0];
    let look = null;
    if (first) { look = clone(first); delete look.start; delete look.end; delete look.off; if (look.follow.mode === "object") look.follow = { mode: "none", zoom: look.follow.zoom, path: [] }; }
    S.recipes.push({ name: name.trim(), group: P.group, picks: [...P.picks].sort((a, b) => a - b), look });
    try { await storeRecipes(); toast("Saved '" + name.trim() + "'"); } catch (e) { S.recipes.pop(); toast(errText(e), true); }
  });
  // audio, text, format
  $("musicAdd").addEventListener("click", () => addFileThen("audio", "musicProg", (d) => {
    if (d.has_video) { toast("That is a video. Choose a music file such as an MP3.", true); return; }
    change(() => { S.proj.settings.music = d.id; }, { keepVideo: true }); fillFileSelects();
  }));
  $("beatBtn").addEventListener("click", beatCut);
  for (const id of ["sBass", "sTreble"]) $(id).addEventListener("input", soundLabels);
  $("capAdd").addEventListener("click", () => addFileThen("subtitles", "musicProg", (d) => {
    if (!d.subtitles) { toast("That is not a subtitle file (.srt or .vtt).", true); return; }
    change(() => { S.proj.settings.captions = d.id; }, { keepVideo: true }); fillFileSelects(); $("capNote").textContent = d.words + " words found.";
  }));
  $("addText").addEventListener("click", () => {
    change(() => S.proj.texts.push({ text: "Your words", start: +S.t.toFixed(2), end: +Math.min(S.t + 3, rawTotal()).toFixed(2), position: "bottom", size: "medium", color: "white", box: true }), { keepVideo: true });
    renderTexts(); renderOverlay();
    const cards = $("texts").querySelectorAll(".box"), w = cards.length && cards[cards.length - 1].querySelector(".input"); if (w) { w.focus(); w.select(); }
  });
  // transport
  $("tPlay").addEventListener("click", togglePlay);
  $("tStart").addEventListener("click", () => seek(0));
  $("tEnd").addEventListener("click", () => seek(rawTotal()));
  const frame = () => 1 / (S.info.fps_float || 25);
  $("tBack").addEventListener("click", () => seek(S.t - frame()));
  $("tFwd").addEventListener("click", () => seek(S.t + frame()));
  $("tMute").addEventListener("click", () => { S.mutedAll = !S.mutedAll; $("tMute").replaceChildren(icon(S.mutedAll ? "mute" : "vol")); showFrame(); $("music").muted = S.mutedAll; });
  $("frame").addEventListener("click", (e) => { if (e.target.closest("#drawLayer")) return; togglePlay(); });
  // timeline
  $("tlScroll").addEventListener("pointerdown", (e) => { if (e.target.closest("#ruler")) onRulerDown(e); else onLaneDown(e); });
  let raf = 0; $("tlScroll").addEventListener("scroll", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(renderTimeline); });
  $("tlScroll").addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); const r = $("tlScroll").getBoundingClientRect(); zoomTo(S.pps * (e.deltaY < 0 ? 1.2 : 1 / 1.2), timeAt(e.clientX - r.left + $("tlScroll").scrollLeft)); }
    else if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.preventDefault(); $("tlScroll").scrollLeft += e.deltaY; }
  }, { passive: false });
  $("zoom").addEventListener("input", () => { const t = S.t; setZoomFromSlider(); renderTimeline(); void t; });
  $("bZoomIn").addEventListener("click", () => zoomTo(S.pps * 1.5));
  $("bZoomOut").addEventListener("click", () => zoomTo(S.pps / 1.5));
  $("bFit").addEventListener("click", fitZoom);
  $("bUndo").addEventListener("click", undo); $("bRedo").addEventListener("click", redo);
  $("bSplit").addEventListener("click", splitAtPlayhead); $("bDup").addEventListener("click", duplicateSelected); $("bDel").addEventListener("click", removeSelected);
  // resize the timeline by dragging its top edge
  $("tlResize").addEventListener("pointerdown", (e) => {
    const h0 = $("tl").offsetHeight, y0 = e.clientY; e.target.setPointerCapture(e.pointerId);
    const mv = (ev) => { document.documentElement.style.setProperty("--tl", clamp(h0 - (ev.clientY - y0), 150, window.innerHeight * 0.6) + "px"); };
    const up = () => { e.target.removeEventListener("pointermove", mv); e.target.removeEventListener("pointerup", up); };
    e.target.addEventListener("pointermove", mv); e.target.addEventListener("pointerup", up);
  });
  document.addEventListener("keydown", (e) => {
    if (!S.proj) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || document.querySelector(".dialog")) return;
    const k = e.key, mod = e.ctrlKey || e.metaKey;
    if (k === " ") { e.preventDefault(); togglePlay(); }
    else if (mod && k.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
    else if (mod && k.toLowerCase() === "y") { e.preventDefault(); redo(); }
    else if (mod && k.toLowerCase() === "a") { e.preventDefault(); S.sel = new Set(kept()); refreshAll({ keepVideo: true }); }
    else if (mod && k.toLowerCase() === "d") { e.preventDefault(); duplicateSelected(); }
    else if (mod && k.toLowerCase() === "b") { e.preventDefault(); splitAtPlayhead(); }
    else if (k.toLowerCase() === "s" && !mod) { e.preventDefault(); splitAtPlayhead(); }
    else if (k === "Delete" || k === "Backspace") { e.preventDefault(); removeSelected(); }
    else if (k === "Escape") { S.sel.clear(); refreshAll({ keepVideo: true }); }
    else if (k === "ArrowLeft") { e.preventDefault(); seek(S.t - (e.shiftKey ? 1 : frame())); }
    else if (k === "ArrowRight") { e.preventDefault(); seek(S.t + (e.shiftKey ? 1 : frame())); }
    else if (k === "Home") seek(0); else if (k === "End") seek(rawTotal());
    else if (k === "+" || k === "=") zoomTo(S.pps * 1.5); else if (k === "-") zoomTo(S.pps / 1.5);
  });
  // Leaving the page within half a second of a change: send the save in a way that survives closing the page.
  window.addEventListener("pagehide", () => {
    if (!S.saveTimer) return;
    clearTimeout(S.saveTimer); S.saveTimer = null;
    const body = JSON.stringify({ name: S.proj.name, pieces: S.proj.pieces, settings: S.proj.settings, texts: S.proj.texts, steps: S.proj.steps, rule: S.proj.rule });
    try { fetch("/api/projects/" + S.id, { method: "PUT", keepalive: body.length < 60000, headers: { "X-EditForge-Token": TOKEN, "Content-Type": "application/json" }, body }); } catch (e) { /* closing */ }
  });
}

(async function init() {
  topBar("/edit", [el("div", { class: "lenpill", title: "Length of the finished video" }, icon("clock", "s"), el("span", { text: "Video" }), el("b", { id: "topLen", text: "0:00.0" })),
    btn("Export", { icon: "export", cls: "primary", onclick: () => exportDialog() })]);
  paintIcons(); enhanceAll();
  if (!S.id) { location.replace("/"); return; }
  wire(); await openProject();
})();
