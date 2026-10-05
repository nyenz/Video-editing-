"use strict";
// Combine page: put two videos together in one of five ways.
function mode() { return document.querySelector('input[name="mode"]:checked').value; }
function showOptions() {
  const m = mode();
  for (const lab of document.querySelectorAll("[data-for]")) lab.hidden = !lab.dataset.for.split(" ").includes(m);
}
async function loadFiles(pickB) {
  const { files } = await api("/api/files");
  const vids = files.filter((f) => !f.audio && !f.subtitles);
  for (const id of ["fileA", "fileB"]) {
    const sel = $(id), cur = sel.value;
    sel.replaceChildren();
    if (!vids.length) sel.append(el("option", { value: "", text: "(add a video first)" }));
    for (const f of vids) sel.append(el("option", { value: f.id, text: f.name + "  -  " + fmtSize(f.size) + "  (" + f.where + ")" }));
    if (cur && vids.some((f) => f.id === cur)) sel.value = cur;
  }
  if (pickB) $("fileB").value = pickB;
  else if (vids.length > 1 && $("fileA").value === $("fileB").value) $("fileB").value = vids[1].id;
}
$("swapBtn").addEventListener("click", () => { const a = $("fileA").value; $("fileA").value = $("fileB").value; $("fileB").value = a; });
$("addFile").addEventListener("change", (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (f) uploadFile(f, $("addBar"), "addStatus", (data) => loadFiles(data.id));
});
for (const r of document.querySelectorAll('input[name="mode"]')) r.addEventListener("change", showOptions);
$("goBtn").addEventListener("click", async () => {
  const a = $("fileA").value, b = $("fileB").value;
  if (!a || !b) { note("goNote", "Choose two videos first."); return; }
  $("goBtn").disabled = true; note("goNote", "Starting ...");
  const options = { mode: mode(), transition: $("oTrans").value, transition_s: parseFloat($("oTransS").value), length: $("oLength").value,
    corner: $("oCorner").value, size: parseFloat($("oSize").value), opacity: parseFloat($("oOpacity").value), sound: $("oSound").value, quality: $("oQuality").value };
  try { await api("/api/combine", { method: "POST", json: { a, b, options } }); note("goNote", "Started. Watch the progress in step 3 below."); }
  catch (e) { note("goNote", errText(e)); }
  finally { $("goBtn").disabled = false; }
});
(async function init() {
  showOptions();
  try { await loadFiles(); } catch (e) { note("addStatus", errText(e)); }
  startJobList("jobs", () => loadFiles());
})();
