"use strict";
// Home: one big "Create project", the tools, and the projects (newest opened first). A quiet start screen.
const H = { projects: [], trash: false, view: "grid", q: "" };
try { H.view = localStorage.getItem("ef.home.view") === "list" ? "list" : "grid"; } catch (e) { /* storage may be off */ }

// ------------------------------------------------------------------ projects
async function loadProjects() {
  try { H.projects = (await api("/api/projects")).projects; } catch (e) { toast(errText(e), true); return; }
  render();
}
function shown() {
  const q = H.q.toLowerCase();
  return H.projects.filter((p) => !!p.trashed === H.trash && (!q || p.name.toLowerCase().includes(q) || p.source_name.toLowerCase().includes(q)));
}
function render() {
  const list = shown(), grid = $("grid"), trashN = H.projects.filter((p) => p.trashed).length;
  $("projTitle").textContent = H.trash ? "Trash" : "Projects";
  $("projCount").textContent = list.length ? String(list.length) : "";
  $("trashCount").textContent = String(trashN); $("trashCount").hidden = !trashN || H.trash;
  $("trashBtn").classList.toggle("on", H.trash);
  $("trashLabel").textContent = H.trash ? "Back to projects" : "Trash";
  $("emptyTrash").hidden = !(H.trash && trashN);
  grid.className = "proj-grid" + (H.view === "list" ? " as-list" : "");
  grid.replaceChildren();
  if (!list.length) {
    grid.append(el("div", { class: "proj-empty" }, icon(H.trash ? "trash" : "film", "l"),
      el("b", { text: H.q ? "Nothing matches '" + H.q + "'." : H.trash ? "The trash is empty." : "No projects yet." }),
      el("span", { class: "hint", text: H.trash || H.q ? "" : "Press Create project and choose a video." })));
    return;
  }
  for (const p of list) grid.append(card(p));
}
function card(p) {
  const meta = fmtShort(p.size) + " | " + fmtTime(p.duration, 0);
  const pic = el("div", { class: "pthumb" + (p.missing ? " missing" : "") });
  if (p.missing) {
    pic.append(el("span", { class: "miss-t", text: "Video not found" }),
      p.trashed ? null : btn("Find it again", { icon: "link", cls: "sm", onclick: (e) => { e.stopPropagation(); relink(p); } }));
  } else if (p.has_video) {
    const img = el("img", { alt: "", loading: "lazy", decoding: "async", src: thumbUrl(p.source, p.thumb_at, true), draggable: "false" });
    img.addEventListener("error", () => img.remove());
    pic.append(img);
  } else pic.append(icon("music", "l"));
  if (p.steps) pic.append(el("span", { class: "chip-tag", text: (p.steps + 1) + " steps" }));
  const more = btn("", { icon: "more", cls: "ghost more", title: "More", onclick: (e) => { e.stopPropagation(); menu(p, more); } });
  pic.append(more);
  const edited = "Edited: " + fmtTime(p.length, 0) + " · " + p.pieces + " pieces" + (p.where ? "\n" + (p.linked ? "Used where it is: " : "Copy: ") + p.where : "");
  const node = el("div", { class: "pcard" + (p.missing ? " is-missing" : ""), tabindex: "0", role: "button", title: edited, "aria-label": p.name },
    pic, el("div", { class: "pinfo" }, el("b", { class: "ellip", text: p.name }), el("small", { class: "num", text: meta }),
      H.view === "list" ? el("small", { class: "num when", text: "Edited " + fmtTime(p.length, 0) + " · opened " + ago(p.opened) }) : null));
  const open = () => { if (H.trash) return; if (p.missing) relink(p); else location.href = "/edit?p=" + p.id; };
  node.addEventListener("click", open);
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  return node;
}
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  if (s < 86400 * 30) return Math.round(s / 86400) + " days ago";
  return new Date(t * 1000).toLocaleDateString();
}
/** The card's small menu (custom, like the dropdowns). */
function menu(p, anchor) {
  closeMenu();
  const items = H.trash
    ? [["Restore", "restore", () => act(p, "restore")], ["Delete forever", "trash", () => forget(p)]]
    : [["Open", "layers", () => { if (p.missing) relink(p); else location.href = "/edit?p=" + p.id; }], ["Rename", "edit", () => rename(p)],
       ["Duplicate", "copy", () => act(p, "duplicate")], ["Move to trash", "trash", () => act(p, "trash")]];
  const m = el("div", { class: "menu pmenu", role: "menu" });
  for (const [label, ic, fn] of items) m.append(el("div", { role: "menuitem", onclick: (e) => { e.stopPropagation(); closeMenu(); fn(); } }, icon(ic, "s"), el("span", { text: label })));
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.min(window.innerWidth - 190, r.right - 180) + "px"; m.style.top = (r.bottom + 4) + "px";
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  function outside(e) { if (!m.contains(e.target)) closeMenu(); }
  H.menu = { m, outside };
}
function closeMenu() { if (H.menu) { H.menu.m.remove(); document.removeEventListener("mousedown", H.menu.outside, true); H.menu = null; } }
async function act(p, what) {
  try {
    await api("/api/projects/" + p.id + "/" + what, { method: "POST", json: {} });
    if (what === "trash") toast("Moved '" + p.name + "' to the trash. Open Trash to bring it back.");
    await loadProjects();
  } catch (e) { toast(errText(e), true); }
}
async function rename(p) {
  const name = await askText("Rename project", p.name);
  if (!name || name === p.name) return;
  try { await api("/api/projects/" + p.id + "/rename", { method: "POST", json: { name } }); await loadProjects(); } catch (e) { toast(errText(e), true); }
}
async function forget(p) {
  if (!(await askYes("Delete '" + p.name + "' for good? Your video file itself is not touched, and exported videos are kept.", "Delete", true))) return;
  await api("/api/projects/" + p.id, { method: "DELETE" }).catch((e) => toast(errText(e), true));
  loadProjects();
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
    await loadProjects();
  } catch (e) { toast(errText(e), true); }
}

// ------------------------------------------------------------------ create project
async function createProject(dropped) {
  const bar = $("createProg");
  const prog = (k) => { bar.hidden = false; bar.firstChild.style.width = Math.round(k * 100) + "%"; };
  try {
    const d = dropped ? await uploadFile(dropped, prog) : await chooseFile("video", prog);
    if (!d) return;
    if (!d.has_video && !d.has_audio) { toast("That file has no picture or sound to edit.", true); return; }
    await openInEditor(d.id, 2);
  } catch (e) { toast(errText(e), true); }
  finally { bar.hidden = true; }
}
async function openEditor() {
  const last = H.projects.find((p) => !p.trashed && !p.missing);
  if (last) location.href = "/edit?p=" + last.id; else createProject();
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
    row("Work folders", "Left over from making videos.", z.work, "work");
    row("Exports", "Your finished videos. Remove them one by one in Exports.", z.exports, "");
    body.append(el("p", { class: "hint", text: "Light copies and pictures not used for a month are removed by themselves. Everything is in " + st.home }));
  };
  try { fill(await api("/api/storage")); } catch (e) { body.append(el("p", { class: "hint", text: errText(e) })); }
}

// ------------------------------------------------------------------ start
(function init() {
  const nav = $("homeNav");
  for (const [href, name, ic] of PAGES) nav.append(el("a", { href, class: "home-link" + (href === "/" ? " on" : "") }, icon(ic), el("span", { text: name })));
  $("homeTop").append(el("span", { class: "grow" }), Update.pill(), exportsButton());
  Exports.start();
  const tools = [["/clips", "Clips", "scissors", "Mark parts of a long video and make clips."],
    ["/edit", "Editor", "layers", "Cut into pieces, patterns and steps."],
    ["/combine", "Combine", "combine", "Put two videos together."]];
  for (const [href, name, ic, line] of tools) $("tools").append(el("a", { class: "tool", href, onclick: href === "/edit" ? (e) => { e.preventDefault(); openEditor(); } : null },
    el("span", { class: "tool-ic" }, icon(ic)), el("span", { class: "t" }, el("b", { text: name }), el("small", { text: line }))));
  paintIcons();
  const create = $("create");
  create.addEventListener("click", () => createProject());
  create.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); createProject(); } });
  dropTarget(create, (f) => createProject(f));
  $("search").addEventListener("input", () => { H.q = $("search").value.trim(); render(); });
  $("viewToggle").append(segmented([["grid", "Grid"], ["list", "List"]], H.view, (v) => { H.view = v; try { localStorage.setItem("ef.home.view", v); } catch (e) { /* off */ } render(); }));
  $("trashBtn").addEventListener("click", () => { H.trash = !H.trash; render(); });
  $("emptyTrash").addEventListener("click", async () => {
    if (!(await askYes("Delete everything in the trash for good? Video files themselves are not touched.", "Empty trash", true))) return;
    await api("/api/trash/empty", { method: "POST", json: {} }).catch((e) => toast(errText(e), true));
    loadProjects();
  });
  $("storageBtn").addEventListener("click", storagePanel);
  const msg = new URLSearchParams(location.search).get("msg");
  if (msg) { toast(msg, true); history.replaceState(null, "", "/"); }
  loadProjects();
})();
