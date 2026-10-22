"use strict";
// Home: the three phases of the work, each with its own projects.
//   1 Clips: take many clips out of a movie.   2 Edit: cut, patterns, sound, look.   3 Final: finished videos.
const PHASES = [
  ["clips", "Clips", "scissors", "Take clips out of a movie"],
  ["edit", "Edit", "layers", "Cut, patterns, sound and look"],
  ["final", "Final", "check", "Your finished videos"],
];
const H = { phase: "clips", clipsets: [], projects: [], final: [], trash: false, view: "grid", q: "" };
try {
  H.view = localStorage.getItem("ef.home.view") === "list" ? "list" : "grid";
  const ph = localStorage.getItem("ef.home.phase");
  if (PHASES.some((p) => p[0] === ph)) H.phase = ph;
} catch (e) { /* storage may be off */ }

// ------------------------------------------------------------------ loading
async function loadAll() {
  try {
    const [a, b, c] = await Promise.all([api("/api/clipsets"), api("/api/projects"), api("/api/final")]);
    H.clipsets = a.clipsets; H.projects = b.projects; H.final = c.videos;
  } catch (e) { toast(errText(e), true); return; }
  render();
}
const loadProjects = loadAll;                // older name, used by the actions below
function itemsOf(phase) { return phase === "clips" ? H.clipsets : phase === "edit" ? H.projects : H.final; }
function shown() {
  const q = H.q.toLowerCase();
  const list = itemsOf(H.phase);
  return list.filter((p) => (H.phase === "final" || !!p.trashed === H.trash) &&
    (!q || (p.name || "").toLowerCase().includes(q) || (p.source_name || p.project_name || "").toLowerCase().includes(q)));
}

// ------------------------------------------------------------------ drawing
function renderPhases() {
  const box = $("phases"); box.replaceChildren();
  PHASES.forEach(([key, name, ic, line], i) => {
    const n = itemsOf(key).filter((p) => !p.trashed).length;
    const b = el("button", { class: "phase" + (H.phase === key ? " on" : ""), type: "button", role: "tab", "aria-selected": String(H.phase === key),
      onclick: () => { H.phase = key; H.trash = false; try { localStorage.setItem("ef.home.phase", key); } catch (e) { /* off */ } render(); } },
      el("span", { class: "phase-n", text: String(i + 1) }), el("span", { class: "phase-ic" }, icon(ic)),
      el("span", { class: "t" }, el("b", { text: name }), el("small", { text: line })), el("span", { class: "count num", text: String(n) }));
    box.append(b);
  });
}
function render() {
  renderPhases();
  const final = H.phase === "final";
  $("createRow").hidden = final;
  $("createTitle").textContent = H.phase === "clips" ? "New clip project" : "New edit project";
  $("createLine").textContent = H.phase === "clips" ? "Choose a movie, then mark as many clips as you like. Nothing is copied."
    : "Choose a video to cut into pieces. It is used where it is, nothing is copied.";
  $("fromClip").hidden = H.phase !== "edit";
  const list = shown(), grid = $("grid"), all = itemsOf(H.phase), trashN = final ? 0 : all.filter((p) => p.trashed).length;
  $("projTitle").textContent = H.trash ? "Trash" : { clips: "Clip projects", edit: "Edit projects", final: "Finished videos" }[H.phase];
  $("projCount").textContent = list.length ? String(list.length) : "";
  $("trashBtn").hidden = final;
  $("trashCount").textContent = String(trashN); $("trashCount").hidden = !trashN || H.trash;
  $("trashBtn").classList.toggle("on", H.trash);
  $("trashLabel").textContent = H.trash ? "Back to projects" : "Trash";
  $("emptyTrash").hidden = !(H.trash && trashN);
  grid.className = "proj-grid" + (H.view === "list" ? " as-list" : "");
  grid.replaceChildren();
  if (!list.length) {
    const hint = H.trash || H.q ? "" : { clips: "Press New clip project and choose a movie.", edit: "Press New edit project, or open a made clip.",
      final: "Videos you export from an Edit project appear here." }[H.phase];
    grid.append(el("div", { class: "proj-empty" }, icon(H.trash ? "trash" : final ? "check" : "film", "l"),
      el("b", { text: H.q ? "Nothing matches '" + H.q + "'." : H.trash ? "The trash is empty." : final ? "No finished videos yet." : "No projects yet." }),
      el("span", { class: "hint", text: hint })));
    return;
  }
  for (const p of list) grid.append(H.phase === "clips" ? clipCard(p) : H.phase === "edit" ? card(p) : finalCard(p));
}
function thumbBox(missing, extraCls) { return el("div", { class: "pthumb" + (missing ? " missing" : "") + (extraCls ? " " + extraCls : "") }); }
function addImg(pic, url) {
  const img = el("img", { alt: "", loading: "lazy", decoding: "async", src: url, draggable: "false" });
  img.addEventListener("error", () => img.remove());
  pic.append(img);
}
function cardShell(p, pic, lines, open, title) {
  const more = btn("", { icon: "more", cls: "ghost more", title: "More", onclick: (e) => { e.stopPropagation(); menu(p, more); } });
  pic.append(more);
  const node = el("div", { class: "pcard" + (p.missing && !p.freed ? " is-missing" : ""), tabindex: "0", role: "button", title: title || "", "aria-label": p.name },
    pic, el("div", { class: "pinfo" }, el("b", { class: "ellip", text: p.name }), ...lines));
  node.addEventListener("click", open);
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  return node;
}

/** Phase 1 card: a movie and its clips. */
function clipCard(p) {
  const pic = thumbBox(p.missing && !p.freed && !p.thumb_file);
  if (p.missing && !p.freed && !p.thumb_file) {
    pic.append(el("span", { class: "miss-t", text: "Movie not found" }));
  } else if (p.thumb_file) addImg(pic, thumbUrl(p.thumb_file, 0.5, true));
  else if (p.has_video) addImg(pic, thumbUrl(p.source, p.thumb_at, true));
  else pic.append(icon("music", "l"));
  pic.append(el("span", { class: "chip-tag" + (p.made && p.made === p.clips ? " good" : ""), text: p.clips ? p.made + " / " + p.clips + " made" : "No clips yet" }));
  if (p.freed) pic.append(el("span", { class: "chip-tag top", text: "Movie freed" }));
  const meta = p.clips + " clip" + (p.clips === 1 ? "" : "s") + " | " + fmtTime(p.length, 0);
  const open = () => { if (!H.trash) location.href = "/clips?c=" + p.id; };
  return cardShell(p, pic, [el("small", { class: "num", text: meta }),
    H.view === "list" ? el("small", { class: "num when", text: "From " + p.source_name + " · opened " + ago(p.opened) }) : null], open, "From " + p.source_name);
}
/** Phase 2 card: an edit project. */
function card(p) {
  const meta = fmtShort(p.size) + " | " + fmtTime(p.duration, 0);
  const pic = thumbBox(p.missing);
  if (p.missing) {
    pic.append(el("span", { class: "miss-t", text: "Video not found" }),
      p.trashed ? null : btn("Find it again", { icon: "link", cls: "sm", onclick: (e) => { e.stopPropagation(); relink(p); } }));
  } else if (p.has_video) addImg(pic, thumbUrl(p.source, p.thumb_at, true));
  else pic.append(icon("music", "l"));
  if (p.steps) pic.append(el("span", { class: "chip-tag", text: (p.steps + 1) + " steps" }));
  const edited = "Edited: " + fmtTime(p.length, 0) + " · " + p.pieces + " pieces" + (p.where ? "\n" + (p.linked ? "Used where it is: " : "Copy: ") + p.where : "");
  const open = () => { if (H.trash) return; if (p.missing) relink(p); else location.href = "/edit?p=" + p.id; };
  return cardShell(p, pic, [el("small", { class: "num", text: meta }),
    H.view === "list" ? el("small", { class: "num when", text: "Edited " + fmtTime(p.length, 0) + " · opened " + ago(p.opened) }) : null], open, edited);
}
/** Phase 3 card: a finished video. */
function finalCard(v) {
  const pic = thumbBox(false);
  addImg(pic, thumbUrl(v.id, Math.min(1, (v.duration || 2) / 2), true));
  pic.append(el("span", { class: "chip-tag", text: fmtTime(v.duration, 0) }));
  const open = () => watch(v);
  return cardShell(v, pic, [el("small", { class: "num", text: fmtShort(v.size) + " | " + (v.project_name ? "from " + v.project_name : "made " + ago(v.when)) })], open, v.name);
}
function watch(v) {
  const box = el("div", { class: "watch" }, miniPlayer(mediaUrl(v.id, false)));
  dialogBox(v.name, [box], [["Close", null, "ghost"]], (done, dlg) => dlg.classList.add("wide"));
}
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  if (s < 86400 * 30) return Math.round(s / 86400) + " days ago";
  return new Date(t * 1000).toLocaleDateString();
}

// ------------------------------------------------------------------ the card menu
function menuItems(p) {
  if (H.phase === "final") {
    return [["Watch", "play", () => watch(p)],
      ["Save a copy", "download", () => { location.href = "/download/" + p.job + "?t=" + encodeURIComponent(TOKEN); }],
      p.project_id ? ["Open its project", "layers", () => { location.href = "/edit?p=" + p.project_id; }] : null,
      ["Edit it again", "edit", () => openInEditor(p.id)],
      ["Remove", "trash", async () => {
        if (!(await askYes("Remove '" + p.name + "'? The file is deleted from the app (copies you saved are kept).", "Remove", true))) return;
        await api("/api/jobs/" + p.job, { method: "DELETE" }).catch((e) => toast(errText(e), true)); loadAll();
      }]].filter(Boolean);
  }
  const base = H.phase === "clips" ? "/api/clipsets/" : "/api/projects/";
  if (H.trash) return [["Restore", "restore", () => act(base, p, "restore")], ["Delete forever", "trash", () => forget(base, p)]];
  if (H.phase === "clips") {
    return [["Open", "scissors", () => { location.href = "/clips?c=" + p.id; }], ["Rename", "edit", () => rename(base, p)],
      ["Move to trash", "trash", () => act(base, p, "trash")]];
  }
  return [["Open", "layers", () => { if (p.missing) relink(p); else location.href = "/edit?p=" + p.id; }], ["Rename", "edit", () => rename(base, p)],
    ["Duplicate", "copy", () => act(base, p, "duplicate")], ["Move to trash", "trash", () => act(base, p, "trash")]];
}
function menu(p, anchor) {
  closeMenu();
  const m = el("div", { class: "menu pmenu", role: "menu" });
  for (const [label, ic, fn] of menuItems(p)) m.append(el("div", { role: "menuitem", onclick: (e) => { e.stopPropagation(); closeMenu(); fn(); } }, icon(ic, "s"), el("span", { text: label })));
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.min(window.innerWidth - 190, r.right - 180) + "px"; m.style.top = (r.bottom + 4) + "px";
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  function outside(e) { if (!m.contains(e.target)) closeMenu(); }
  H.menu = { m, outside };
}
function closeMenu() { if (H.menu) { H.menu.m.remove(); document.removeEventListener("mousedown", H.menu.outside, true); H.menu = null; } }
async function act(base, p, what) {
  try {
    await api(base + p.id + "/" + what, { method: "POST", json: {} });
    if (what === "trash") toast("Moved '" + p.name + "' to the trash. Open Trash to bring it back.");
    await loadAll();
  } catch (e) { toast(errText(e), true); }
}
async function rename(base, p) {
  const name = await askText("Rename project", p.name);
  if (!name || name === p.name) return;
  try { await api(base + p.id + "/rename", { method: "POST", json: { name } }); await loadAll(); } catch (e) { toast(errText(e), true); }
}
async function forget(base, p) {
  const extra = H.phase === "clips" ? " Clips you made stay in Exports." : " Exported videos are kept.";
  if (!(await askYes("Delete '" + p.name + "' for good? Your video file itself is not touched." + extra, "Delete", true))) return;
  await api(base + p.id, { method: "DELETE" }).catch((e) => toast(errText(e), true));
  loadAll();
}
/** "Find it again": choose the moved video; the project keeps all its pieces and steps. */
async function relink(p) {
  try {
    let r = await api("/api/projects/" + p.id + "/relink", { method: "POST", json: {} });
    if (r.cancelled) return;
    if (r.no_dialog) {
      const d = await chooseFile("video"); if (!d) return;
      r = await api("/api/projects/" + p.id + "/relink", { method: "POST", json: { file: d.id } });
    }
    if (r.note) toast(r.note, true); else toast("Found. '" + p.name + "' uses " + (r.project.src.path || r.project.src.name) + " now.");
    await loadAll();
  } catch (e) { toast(errText(e), true); }
}

// ------------------------------------------------------------------ create
async function createProject(dropped) {
  const bar = $("createProg");
  const prog = (k) => { bar.hidden = false; bar.firstChild.style.width = Math.round(k * 100) + "%"; };
  try {
    const d = dropped ? await uploadFile(dropped, prog) : await chooseFile("video", prog);
    if (!d) return;
    if (!d.has_video && !d.has_audio) { toast("That file has no picture or sound to edit.", true); return; }
    if (H.phase === "clips") {
      const r = await api("/api/clipsets", { method: "POST", json: { source: d.id } });
      location.href = "/clips?c=" + r.clipset.id;
    } else await openInEditor(d.id, 2);
  } catch (e) { toast(errText(e), true); }
  finally { bar.hidden = true; }
}
/** Edit a clip made in phase 1. */
async function fromClip() {
  let clips;
  try { clips = (await api("/api/clips/made")).clips; } catch (e) { toast(errText(e), true); return; }
  const list = el("div", { class: "list pick-list" });
  if (!clips.length) list.append(el("p", { class: "hint", text: "No made clips yet. Open a clip project and press Make clips." }));
  for (const c of clips) {
    list.append(el("div", { class: "item", onclick: () => list.dispatchEvent(new CustomEvent("pick", { detail: c.file })) },
      el("img", { class: "thumb", alt: "", src: thumbUrl(c.file, 0.5), loading: "lazy" }),
      el("div", { class: "t" }, el("b", { text: c.name }), el("small", { class: "num", text: fmtTime(c.length, 1) + " · " + c.clipset_name }))));
  }
  const file = await dialogBox("Edit a made clip", [list], [["Cancel", null, "ghost"]], (done) => list.addEventListener("pick", (e) => done(e.detail)));
  if (file) openInEditor(file, 2);
}

// ------------------------------------------------------------------ storage
async function storagePanel() {
  if ($("storageDrawer")) return;
  const close = () => { scrim.remove(); drawer.remove(); };
  const scrim = el("div", { class: "scrim", onclick: close });
  const body = el("div", { class: "body" });
  const drawer = el("aside", { class: "drawer", id: "storageDrawer", "aria-label": "Storage" },
    el("div", { class: "side-h" }, el("h2", { text: "Storage" }), btn("", { icon: "x", cls: "ghost", title: "Close", onclick: close })), body);
  document.body.append(scrim, drawer);
  const fill = (st) => {
    body.replaceChildren();
    const z = st.sizes;
    body.append(el("div", { class: "box" },
      el("div", { class: "stat" }, el("span", { class: "lbl", text: "Used by the app" }), el("b", { class: "num", text: fmtSize(st.total) })),
      el("div", { class: "stat" }, el("span", { class: "lbl", text: "Free on this disk" }), el("span", { class: "num", text: fmtSize(st.free) }))),
      el("p", { class: "hint", text: st.linked + " video" + (st.linked === 1 ? " is" : "s are") + " used where " + (st.linked === 1 ? "it is" : "they are") + " (linked, no copy)." }));
    const row = (title, hint, bytes, what, sure) => body.append(el("div", { class: "store-row" },
      el("div", { class: "t" }, el("b", { text: title }), el("small", { text: hint })), el("span", { class: "num", text: fmtSize(bytes) }),
      what ? btn("Free up", { cls: "sm", onclick: async () => {
        if (sure && !(await askYes(sure, "Free up", true))) return;
        try { const r = await api("/api/storage/clean", { method: "POST", json: { what } }); toast("Freed. " + r.removed + " removed."); fill(r.storage); }
        catch (e) { toast(errText(e), true); }
      } }) : btn("Open", { cls: "sm", onclick: () => { close(); Exports.show(); } })));
    row("Light copies", "Small copies for smooth playing. Made again when needed.", z.previews, "previews");
    row("Pictures", "Small pictures for timelines and cards. Made again when needed.", z.thumbs, "thumbs");
    row("Sent-in copies", "Videos added by dragging, that no project uses (" + fmtSize(st.loose_uploads) + "). All copies: " + fmtSize(z.uploads) + ".", st.loose_uploads, "uploads",
      "Delete the copies of videos you added by dragging, that no project uses? Your own files are not touched.");
    row("Sound layers", "Voices, music and effects split from videos. Made again when needed.", z.stems || 0, "stems");
    row("Work folders", "Left over from making videos.", z.work, "work");
    row("Exports", "Your made clips and finished videos. Remove them one by one in Exports.", z.exports, "");
    body.append(el("p", { class: "hint", text: "Tip: after all clips of a movie are made, open the clip project and press Free up the movie. Everything is in " + st.home }));
  };
  try { fill(await api("/api/storage")); } catch (e) { body.append(el("p", { class: "hint", text: errText(e) })); }
}

// ------------------------------------------------------------------ start
(function init() {
  const nav = $("homeNav");
  for (const [href, name, ic] of PAGES) nav.append(el("a", { href, class: "home-link" + (href === "/" ? " on" : "") }, icon(ic), el("span", { text: name })));
  $("homeTop").append(el("span", { class: "grow" }), Update.pill(), exportsButton());
  Exports.start();
  Exports.onFiles = () => loadAll();
  paintIcons();
  const create = $("create");
  create.addEventListener("click", () => createProject());
  create.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); createProject(); } });
  dropTarget(create, (f) => createProject(f));
  $("fromClip").addEventListener("click", fromClip);
  $("search").addEventListener("input", () => { H.q = $("search").value.trim(); render(); });
  $("viewToggle").append(segmented([["grid", "Grid"], ["list", "List"]], H.view, (v) => { H.view = v; try { localStorage.setItem("ef.home.view", v); } catch (e) { /* off */ } render(); }));
  $("trashBtn").addEventListener("click", () => { H.trash = !H.trash; render(); });
  $("emptyTrash").addEventListener("click", async () => {
    if (!(await askYes("Delete everything in this trash for good? Video files themselves are not touched.", "Empty trash", true))) return;
    if (H.phase === "clips") {
      for (const p of H.clipsets.filter((x) => x.trashed)) await api("/api/clipsets/" + p.id, { method: "DELETE" }).catch(() => {});
    } else await api("/api/trash/empty", { method: "POST", json: {} }).catch((e) => toast(errText(e), true));
    loadAll();
  });
  $("storageBtn").addEventListener("click", storagePanel);
  const params = new URLSearchParams(location.search);
  const msg = params.get("msg"), ph = params.get("phase");
  if (PHASES.some((p) => p[0] === ph)) H.phase = ph;
  if (msg) toast(msg, true);
  if (msg || ph) history.replaceState(null, "", "/");
  render();
  loadAll();
})();
