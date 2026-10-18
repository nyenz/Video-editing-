"use strict";
// Combine: put two videos together in one of five ways. Shows the finished length and size before making it.
const MODES = [
  ["sequence", "One after the other", "The first plays, then the second."],
  ["side", "Side by side", "Both play at once, left and right."],
  ["stack", "Top and bottom", "Both play at once, one above the other."],
  ["corner", "Small one in a corner", "The second plays small on top of the first."],
  ["blend", "One over the other", "The second lies over the first, see-through."],
];
const K = { mode: "sequence", info: {} };
const even = (x) => Math.max(2, Math.round(x / 2) * 2);

function modePic(m) {
  // a tiny drawing of each layout
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 120 56"); svg.setAttribute("class", "pic");
  const r = (x, y, w, h, c, o) => { const n = document.createElementNS(ns, "rect"); Object.entries({ x, y, width: w, height: h, rx: 3, fill: c, "fill-opacity": o || 1 }).forEach(([k, v]) => n.setAttribute(k, v)); svg.append(n); };
  const A = "#35d0e6", B = "#ff5a79";
  if (m === "sequence") { r(6, 14, 50, 28, A); r(64, 14, 50, 28, B); }
  if (m === "side") { r(14, 12, 46, 32, A); r(60, 12, 46, 32, B); }
  if (m === "stack") { r(38, 2, 44, 26, A); r(38, 28, 44, 26, B); }
  if (m === "corner") { r(22, 6, 76, 44, A); r(72, 32, 22, 14, B); }
  if (m === "blend") { r(22, 6, 76, 44, A); r(22, 6, 76, 44, B, 0.5); }
  return svg;
}
function renderModes() {
  const box = $("modes"); box.replaceChildren();
  for (const [k, name, text] of MODES) box.append(el("button", { type: "button", class: "mode" + (K.mode === k ? " on" : ""), role: "radio", "aria-checked": String(K.mode === k),
    onclick: () => { K.mode = k; renderModes(); showOptions(); summary(); } }, modePic(k), el("b", { text: name }), el("span", { class: "hint", text })));
}
function showOptions() { for (const n of document.querySelectorAll("[data-for]")) n.hidden = !n.dataset.for.split(" ").includes(K.mode); }

async function loadFiles(pickB) {
  const vids = (await api("/api/files")).files.filter((f) => !f.audio && !f.subtitles);
  for (const id of ["fileA", "fileB"]) {
    const sel = $(id), cur = sel.value;
    sel.replaceChildren();
    if (!vids.length) sel.append(el("option", { value: "", text: "Add a video first" }));
    for (const f of vids) sel.append(el("option", { value: f.id, text: f.name + " · " + fmtSize(f.size) }));
    if (cur && vids.some((f) => f.id === cur)) sel.value = cur;
  }
  if (pickB) $("fileB").value = pickB;
  else if (vids.length > 1 && $("fileA").value === $("fileB").value) $("fileB").value = vids[1].id;
  await facts();
}
async function infoOf(id) {
  if (!id) return null;
  if (!K.info[id]) { try { K.info[id] = await api("/api/info?id=" + encodeURIComponent(id)); } catch (e) { return null; } }
  return K.info[id];
}
async function facts() {
  for (const [sel, box] of [["fileA", "factsA"], ["fileB", "factsB"]]) {
    const id = $(sel).value, i = await infoOf(id), n = $(box);
    n.replaceChildren();
    if (!i) continue;
    if (i.has_video) n.append(el("img", { class: "thumb", alt: "", src: thumbUrl(id, Math.min(1, i.duration / 2)) }));
    n.append(el("div", { class: "t" }, el("b", { class: "num", text: fmtTime(i.duration) }), el("small", { text: i.has_video ? i.width + "×" + i.height : "sound only" })));
  }
  summary();
}
function options() {
  return { mode: K.mode, transition: $("oTrans").value, transition_s: parseFloat($("oTransS").value), length: $("oLength").value,
    corner: $("oCorner").value, size: parseFloat($("oSize").value), opacity: parseFloat($("oOpacity").value), sound: $("oSound").value, quality: $("oQuality").value };
}
/** The same sums the server does, so the length is known before anything is made. */
function summary() {
  const a = K.info[$("fileA").value], b = K.info[$("fileB").value], o = options();
  if (!a || !b) { $("rLen").textContent = "–"; $("rSize").textContent = "–"; return; }
  let len;
  if (o.mode === "sequence") { const cut = o.transition === "none" ? 0 : Math.max(0, Math.min(o.transition_s, a.duration / 2, b.duration / 2)); len = a.duration + b.duration - cut; }
  else if (o.mode === "corner" || o.mode === "blend") len = a.duration;
  else len = o.length === "shortest" ? Math.min(a.duration, b.duration) : Math.max(a.duration, b.duration);
  $("rLen").textContent = fmtTime(len);
  if (!a.has_video || !b.has_video) { $("rSize").textContent = "Both need a picture"; return; }
  const W = even(a.width), H = even(a.height);
  const size = o.mode === "side" ? (W + even(H * b.width / b.height)) + "×" + H : o.mode === "stack" ? W + "×" + (H + even(W * b.height / b.width)) : W + "×" + H;
  $("rSize").textContent = size;
}

(async function init() {
  topBar("/combine");
  paintIcons();
  renderModes(); showOptions();
  $("swapBtn").addEventListener("click", () => { const a = $("fileA").value; $("fileA").value = $("fileB").value; $("fileB").value = a; facts(); });
  for (const id of ["fileA", "fileB"]) $(id).addEventListener("change", facts);
  for (const n of document.querySelectorAll(".opts select")) n.addEventListener("change", summary);
  $("addFile").addEventListener("click", async () => {
    const bar = $("addProg");
    try {
      const d = await chooseFile("video", (k) => { bar.hidden = false; bar.firstChild.style.width = Math.round(k * 100) + "%"; });
      if (!d) return;
      $("addStatus").textContent = d.linked ? "Used where it is: " + d.where : ""; await loadFiles(d.id);
    }
    catch (e) { $("addStatus").textContent = errText(e); }
    finally { bar.hidden = true; }
  });
  $("goBtn").addEventListener("click", async () => {
    const a = $("fileA").value, b = $("fileB").value;
    if (!a || !b) { $("goNote").textContent = "Choose two videos first."; return; }
    $("goBtn").disabled = true; $("goNote").textContent = "Starting…";
    try { await api("/api/combine", { method: "POST", json: { a, b, options: options() } }); $("goNote").textContent = ""; toast("Combining · " + $("rLen").textContent + " long"); Exports.show(); }
    catch (e) { $("goNote").textContent = errText(e); }
    finally { $("goBtn").disabled = false; }
  });
  Exports.onFiles = () => loadFiles().catch(() => {});
  try { await loadFiles(); } catch (e) { $("addStatus").textContent = errText(e); }
  enhanceAll();
})();
