"use strict";
// Shared helpers for every EditForge page: icons, the top bar, custom controls, uploads, exports.
// Everything shown is added with textContent or built elements, never as HTML from data, so nothing typed can run as code.
const TOKEN = document.querySelector('meta[name="ef-token"]').content;
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ icons (24 x 24, drawn with lines)
const ICONS = {
  film: "M4 3h16v18H4zM8 3v18M16 3v18M4 8h4M4 13h4M4 18h4M16 8h4M16 13h4M16 18h4",
  scissors: "M6 6m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M6 18m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M8.1 8.1 20 20M8.1 15.9 20 4",
  pattern: "M4 5h4v4H4zM10 5h4v4h-4zM16 5h4v4h-4zM4 15h4v4H4zM10 15h4v4h-4zM16 15h4v4h-4z",
  music: "M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0a3 3 0 0 1 6 0M21 16a3 3 0 1 1-6 0a3 3 0 0 1 6 0",
  type: "M4 7V4h16v3M9 20h6M12 4v16",
  crop: "M6 2v14a2 2 0 0 0 2 2h14M18 22V8a2 2 0 0 0-2-2H2",
  combine: "M3 4h7v16H3zM14 4h7v16h-7z",
  code: "M16 18l6-6-6-6M8 6l-6 6 6 6",
  play: "M7 4v16l13-8z",
  pause: "M7 4h3v16H7zM14 4h3v16h-3z",
  swap: "M4 8h15l-4-4M20 16H5l4 4",
  back: "M11 19l-9-7 9-7zM22 19l-9-7 9-7z",
  fwd: "M13 19l9-7-9-7zM2 19l9-7-9-7z",
  stepb: "M19 20 9 12l10-8zM5 19V5",
  stepf: "M5 4l10 8-10 8zM19 5v14",
  undo: "M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
  redo: "M15 14l5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  split: "M12 2v20M5 7l-3 5 3 5M19 7l3 5-3 5",
  zin: "M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M21 21l-4.3-4.3M11 8v6M8 11h6",
  zout: "M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M21 21l-4.3-4.3M8 11h6",
  fit: "M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3",
  plus: "M12 5v14M5 12h14",
  upload: "M12 16V4M7 9l5-5 5 5M4 17v3h16v-3",
  download: "M12 4v12M7 11l5 5 5-5M4 17v3h16v-3",
  folder: "M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z",
  check: "M5 12l5 5L20 7",
  x: "M18 6 6 18M6 6l12 12",
  chev: "M6 9l6 6 6-6",
  chevr: "M9 6l6 6-6 6",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0",
  fliph: "M12 3v18M8 7 3 12l5 5zM16 7l5 5-5 5z",
  flipv: "M3 12h18M7 8l5-5 5 5zM7 16l5 5 5-5z",
  mirror: "M12 3v18M4 6h5v12H4zM15 6h5v12h-5z",
  drop: "M12 3s7 7.5 7 12a7 7 0 0 1-14 0c0-4.5 7-12 7-12z",
  gauge: "M12 14l4-4M3.3 17a10 10 0 1 1 17.4 0",
  vol: "M11 5 6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14",
  mute: "M11 5 6 9H2v6h4l5 4zM22 9l-6 6M16 9l6 6",
  reverse: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5",
  target: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M12 12h.01",
  face: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01",
  sparkles: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  info: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 16v-4M12 8h.01",
  warn: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01",
  clock: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 7v5l3 3",
  layers: "M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  bookmark: "M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z",
  export: "M14 3h7v7M10 14 21 3M21 14v7H3V3h7",
  zoom: "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7",
  palette: "M12 22a10 10 0 1 1 10-10c0 2.8-2.2 3-4 3h-1.5a1.5 1.5 0 0 0-1 2.6A1.5 1.5 0 0 1 14 20.4 2 2 0 0 1 12 22zM7.5 10.5h.01M12 7.5h.01M16.5 10.5h.01",
  ratio: "M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z",
  mark: "M12 2v20M6 6l6-4 6 4",
  inp: "M4 4v16M8 12h12M14 6l6 6-6 6",
  outp: "M20 4v16M16 12H4M10 6l-6 6 6 6",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  up: "M12 19V5M5 12l7-7 7 7",
  down: "M12 5v14M19 12l-7 7-7-7",
  restore: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.7 2.7L3 8M3 3v5h5",
  magnet: "M6 3v8a6 6 0 0 0 12 0V3M6 7h4M14 7h4",
};
/** An <svg> icon element. */
function icon(name, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "ic" + (cls ? " " + cls : ""));
  s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", ICONS[name] || ICONS.info);
  s.append(p);
  return s;
}
/** Replace every <i data-ic="name"> in the page with its icon. */
function paintIcons(root) {
  for (const n of (root || document).querySelectorAll("i[data-ic]")) n.replaceWith(icon(n.dataset.ic, n.className));
}

// ------------------------------------------------------------------ small helpers
function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids) if (kid !== null && kid !== undefined && kid !== false) n.append(kid);
  return n;
}
/** A button with an optional icon. */
function btn(label, opts) {
  const o = opts || {};
  const b = el("button", { class: "btn" + (o.cls ? " " + o.cls : "") + (label ? "" : " icon"), type: "button", title: o.title || label || "", onclick: o.onclick, "aria-label": label ? null : o.title });
  if (o.icon) b.append(icon(o.icon, "s"));
  if (label) b.append(el("span", { text: label }));
  return b;
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
function errText(e) { return e.message + (e.fix ? " " + e.fix : ""); }
function mediaUrl(id, preview) { return "/media?id=" + encodeURIComponent(id) + (preview ? "&preview=1" : "") + "&t=" + encodeURIComponent(TOKEN); }
function thumbUrl(id, at) { return "/thumb?id=" + encodeURIComponent(id) + "&at=" + Math.max(0, at).toFixed(2) + "&t=" + encodeURIComponent(TOKEN); }
function fmtSize(b) { return b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(1) + " MB"; }
/** 3725.5 -> "1:02:05.5" (tenths) ; digits=0 -> "1:02:05"; digits=3 -> milliseconds. */
function fmtTime(s, digits) {
  if (s === null || s === undefined || !isFinite(s)) return "-";
  const d = digits === undefined ? 1 : digits, k = Math.pow(10, d);
  const tot = Math.round(Math.max(0, s) * k);
  const whole = Math.floor(tot / k), frac = tot % k;
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), sec = whole % 60;
  let out = (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(sec).padStart(2, "0");
  if (d) out += "." + String(frac).padStart(d, "0");
  return out;
}
/** Accepts 75, 75.5, 1:15, 1:02:05.5 and returns seconds, or null. */
function parseTime(text) {
  const t = String(text).trim().replace(",", ".");
  if (!t) return null;
  const parts = t.split(":");
  if (parts.length > 3) return null;
  let total = 0;
  for (const p of parts) { if (!/^\d+(\.\d+)?$/.test(p)) return null; total = total * 60 + parseFloat(p); }
  return total;
}
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

// ------------------------------------------------------------------ toasts
function toast(text, bad) {
  let box = document.querySelector(".toasts");
  if (!box) { box = el("div", { class: "toasts", "aria-live": "polite" }); document.body.append(box); }
  const t = el("div", { class: "toast" + (bad ? " bad" : "") }, icon(bad ? "warn" : "check", "s"), el("span", { text }));
  box.append(t);
  setTimeout(() => t.remove(), bad ? 6500 : 3200);
}

// ------------------------------------------------------------------ top bar (same on every page)
function topBar(active, extra) {
  const pages = [["/", "Clips", "scissors"], ["/edit", "Editor", "layers"], ["/combine", "Combine", "combine"], ["/advanced", "Advanced", "code"]];
  const nav = el("nav", { class: "nav", "aria-label": "Pages" });
  for (const [href, name, ic] of pages) nav.append(el("a", { href, class: active === href ? "on" : "" }, icon(ic, "s"), el("span", { text: name })));
  const bar = el("header", { class: "top" },
    el("a", { class: "brand", href: "/" }, el("img", { src: "/static/logo.svg", alt: "" }), el("span", { text: "EditForge" })),
    nav, el("div", { class: "grow" }));
  for (const x of extra || []) bar.append(x);
  const ex = btn("Exports", { icon: "download", cls: "ghost", onclick: () => Exports.show() });
  ex.append(el("span", { class: "count", id: "exportsCount", hidden: true }));
  bar.append(ex);
  Exports.start();
  document.body.prepend(bar);
  return bar;
}

// ------------------------------------------------------------------ custom dropdown on top of a hidden <select>
const Selects = [];
function enhanceSelect(sel) {
  if (sel.dataset.enhanced) return;
  sel.dataset.enhanced = "1";
  sel.classList.add("native");
  const label = el("span");
  const button = el("button", { type: "button", "aria-haspopup": "listbox" }, label, icon("chev", "s"));
  const wrap = el("div", { class: "sel" + (sel.dataset.wide ? " wide" : "") }, button);
  if (sel.style.width) wrap.style.width = sel.style.width;
  if (sel.id) button.setAttribute("aria-label", sel.getAttribute("aria-label") || sel.id);
  sel.after(wrap);
  const sync = () => {
    const o = sel.options[sel.selectedIndex];
    label.textContent = o ? o.textContent : "";
    button.disabled = sel.disabled;
  };
  let menu = null, hl = -1;
  const close = () => { if (menu) { menu.remove(); menu = null; wrap.classList.remove("open"); document.removeEventListener("mousedown", outside, true); } };
  const outside = (e) => { if (menu && !menu.contains(e.target) && !wrap.contains(e.target)) close(); };
  const choose = (i) => { if (i >= 0 && i < sel.options.length) { sel.selectedIndex = i; sel.dispatchEvent(new Event("change", { bubbles: true })); sync(); } close(); button.focus(); };
  const paint = () => [...menu.children].forEach((d, i) => d.classList.toggle("hl", i === hl));
  const open = () => {
    if (menu) { close(); return; }
    menu = el("div", { class: "menu", role: "listbox" });
    [...sel.options].forEach((o, i) => {
      const d = el("div", { role: "option", class: i === sel.selectedIndex ? "cur" : "", text: o.textContent });
      d.addEventListener("mousedown", (e) => { e.preventDefault(); choose(i); });
      menu.append(d);
    });
    document.body.append(menu);
    const r = button.getBoundingClientRect();
    menu.style.minWidth = r.width + "px";
    const below = window.innerHeight - r.bottom;
    menu.style.left = Math.min(r.left, window.innerWidth - menu.offsetWidth - 8) + "px";
    if (below < Math.min(320, menu.offsetHeight) + 10 && r.top > below) menu.style.bottom = (window.innerHeight - r.top + 4) + "px"; else menu.style.top = (r.bottom + 4) + "px";
    hl = sel.selectedIndex; paint();
    if (menu.children[hl]) menu.children[hl].scrollIntoView({ block: "nearest" });
    wrap.classList.add("open");
    document.addEventListener("mousedown", outside, true);
  };
  button.addEventListener("click", open);
  button.addEventListener("keydown", (e) => {
    if (!menu && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); const i = clamp(sel.selectedIndex + (e.key === "ArrowDown" ? 1 : -1), 0, sel.options.length - 1); choose(i); return; }
    if (!menu) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); hl = Math.min(sel.options.length - 1, hl + 1); paint(); menu.children[hl].scrollIntoView({ block: "nearest" }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); hl = Math.max(0, hl - 1); paint(); menu.children[hl].scrollIntoView({ block: "nearest" }); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(hl); }
  });
  window.addEventListener("blur", close);
  new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true });
  Selects.push({ sel, sync, last: "" });
  sync();
}
function enhanceAll(root) {
  for (const s of (root || document).querySelectorAll("select")) enhanceSelect(s);
  for (const r of (root || document).querySelectorAll("input.rng")) paintRange(r);
}
// a value set from code fires no event, so keep the labels honest
setInterval(() => { for (const s of Selects) { const v = s.sel.value + "|" + s.sel.disabled; if (v !== s.last) { s.last = v; s.sync(); } } }, 250);
function paintRange(r) {
  const f = () => { const p = ((parseFloat(r.value) - parseFloat(r.min || 0)) / ((parseFloat(r.max || 100) - parseFloat(r.min || 0)) || 1)) * 100; r.style.setProperty("--p", p + "%"); };
  if (!r.dataset.painted) { r.dataset.painted = "1"; r.addEventListener("input", f); }
  f();
}
/** A segmented choice; onpick(value). */
function segmented(options, value, onpick) {
  const box = el("div", { class: "seg", role: "radiogroup" });
  const set = (v) => { for (const b of box.children) b.classList.toggle("on", b.dataset.v === String(v)); };
  for (const [v, text] of options) box.append(el("button", { type: "button", "data-v": String(v), role: "radio", text, onclick: () => { set(v); onpick(v); } }));
  set(value); box.set = set;
  return box;
}

// ------------------------------------------------------------------ uploads
/** Send a file with progress. Calls onprog(0..1), resolves with the server's reply. */
function uploadFile(file, onprog) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload?name=" + encodeURIComponent(file.name));
    xhr.setRequestHeader("X-EditForge-Token", TOKEN);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onprog) onprog(e.loaded / e.total); };
    xhr.onerror = () => reject(new Error("The file could not be added. Check that EditForge is still running."));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
      if (xhr.status >= 300) { const err = new Error(data.error || "The file could not be added."); err.fix = data.fix; reject(err); } else resolve(data);
    };
    xhr.send(file);
  });
}
/** Open the file chooser; resolves with the File or null. */
function pickFile(accept) {
  return new Promise((resolve) => {
    const inp = el("input", { type: "file", accept: accept || "" });
    inp.style.display = "none";
    inp.addEventListener("change", () => { resolve(inp.files[0] || null); inp.remove(); });
    document.body.append(inp); inp.click();
  });
}
/** Make an element accept dropped files. */
function dropTarget(node, onfile) {
  for (const ev of ["dragenter", "dragover"]) node.addEventListener(ev, (e) => { e.preventDefault(); node.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) node.addEventListener(ev, (e) => { e.preventDefault(); node.classList.remove("over"); });
  node.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) onfile(f); });
}

// ------------------------------------------------------------------ light copy for smooth playback
/**
 * Browsers cannot play every file: an .mkv, or sound in Dolby (AC-3, E-AC-3, DTS), plays without
 * sound or not at all. Then a light 480p copy with normal sound is made, only for watching.
 * Finished videos are always made from the original. onstate({state, progress, error}).
 */
async function ensurePreview(fileId, info, onstate, always) {
  if (!info.has_video) return false;
  if (info.preview && info.preview.state === "ready") { onstate({ state: "ready", progress: 1 }); return true; }
  if (!always && info.plays_in_browser) return false;
  try { await api("/api/preview", { method: "POST", json: { input: fileId } }); }
  catch (e) { onstate({ state: "failed", error: errText(e) }); return false; }
  return new Promise((resolve) => {
    const tick = async () => {
      let st;
      try { st = await api("/api/preview?id=" + encodeURIComponent(fileId)); } catch (e) { setTimeout(tick, 1500); return; }
      onstate(st);
      if (st.state === "ready") resolve(true); else if (st.state === "failed") resolve(false); else setTimeout(tick, 1000);
    };
    tick();
  });
}

// ------------------------------------------------------------------ exports drawer (finished videos and progress)
const Exports = {
  visible: false, openIds: new Set(), sig: "", timer: null, onFiles: null,
  async refresh(force) {
    let data;
    try { data = await api("/api/jobs"); } catch (e) { return; }
    const running = data.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    const pill = $("exportsCount");
    if (pill) { pill.textContent = running ? String(running) : ""; pill.hidden = !running; }
    if (!this.visible) return;
    const sig = JSON.stringify(data.jobs.map((j) => [j.id, j.status, Math.round((j.progress || 0) * 100), j.message])) + [...this.openIds].join();
    if (!force && sig === this.sig) return;
    this.sig = sig;
    const body = $("exportsBody"); body.replaceChildren();
    if (!data.jobs.length) body.append(el("p", { class: "hint", text: "Nothing yet. Videos you make appear here." }));
    for (const j of data.jobs) body.append(this.card(j));
  },
  card(j) {
    const pct = Math.round((j.progress || 0) * 100);
    const name = (j.output_path || "").split(/[\\/]/).pop() || "Waiting to start";
    const label = { queued: "Waiting", running: pct + "%", done: "Done", failed: "Failed", cancelled: "Stopped", interrupted: "Stopped" }[j.status] || j.status;
    const node = el("div", { class: "job" }, el("div", { class: "hd" }, icon(j.preview ? "eye" : "film", "s"), el("b", { text: name, title: name }), el("span", { class: "st " + j.status, text: label })));
    if (j.status === "running" || j.status === "queued") {
      const bar = el("div", { class: "prog" }, el("i")); bar.firstChild.style.width = pct + "%"; node.append(bar);
      node.append(el("div", { class: "facts", text: (j.message || "") + (j.eta ? " · about " + fmtTime(j.eta, 0) + " left" : "") }));
    }
    const v = j.result && j.result.verify;
    if (j.status === "done" && j.result) node.append(el("div", { class: "facts", text: fmtTime(j.result.duration) + " long" + (v && v.width ? " · " + v.width + "×" + v.height : "") + (v && v.size_bytes ? " · " + fmtSize(v.size_bytes) : "") + (j.preview ? " · quick preview" : "") }));
    for (const w of j.warnings || []) node.append(el("div", { class: "facts warn", text: w }));
    if (j.error) node.append(el("div", { class: "facts err", text: j.error + (j.error_fix ? " " + j.error_fix : "") }));
    const acts = el("div", { class: "btns" });
    if (j.status === "running" || j.status === "queued") acts.append(btn("Stop", { icon: "x", onclick: async () => { await api("/api/jobs/" + j.id + "/cancel", { method: "POST", json: {} }); this.refresh(true); } }));
    if (["interrupted", "failed", "cancelled"].includes(j.status)) acts.append(btn("Try again", { icon: "restore", onclick: async () => { await api("/api/jobs/" + j.id + "/resume", { method: "POST", json: {} }); this.refresh(true); } }));
    if (j.status === "done" && j.file_id) {
      acts.append(btn(this.openIds.has(j.id) ? "Hide" : "Watch", { icon: "play", cls: "primary", onclick: () => { if (this.openIds.has(j.id)) this.openIds.delete(j.id); else this.openIds.add(j.id); this.refresh(true); } }));
      acts.append(el("a", { class: "btn", href: "/download/" + j.id + "?t=" + encodeURIComponent(TOKEN) }, icon("download", "s"), el("span", { text: "Save" })));
      if (!j.preview) {
        acts.append(btn("Edit", { icon: "layers", title: "Open this video in the Editor", onclick: () => openInEditor(j.file_id) }));
        acts.append(el("a", { class: "btn ghost", href: "/?open=" + encodeURIComponent(j.file_id), title: "Cut clips from this video" }, icon("scissors", "s")));
      }
    }
    if (["done", "failed", "cancelled", "interrupted"].includes(j.status)) acts.append(btn("", { icon: "trash", cls: "ghost danger", title: "Remove", onclick: async () => {
      if (!window.confirm("Remove '" + name + "'? The file is deleted from EditForge (copies you saved are kept).")) return;
      this.openIds.delete(j.id); await api("/api/jobs/" + j.id, { method: "DELETE" }); if (this.onFiles) this.onFiles(); this.refresh(true);
    } }));
    node.append(acts);
    if (j.status === "done" && j.file_id && this.openIds.has(j.id)) node.append(miniPlayer(mediaUrl(j.file_id, false)));
    return node;
  },
  show() {
    if ($("exportsDrawer")) return;
    this.visible = true; this.sig = "";
    const close = () => { this.visible = false; scrim.remove(); drawer.remove(); };
    const scrim = el("div", { class: "scrim", onclick: close });
    const drawer = el("aside", { class: "drawer", id: "exportsDrawer", "aria-label": "Exports" },
      el("div", { class: "side-h" }, el("h2", { text: "Exports" }), btn("", { icon: "x", cls: "ghost", title: "Close", onclick: close })),
      el("div", { class: "body", id: "exportsBody" }));
    document.body.append(scrim, drawer);
    document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); } });
    this.refresh(true);
  },
  start() { if (!this.timer) { this.refresh(); this.timer = setInterval(() => this.refresh(), 1000); } },
};
/** A small custom video player: picture, play, a seek bar, time, sound and full screen. */
function miniPlayer(url) {
  const v = el("video", { preload: "metadata", src: url, playsinline: "" });
  const playB = btn("", { icon: "play", cls: "icon ghost", title: "Play" });
  const bar = el("input", { type: "range", class: "rng", min: "0", max: "1000", value: "0", "aria-label": "Position" });
  const time = el("span", { class: "num note", text: "0:00 / 0:00" });
  const muteB = btn("", { icon: "vol", cls: "icon ghost", title: "Sound on/off" });
  const fullB = btn("", { icon: "fit", cls: "icon ghost", title: "Full screen" });
  const box = el("div", { class: "mini" }, v, el("div", { class: "mini-bar" }, playB, bar, time, muteB, fullB));
  paintRange(bar);
  const show = () => {
    const d = v.duration || 0;
    time.textContent = fmtTime(v.currentTime, 0) + " / " + fmtTime(d, 0);
    if (!bar.matches(":active")) { bar.value = String(d ? Math.round(v.currentTime / d * 1000) : 0); paintRange(bar); }
  };
  const toggle = () => { if (v.paused) { const pr = v.play(); if (pr && pr.catch) pr.catch(() => {}); } else v.pause(); };
  playB.addEventListener("click", toggle); v.addEventListener("click", toggle);
  v.addEventListener("play", () => playB.replaceChildren(icon("pause")));
  v.addEventListener("pause", () => playB.replaceChildren(icon("play")));
  v.addEventListener("timeupdate", show); v.addEventListener("loadedmetadata", show);
  bar.addEventListener("input", () => { if (v.duration) v.currentTime = parseFloat(bar.value) / 1000 * v.duration; paintRange(bar); });
  muteB.addEventListener("click", () => { v.muted = !v.muted; muteB.replaceChildren(icon(v.muted ? "mute" : "vol")); });
  fullB.addEventListener("click", () => { if (document.fullscreenElement) document.exitFullscreen(); else if (box.requestFullscreen) box.requestFullscreen(); });
  return box;
}

/** Make a project from a video and open it in the Editor. */
async function openInEditor(fileId, interval) {
  try {
    const r = await api("/api/projects", { method: "POST", json: { source: fileId, interval: interval || 2 } });
    window.location.href = "/edit?p=" + r.project.id;
  } catch (e) { toast(errText(e), true); }
}
