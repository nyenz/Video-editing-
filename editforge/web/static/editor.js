"use strict";
// The Editor: a video cut into pieces. Pick pieces (by click, group or pattern) and change them.
const video = $("player");
const MIN_PIECE = 0.08;
const FX_NAMES = { hflip: "flipped", mirror: "mirror", vflip: "upside down", grayscale: "b&w", invert: "inverted", sepia: "sepia", blur: "blur", sharpen: "sharp", vignette: "corners" };

const S = {
  id: new URLSearchParams(window.location.search).get("p") || "",
  proj: null, info: null,
  picked: new Set(), lastClick: null,
  undo: [], redo: [],
  saveTimer: null, saving: Promise.resolve(),
  queue: [], queuePos: 0, stopAt: null,      // quick play
  recipes: [], files: [],
};

// ---------------------------------------------------------------- small helpers
function blankPiece(start, end) {
  return { start, end, off: false, speed: 1, reverse: false, mute: false, volume: 1, fx: [], zoom: { mode: "none", amount: 1.3 }, color: { brightness: 0, contrast: 1, saturation: 1 },
    follow: { mode: "none", zoom: 1.6, path: [] } };
}
function clonePiece(p) { return JSON.parse(JSON.stringify(p)); }
function num(id, fallback) { const v = parseFloat(String($(id).value).replace(",", ".")); return isFinite(v) ? v : fallback; }
function pickedList() { return [...S.picked].filter((i) => i < S.proj.pieces.length).sort((a, b) => a - b); }
function outLen(p) { return (p.end - p.start) / p.speed; }

// ---------------------------------------------------------------- saving and undo
function snapshot() { return JSON.stringify({ pieces: S.proj.pieces, settings: S.proj.settings, name: S.proj.name, texts: S.proj.texts }); }
function remember() { S.undo.push(snapshot()); if (S.undo.length > 60) S.undo.shift(); S.redo = []; }
function restore(json) { const d = JSON.parse(json); S.proj.pieces = d.pieces; S.proj.settings = d.settings; S.proj.name = d.name; S.proj.texts = d.texts || []; S.picked.clear(); renderTexts(); fillSettings(); $("projName").value = S.proj.name; renderAll(); scheduleSave(); }
function undo() { if (!S.undo.length) return; S.redo.push(snapshot()); restore(S.undo.pop()); }
function redo() { if (!S.redo.length) return; S.undo.push(snapshot()); restore(S.redo.pop()); }
function scheduleSave() { note("saveState", "Saving ..."); clearTimeout(S.saveTimer); S.saveTimer = setTimeout(saveNow, 500); }
function saveNow() {
  clearTimeout(S.saveTimer); S.saveTimer = null;
  const body = { name: S.proj.name, pieces: S.proj.pieces, settings: S.proj.settings, texts: S.proj.texts };
  S.saving = S.saving.then(() => api("/api/projects/" + S.id, { method: "PUT", json: body }))
    .then(() => { if (!S.saveTimer) note("saveState", "Saved"); })
    .catch((e) => { note("saveState", "NOT saved: " + errText(e)); });
  return S.saving;
}
/** Change the project: fn does the change; everything is redrawn and saved, and Undo can take it back. */
function change(fn) { remember(); fn(); renderAll(); scheduleSave(); }

// ---------------------------------------------------------------- home: project list and new project
async function loadFiles() {
  S.files = (await api("/api/files")).files;
  return S.files;
}
async function showHome() {
  $("home").hidden = false;
  const files = await loadFiles();
  const sel = $("newSource"); sel.replaceChildren();
  const vids = files.filter((f) => !f.audio && !f.subtitles);
  if (!vids.length) sel.append(el("option", { value: "", text: "(add a video first)" }));
  for (const f of vids) sel.append(el("option", { value: f.id, text: f.name + "  -  " + fmtSize(f.size) + "  (" + f.where + ")" }));
  const { projects } = await api("/api/projects");
  const box = $("projects"); box.replaceChildren();
  if (!projects.length) box.append(el("div", { class: "empty", text: "No projects yet." }));
  for (const p of projects) {
    const del = el("button", { class: "small", text: "Delete" });
    del.addEventListener("click", async () => { if (window.confirm("Delete the project '" + p.name + "'? Finished videos are kept.")) { await api("/api/projects/" + p.id, { method: "DELETE" }); showHome(); } });
    box.append(el("div", { class: "proj" }, el("span", {}, el("b", { text: p.name }), el("span", { class: "note", text: "  -  " + p.pieces + " piece" + (p.pieces === 1 ? "" : "s") + " from " + p.source_name })),
      el("span", { class: "acts" }, el("a", { class: "button", href: "/edit?p=" + p.id, text: "Open" }), del)));
  }
}
$("newBtn").addEventListener("click", () => {
  const src = $("newSource").value;
  if (!src) { note("newStatus", "Add a video first."); return; }
  const sec = num("newInterval", 0);
  if ($("newSliceOn").checked && !(sec >= 0.1)) { note("newStatus", "Write how many seconds each piece should be, for example 2."); return; }
  newProject(src, $("newSliceOn").checked ? sec : 0);
});
$("newFile").addEventListener("change", (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (f) uploadFile(f, $("newBar"), "newStatus", async (data) => { await showHome(); $("newSource").value = data.id; });
});

// ---------------------------------------------------------------- opening a project
async function openProject() {
  let r;
  try { r = await api("/api/projects/" + S.id); }
  catch (e) { $("home").hidden = false; note("newStatus", errText(e)); S.id = ""; showHome(); return; }
  S.proj = r.project; S.info = r.info;
  if (!Array.isArray(S.proj.texts)) S.proj.texts = [];
  for (const p of S.proj.pieces) if (!p.follow) p.follow = { mode: "none", zoom: 1.6, path: [] };
  $("work").hidden = false;
  $("projName").value = S.proj.name;
  note("projFacts", "From " + S.proj.source.split("/").pop() + " - " + fmtTime(S.info.duration, true) + " long" + (S.info.has_video ? ", " + S.info.width + "x" + S.info.height : ", sound only"));
  showVideo(video, S.proj.source, S.info, "pvBox", "pvText", "pvBar");
  fillMusic(); fillSettings(); renderPicks(); renderRecipes(); renderTexts(); renderAll(); note("saveState", "Saved");
  startJobList("jobs", async () => { await loadFiles(); fillMusic(); });
  loadFiles().then(fillMusic).catch(() => {});
  api("/api/recipes").then((r) => { S.recipes = r.recipes; renderRecipes(); }).catch(() => {});
  api("/api/state").then((st) => {
    const missing = [];
    if (!st.features.faces) missing.push("following faces");
    if (!st.features.objects) missing.push("following a thing");
    if (!st.features.captions) missing.push("automatic captions (a subtitle file still works)");
    note("featNote", missing.length ? "Not available until a free add-on is installed: " + missing.join("; ") + ". See the README file, section \"Optional add-ons\"." : "");
  }).catch(() => {});
}

// ---------------------------------------------------------------- step 1: cutting into pieces
function hasEdits() {
  return S.proj.pieces.some((p) => p.off || p.speed !== 1 || p.reverse || p.mute || p.volume !== 1 || p.fx.length || p.zoom.mode !== "none" || p.follow.mode !== "none" || p.color.brightness !== 0 || p.color.contrast !== 1 || p.color.saturation !== 1);
}
function replacePieces(edges, what) {
  if (S.proj.pieces.length > 1 && hasEdits() && !window.confirm("Cutting again starts over: the edits on the pieces you have now will be lost. (Undo brings them back.) Go on?")) return;
  const pieces = [];
  for (let i = 0; i + 1 < edges.length; i++) if (edges[i + 1] - edges[i] >= MIN_PIECE) pieces.push(blankPiece(+edges[i].toFixed(4), +edges[i + 1].toFixed(4)));
  if (!pieces.length) { note("sliceNote", "That would leave no pieces."); return; }
  if (pieces.length > 5000) { note("sliceNote", "That would make " + pieces.length + " pieces. The most is 5000. Choose longer pieces."); return; }
  change(() => { S.proj.pieces = pieces; S.picked.clear(); });
  note("sliceNote", "Cut into " + pieces.length + " pieces " + what + ".");
}
$("sliceBtn").addEventListener("click", () => {
  const sec = num("sliceSec", 0), d = S.info.duration;
  if (!(sec >= 0.1)) { note("sliceNote", "Write how many seconds each piece should be (at least 0.1), for example 2."); return; }
  const edges = [];
  for (let t = 0; t < d - 1e-6; t += sec) edges.push(Math.min(d, t));
  edges.push(d);
  if (edges.length > 2 && edges[edges.length - 1] - edges[edges.length - 2] < MIN_PIECE) edges.splice(edges.length - 2, 1);
  replacePieces(edges, "of " + sec + " seconds");
});
$("beatBtn").addEventListener("click", async () => {
  const every = Math.round(num("sliceBeats", 0)), music = S.proj.settings.music;
  if (!music) { note("sliceNote", "Choose your music first, in step 4 (Music). Then come back and press this button."); return; }
  if (!(every >= 1)) { note("sliceNote", "Write a whole number of beats, for example 4."); return; }
  note("sliceNote", "Listening to the music to find the beat ...");
  let b;
  try { b = await api("/api/beats", { method: "POST", json: { input: music } }); }
  catch (e) { note("sliceNote", errText(e)); return; }
  if (!b.beats.length) { note("sliceNote", "I could not find a clear beat in this music. " + (b.notes || []).join(" ")); return; }
  // The music restarts when it ends, so the beat times repeat for the whole video.
  const d = S.info.duration, edges = [0];
  for (let lap = 0; lap * b.duration < d; lap++) {
    for (let i = every; i < b.beats.length; i += every) {
      const t = lap * b.duration + b.beats[i];
      if (t - edges[edges.length - 1] >= MIN_PIECE && d - t >= MIN_PIECE) edges.push(t);
    }
    if (lap > 2000) break;
  }
  edges.push(d);
  replacePieces(edges, "on every " + every + " beat" + (every === 1 ? "" : "s") + " (the music is about " + Math.round(b.tempo) + " beats a minute)");
  if (S.proj.settings.transition !== "none") note("sliceNote", $("sliceNote").textContent + " Note: crossfades and other transitions make the video shorter at every cut, so the cuts drift off the beat. Choose \"Straight cut\" in step 4 to stay on the beat.");
});

// ---------------------------------------------------------------- step 2: the grid and picking
function tagsOf(p) {
  const t = [];
  if (p.off) t.push("removed");
  if (p.speed !== 1) t.push(p.speed + "x");
  if (p.reverse) t.push("backwards");
  for (const f of p.fx) t.push(FX_NAMES[f] || f);
  if (p.follow.mode === "face") t.push("follows face"); else if (p.follow.mode === "object") t.push("follows thing");
  else if (p.zoom.mode !== "none") t.push("zoom " + p.zoom.mode);
  if (p.color.brightness !== 0 || p.color.contrast !== 1 || p.color.saturation !== 1) t.push("colour");
  if (p.mute) t.push("muted"); else if (p.volume !== 1) t.push("sound " + Math.round(p.volume * 100) + "%");
  return t.join(", ");
}
function renderGrid() {
  const grid = $("grid"), keepScroll = grid.scrollTop;
  grid.replaceChildren();
  S.proj.pieces.forEach((p, i) => {
    const cls = ["tile"].concat(S.picked.has(i) ? ["sel"] : [], p.off ? ["off"] : [], p.fx.map((f) => "fx-" + f));
    const tile = el("button", { class: cls.join(" "), type: "button", "aria-pressed": S.picked.has(i) ? "true" : "false",
      title: "Piece " + (i + 1) + ": " + fmtTime(p.start) + " to " + fmtTime(p.end) + " of the original" });
    if (S.info.has_video) tile.append(el("img", { loading: "lazy", alt: "", src: thumbUrl(S.proj.source, Math.min(p.end - 0.02, p.start + Math.min(0.2, (p.end - p.start) / 2))) }));
    tile.append(el("div", { class: "cap" }, el("span", { text: String(i + 1) }), el("span", { text: outLen(p).toFixed(2) + "s" })));
    tile.append(el("div", { class: "tags", text: tagsOf(p) }));
    tile.addEventListener("click", (e) => clickTile(i, e.shiftKey));
    tile.addEventListener("dblclick", () => quickPlay([i]));
    grid.append(tile);
  });
  grid.scrollTop = keepScroll;
}
function clickTile(i, shift) {
  if (shift && S.lastClick !== null) {
    const a = Math.min(S.lastClick, i), b = Math.max(S.lastClick, i);
    for (let k = a; k <= b; k++) S.picked.add(k);
  } else if (S.picked.has(i)) S.picked.delete(i); else S.picked.add(i);
  S.lastClick = i;
  renderAll();
}
function renderPicks() {
  const group = Math.max(1, Math.min(100, Math.round(num("patGroup", 3)))), box = $("patPicks");
  const was = new Set([...box.querySelectorAll("input:checked")].map((c) => +c.value));
  if (!box.children.length && !was.size) was.add(1);
  box.replaceChildren();
  for (let k = 1; k <= group; k++) {
    const c = el("input", { type: "checkbox", value: String(k) });
    c.checked = was.has(k);
    const lab = el("label", { class: c.checked ? "on" : "" }, c, el("span", { text: String(k) }));
    c.addEventListener("change", () => { lab.className = c.checked ? "on" : ""; });
    box.append(lab);
  }
}
function patternNow() {
  const group = Math.max(1, Math.min(100, Math.round(num("patGroup", 3))));
  return { group, picks: [...$("patPicks").querySelectorAll("input:checked")].map((c) => +c.value) };
}
function pickPattern(group, picks, add) {
  if (!add) S.picked.clear();
  S.proj.pieces.forEach((p, i) => { if (picks.includes((i % group) + 1)) S.picked.add(i); });
  S.lastClick = null;
  renderAll();
}
$("patGroup").addEventListener("input", renderPicks);
$("patBtn").addEventListener("click", () => {
  const { group, picks } = patternNow();
  if (!picks.length) { note("pickNote", "Tick at least one number next to 'pick number'."); return; }
  pickPattern(group, picks, $("patAdd").checked);
});
$("selAll").addEventListener("click", () => { S.proj.pieces.forEach((p, i) => S.picked.add(i)); renderAll(); });
$("selNone").addEventListener("click", () => { S.picked.clear(); renderAll(); });
$("selInvert").addEventListener("click", () => { const n = new Set(); S.proj.pieces.forEach((p, i) => { if (!S.picked.has(i)) n.add(i); }); S.picked = n; renderAll(); });

// saved patterns ("recipes"): a pattern plus the edits to give the picked pieces
function renderRecipes() {
  const sel = $("recipes"); sel.replaceChildren();
  if (!S.recipes.length) sel.append(el("option", { value: "", text: "(none saved yet)" }));
  S.recipes.forEach((r, i) => sel.append(el("option", { value: String(i), text: r.name + "  -  " + r.picks.join(",") + " of every " + r.group + (r.look ? " + edits" : "") })));
}
async function storeRecipes() { S.recipes = (await api("/api/recipes", { method: "PUT", json: S.recipes })).recipes; renderRecipes(); }
$("recipeSave").addEventListener("click", async () => {
  const { group, picks } = patternNow();
  if (!picks.length) { note("recipeNote", "Tick at least one number in the pattern first."); return; }
  const name = window.prompt("Name for this pattern (for example: mirror every 2nd of 3)");
  if (!name || !name.trim()) return;
  const first = pickedList()[0];
  let look = null;
  if (first !== undefined) {
    look = clonePiece(S.proj.pieces[first]); delete look.start; delete look.end;
    if (look.follow.mode === "object") look.follow = { mode: "none", zoom: look.follow.zoom, path: [] };   // a drawn box belongs to one moment of one video
  }
  S.recipes.push({ name: name.trim(), group, picks, look });
  try { await storeRecipes(); note("recipeNote", "Saved '" + name.trim() + "'" + (look ? " with the edits of piece " + (first + 1) + "." : " (pattern only, because no piece was picked).")); }
  catch (e) { S.recipes.pop(); note("recipeNote", errText(e)); }
});
$("recipeUse").addEventListener("click", () => {
  const r = S.recipes[+$("recipes").value];
  if (!r) { note("recipeNote", "There is no saved pattern to use yet."); return; }
  $("patGroup").value = r.group; $("patPicks").replaceChildren(); renderPicks();
  for (const c of $("patPicks").querySelectorAll("input")) { c.checked = r.picks.includes(+c.value); c.parentElement.className = c.checked ? "on" : ""; }
  if (r.look) {
    change(() => {
      S.picked.clear();
      S.proj.pieces.forEach((p, i) => { if (r.picks.includes((i % r.group) + 1)) { S.picked.add(i); Object.assign(p, clonePiece(r.look), { start: p.start, end: p.end }); } });
    });
    note("recipeNote", "Used '" + r.name + "' on " + S.picked.size + " pieces.");
  } else { pickPattern(r.group, r.picks, false); note("recipeNote", "Picked " + S.picked.size + " pieces with '" + r.name + "'."); }
});
$("recipeDel").addEventListener("click", async () => {
  const i = +$("recipes").value, r = S.recipes[i];
  if (!r || !window.confirm("Delete the saved pattern '" + r.name + "'?")) return;
  S.recipes.splice(i, 1);
  try { await storeRecipes(); note("recipeNote", "Deleted."); } catch (e) { note("recipeNote", errText(e)); }
});

// quick play: plays the source for each piece in order (cuts, order and speed only)
function quickPlay(indices) {
  S.queue = indices.map((i) => S.proj.pieces[i]).filter((p) => p && !p.off);
  if (!S.queue.length) { note("pickNote", "There is nothing to play: those pieces are removed."); return; }
  S.queuePos = 0; playQueued();
}
/** Show a piece's look on the player with CSS, so quick play is close to the real thing (not backwards, not following). */
function dressPlayer(p) {
  if (!p) { video.style.transform = ""; video.style.filter = ""; video.controls = true; video.muted = false; video.volume = 1; return; }
  const sx = p.fx.includes("hflip") ? -1 : 1, sy = p.fx.includes("vflip") ? -1 : 1;
  const z = p.follow.mode !== "none" ? p.follow.zoom : p.zoom.mode === "none" ? 1 : p.zoom.amount;
  video.style.transform = "scale(" + sx * z + "," + sy * z + ")";
  const f = [];
  if (p.fx.includes("grayscale")) f.push("grayscale(1)");
  if (p.fx.includes("invert")) f.push("invert(1)");
  if (p.fx.includes("sepia")) f.push("sepia(1)");
  if (p.fx.includes("blur")) f.push("blur(3px)");
  if (p.color.brightness !== 0) f.push("brightness(" + (1 + p.color.brightness) + ")");
  if (p.color.contrast !== 1) f.push("contrast(" + p.color.contrast + ")");
  if (p.color.saturation !== 1) f.push("saturate(" + p.color.saturation + ")");
  video.style.filter = f.join(" ");
  video.controls = false;                     // the buttons on the video would be flipped and zoomed too
  video.muted = p.mute || !S.proj.settings.original_sound; video.volume = Math.max(0, Math.min(1, p.volume));
}
function playQueued() {
  const p = S.queue[S.queuePos];
  dressPlayer(p || null);
  if (!p) { S.stopAt = null; video.pause(); video.playbackRate = 1; return; }
  S.stopAt = p.end; video.playbackRate = Math.max(0.25, Math.min(4, p.speed)); video.currentTime = p.start;
  const pr = video.play(); if (pr && pr.catch) pr.catch(() => {});
}
function watchPlay() {
  if (S.stopAt !== null && (video.currentTime >= S.stopAt - 0.01 || video.ended)) { S.queuePos++; playQueued(); }
  requestAnimationFrame(watchPlay);
}
video.addEventListener("pause", () => { if (S.stopAt !== null && !video.seeking && video.currentTime < S.stopAt - 0.05) { S.stopAt = null; S.queue = []; video.playbackRate = 1; dressPlayer(null); } });
$("stage").addEventListener("click", (e) => { if (S.stopAt !== null) { e.preventDefault(); S.stopAt = null; S.queue = []; video.pause(); video.playbackRate = 1; dressPlayer(null); } });
$("playPicked").addEventListener("click", () => { const l = pickedList(); if (!l.length) note("pickNote", "Pick some pieces first."); else quickPlay(l); });
$("playAll").addEventListener("click", () => quickPlay(S.proj.pieces.map((p, i) => i)));

// ---------------------------------------------------------------- step 3: changing the picked pieces
function eachPicked(fn) {
  const list = pickedList();
  if (!list.length) { note("toolNote", "Pick one or more pieces in step 2 first."); return false; }
  change(() => list.forEach((i) => fn(S.proj.pieces[i], i)));
  return true;
}
const ACTIONS = {
  remove: () => eachPicked((p) => { p.off = true; }),
  restore: () => eachPicked((p) => { p.off = false; }),
  speed: () => eachPicked((p) => { p.speed = num("speedSel", 1); }),
  reverse: () => { const on = !pickedList().every((i) => S.proj.pieces[i].reverse); eachPicked((p) => { p.reverse = on; }); },
  mute: () => { const on = !pickedList().every((i) => S.proj.pieces[i].mute); eachPicked((p) => { p.mute = on; }); },
  volume: () => eachPicked((p) => { p.volume = num("volSel", 1); p.mute = false; }),
  zoom: () => eachPicked((p) => { p.zoom = { mode: $("zoomMode").value, amount: num("zoomAmt", 1.3) }; }),
  followFace: () => { if (eachPicked((p) => { p.follow = { mode: "face", zoom: num("followZoom", 1.6), path: [] }; })) note("toolNote", "These pieces will zoom in and follow the face. Use Quick preview to check it."); },
  followOff: () => eachPicked((p) => { p.follow = { mode: "none", zoom: p.follow.zoom, path: [] }; }),
  color: () => eachPicked((p) => { p.color = { brightness: num("cBright", 0), contrast: num("cContrast", 1), saturation: num("cSat", 1) }; }),
  reset: () => eachPicked((p) => { Object.assign(p, blankPiece(p.start, p.end)); }),
  copy: () => {
    const list = pickedList(); if (!list.length) { note("toolNote", "Pick one or more pieces in step 2 first."); return; }
    change(() => { const picked = new Set(); let shift = 0; for (const i of list) { S.proj.pieces.splice(i + shift + 1, 0, clonePiece(S.proj.pieces[i + shift])); shift++; picked.add(i + shift); } S.picked = picked; });
  },
  left: () => movePicked(-1),
  right: () => movePicked(1),
};
function movePicked(dir) {
  const list = pickedList(); if (!list.length) { note("toolNote", "Pick one or more pieces in step 2 first."); return; }
  const P = S.proj.pieces, order = dir < 0 ? list : list.slice().reverse(), picked = new Set(list);
  if ((dir < 0 && list[0] === 0) || (dir > 0 && list[list.length - 1] === P.length - 1)) { note("toolNote", "Those pieces are already at the " + (dir < 0 ? "start" : "end") + "."); return; }
  change(() => { for (const i of order) { const j = i + dir; [P[i], P[j]] = [P[j], P[i]]; picked.delete(i); picked.add(j); } S.picked = picked; });
}
function toggleFx(name) {
  const on = !pickedList().every((i) => S.proj.pieces[i].fx.includes(name));
  eachPicked((p) => { p.fx = p.fx.filter((f) => f !== name); if (on) p.fx.push(name); });
}
function grow(which) {
  const sec = num("growSec", 0), d = S.info.duration;
  if (!(sec > 0)) { note("toolNote", "Write how many seconds, for example 1."); return; }
  let blocked = 0;
  const ok = eachPicked((p) => {
    let a = p.start, b = p.end;
    if (which === "start-") a = Math.max(0, a - sec); else if (which === "start+") a = a + sec;
    else if (which === "end-") b = b - sec; else b = Math.min(d, b + sec);
    if (b - a < MIN_PIECE) { blocked++; return; }
    if ((which === "start-" && a === p.start) || (which === "end+" && b === p.end)) blocked++;
    p.start = +a.toFixed(4); p.end = +b.toFixed(4);
  });
  if (ok) note("toolNote", blocked ? blocked + " piece(s) could not change: they would become empty, or they are already at the start or end of the video." : "Done.");
}
for (const b of document.querySelectorAll("[data-act]")) b.addEventListener("click", () => { note("toolNote", ""); ACTIONS[b.dataset.act](); });
for (const b of document.querySelectorAll("[data-fx]")) b.addEventListener("click", () => { note("toolNote", ""); toggleFx(b.dataset.fx); });
for (const b of document.querySelectorAll("[data-grow]")) b.addEventListener("click", () => grow(b.dataset.grow));

function renderTools() {
  const list = pickedList(), any = list.length > 0;
  $("noPick").hidden = any;
  note("pickNote", any ? list.length + " of " + S.proj.pieces.length + " pieces picked" : "No pieces picked.");
  for (const b of document.querySelectorAll("#tools button")) b.disabled = !any;
  for (const b of document.querySelectorAll("[data-fx]")) b.classList.toggle("on", any && list.every((i) => S.proj.pieces[i].fx.includes(b.dataset.fx)));
  document.querySelector('[data-act="reverse"]').classList.toggle("on", any && list.every((i) => S.proj.pieces[i].reverse));
  document.querySelector('[data-act="mute"]').classList.toggle("on", any && list.every((i) => S.proj.pieces[i].mute));
  $("undoBtn").disabled = !S.undo.length; $("redoBtn").disabled = !S.redo.length;
}

// ---------------------------------------------------------------- step 4: whole-video settings
const SETTINGS = [["sCap", "captions"], ["sCapStyle", "caption_style"], ["sCapPos", "caption_position"], ["sCapSize", "caption_size"], ["sShape", "shape"], ["sTrans", "transition"], ["sTransS", "transition_s", true], ["sQuality", "quality"], ["sMusic", "music"],
  ["sMusicVol", "music_volume", true], ["sFadeIn", "fade_in", true], ["sFadeOut", "fade_out", true]];
const CHECKS = [["sDuck", "duck"], ["sOrig", "original_sound"], ["sLoud", "even_loudness"]];
function fillCaptions() {
  const sel = $("sCap"), cur = S.proj ? S.proj.settings.captions : "off";
  sel.replaceChildren(el("option", { value: "off", text: "Off" }), el("option", { value: "auto", text: "Automatic (listen to the speech)" }));
  for (const f of S.files.filter((x) => x.subtitles)) sel.append(el("option", { value: f.id, text: "From file: " + f.name }));
  if (![...sel.options].some((o) => o.value === cur)) sel.append(el("option", { value: cur, text: cur.split("/").pop() + " (missing)" }));
  sel.value = cur;
}
function fillMusic() {
  fillCaptions();
  const sel = $("sMusic"), cur = S.proj ? S.proj.settings.music : "";
  sel.replaceChildren(el("option", { value: "", text: "No music" }));
  for (const f of S.files.filter((x) => x.audio)) sel.append(el("option", { value: f.id, text: f.name }));
  if (cur && ![...sel.options].some((o) => o.value === cur)) sel.append(el("option", { value: cur, text: cur.split("/").pop() + " (missing)" }));
  sel.value = cur;
}
function fillSettings() {
  const st = S.proj.settings;
  for (const [id, key] of SETTINGS) $(id).value = String(st[key]);
  for (const [id, key] of CHECKS) $(id).checked = !!st[key];
}
for (const [id, key, isNum] of SETTINGS) $(id).addEventListener("change", () => change(() => { S.proj.settings[key] = isNum ? parseFloat($(id).value) : $(id).value; }));
for (const [id, key] of CHECKS) $(id).addEventListener("change", () => change(() => { S.proj.settings[key] = $(id).checked; }));
$("musicFile").addEventListener("change", (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (f) uploadFile(f, $("musicBar"), "musicStatus", async (data) => {
    if (data.has_video) { note("musicStatus", "That is a video, not a music file. Choose an audio file such as an MP3."); return; }
    await loadFiles(); change(() => { S.proj.settings.music = data.id; }); fillMusic();
  });
});

$("capFile").addEventListener("change", (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (f) uploadFile(f, $("capBar"), "capStatus", async (data) => {
    if (!data.subtitles) { note("capStatus", "That is not a subtitle file. Choose a .srt or .vtt file."); return; }
    await loadFiles(); change(() => { S.proj.settings.captions = data.id; }); fillMusic();
    note("capStatus", "Ready: " + data.name + " (" + data.words + " words)");
  });
});

// words on the picture
function renderTexts() {
  const box = $("texts"); box.replaceChildren();
  if (!S.proj.texts.length) box.append(el("div", { class: "empty", text: "No words yet." }));
  S.proj.texts.forEach((t, i) => {
    const field = (cls, value, label, apply) => {
      const inp = el("input", { type: "text", class: cls, value: String(value), "aria-label": label });
      inp.addEventListener("change", () => change(() => apply(inp.value)));
      return inp;
    };
    const pick = (value, options, label, apply) => {
      const sel = el("select", { "aria-label": label });
      for (const [v, name] of options) sel.append(el("option", { value: v, text: name }));
      sel.value = value; sel.addEventListener("change", () => change(() => apply(sel.value)));
      return sel;
    };
    const secs = (v) => { const n = parseFloat(String(v).replace(",", ".")); return isFinite(n) && n > 0 ? n : 0; };
    const boxed = el("input", { type: "checkbox" }); boxed.checked = t.box; boxed.addEventListener("change", () => change(() => { t.box = boxed.checked; }));
    const del = el("button", { class: "small", text: "Delete" }); del.addEventListener("click", () => change(() => { S.proj.texts.splice(i, 1); renderTexts(); }));
    box.append(el("div", { class: "textRow" },
      field("words", t.text, "Words", (v) => { if (v.trim()) t.text = v.trim().slice(0, 200); renderTexts(); }),
      el("label", {}, el("span", { text: "from" }), field("tiny", t.start, "From second", (v) => { t.start = secs(v); renderTexts(); }), el("span", { text: "s" })),
      el("label", {}, el("span", { text: "to" }), field("tiny", t.end > 0 ? t.end : "", "To second", (v) => { t.end = secs(v); renderTexts(); }), el("span", { text: "s" })),
      pick(t.position, [["bottom", "Bottom"], ["center", "Middle"], ["top", "Top"], ["top_left", "Top left"], ["top_right", "Top right"], ["bottom_left", "Bottom left"], ["bottom_right", "Bottom right"]], "Place", (v) => { t.position = v; }),
      pick(t.size, [["small", "Small"], ["medium", "Medium"], ["large", "Large"], ["huge", "Huge"]], "Size", (v) => { t.size = v; }),
      pick(t.color, [["white", "White"], ["yellow", "Yellow"], ["black", "Black"], ["red", "Red"], ["blue", "Blue"], ["green", "Green"]], "Colour", (v) => { t.color = v; }),
      el("label", { class: "inline" }, boxed, el("span", { text: "dark box behind" })), del));
  });
}
$("addText").addEventListener("click", () => {
  if (S.proj.texts.length >= 50) return;
  change(() => { S.proj.texts.push({ text: "Your words", start: 0, end: 0, position: "bottom", size: "medium", color: "white", box: true }); });
  renderTexts();
  const rows = $("texts").querySelectorAll("input.words"); const last = rows[rows.length - 1]; if (last) { last.focus(); last.select(); }
});

// follow a thing: draw a box on the video, then the app follows it through the picked pieces
const draw = { on: false, x0: 0, y0: 0, rect: null };
function pictureRect() {      // where the picture really is inside the <video> box (it is letter-boxed)
  const r = video.getBoundingClientRect(), vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh) return null;
  const k = Math.min(r.width / vw, r.height / vh), w = vw * k, h = vh * k;
  return { left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, width: w, height: h };
}
function endDraw() { draw.on = false; $("drawLayer").hidden = true; $("drawBox").hidden = true; $("drawHelp").hidden = true; }
$("followThing").addEventListener("click", () => {
  const list = pickedList();
  if (!list.length) { note("toolNote", "Pick one or more pieces in step 2 first."); return; }
  const lo = Math.min(...list.map((i) => S.proj.pieces[i].start)), hi = Math.max(...list.map((i) => S.proj.pieces[i].end));
  if (!pictureRect()) { note("toolNote", "The video is not showing here, so a box cannot be drawn. Open this page in Google Chrome or Microsoft Edge."); return; }
  dressPlayer(null); S.stopAt = null; S.queue = []; video.pause();
  if (video.currentTime < lo || video.currentTime > hi) video.currentTime = Math.min(hi - 0.05, lo + 0.05);
  $("drawLayer").hidden = false; $("drawHelp").hidden = false;
  $("stage").scrollIntoView({ behavior: "smooth", block: "center" });
  note("toolNote", "Now drag a box around the thing on the video above. (To use another moment: Cancel, pause the video where you can see the thing clearly, and press the button again.)");
});
$("drawCancel").addEventListener("click", () => { endDraw(); note("toolNote", ""); });
const layer = $("drawLayer");
layer.addEventListener("pointerdown", (e) => { layer.setPointerCapture(e.pointerId); draw.on = true; draw.x0 = e.clientX; draw.y0 = e.clientY; draw.rect = null; });
layer.addEventListener("pointermove", (e) => {
  if (!draw.on) return;
  const L = layer.getBoundingClientRect(), b = $("drawBox");
  const x = Math.min(draw.x0, e.clientX), y = Math.min(draw.y0, e.clientY), w = Math.abs(e.clientX - draw.x0), h = Math.abs(e.clientY - draw.y0);
  draw.rect = { x, y, w, h };
  b.hidden = false; b.style.left = (x - L.left) + "px"; b.style.top = (y - L.top) + "px"; b.style.width = w + "px"; b.style.height = h + "px";
});
layer.addEventListener("pointerup", async () => {
  if (!draw.on) return;
  draw.on = false;
  const pr = pictureRect(), r = draw.rect, list = pickedList();
  endDraw();
  if (!pr || !r || r.w < 8 || r.h < 8) { note("toolNote", "That box was too small. Press the button and drag a bigger box."); return; }
  const box = { x: (r.x - pr.left) / pr.width, y: (r.y - pr.top) / pr.height, w: r.w / pr.width, h: r.h / pr.height };
  box.x = Math.max(0, box.x); box.y = Math.max(0, box.y); box.w = Math.min(1 - box.x, box.w); box.h = Math.min(1 - box.y, box.h);
  if (!(box.w > 0.01 && box.h > 0.01)) { note("toolNote", "The box was outside the picture. Try again."); return; }
  const lo = Math.min(...list.map((i) => S.proj.pieces[i].start)), hi = Math.max(...list.map((i) => S.proj.pieces[i].end));
  note("toolNote", "Following it through the picked pieces ...");
  try {
    const res = await api("/api/track", { method: "POST", json: { input: S.proj.source, at: video.currentTime, box, start: lo, end: hi } });
    const zoom = num("followZoom", 1.6);
    change(() => list.forEach((i) => {
      const p = S.proj.pieces[i];
      p.follow = { mode: "object", zoom, path: res.path.filter((pt) => pt[0] >= p.start - 0.5 && pt[0] <= p.end + 0.5) };
    }));
    const pct = Math.round(res.found * 100);
    note("toolNote", "Done. I could see it " + pct + "% of the time." + (pct < 70 ? " That is low: the view stays still where it was lost. Try a tighter box, or fewer pieces at a time." : " Use Quick preview to check it."));
  } catch (e) { note("toolNote", errText(e)); }
});

// ---------------------------------------------------------------- step 5: totals and making
function renderTotals() {
  const kept = S.proj.pieces.filter((p) => !p.off), total = kept.reduce((s, p) => s + outLen(p), 0);
  note("totals", S.proj.pieces.length + " pieces, " + kept.length + " kept. The finished video will be about " + fmtTime(total, true) + " long.");
  $("makeBtn").disabled = !kept.length; $("previewBtn").disabled = !kept.length;
}
async function make(preview) {
  note("makeNote", "Starting ...");
  try {
    await saveNow();
    await api("/api/projects/" + S.id + "/render", { method: "POST", json: { preview } });
    note("makeNote", preview ? "Making a quick preview. It appears below." : "Making the video in full quality. It appears below.");
  } catch (e) { note("makeNote", errText(e)); }
}
$("previewBtn").addEventListener("click", () => make(true));
$("makeBtn").addEventListener("click", () => make(false));

// ---------------------------------------------------------------- everything together
function renderAll() { renderGrid(); renderTools(); renderTotals(); }
$("projName").addEventListener("change", () => { const v = $("projName").value.trim(); if (v) change(() => { S.proj.name = v; }); else $("projName").value = S.proj.name; });
$("undoBtn").addEventListener("click", undo);
$("redoBtn").addEventListener("click", redo);
document.addEventListener("keydown", (e) => {
  if (!S.proj) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); $("selAll").click(); }
  else if (e.key === "Escape") $("selNone").click();
  else if (e.key === "Delete" || e.key === "Backspace") { if (pickedList().length) { e.preventDefault(); ACTIONS.remove(); } }
});
window.addEventListener("beforeunload", () => { if (S.saveTimer) saveNow(); });

(async function init() {
  try { if (S.id) await openProject(); else await showHome(); }
  catch (e) { $("home").hidden = false; note("newStatus", errText(e)); }
  requestAnimationFrame(watchPlay);
})();
