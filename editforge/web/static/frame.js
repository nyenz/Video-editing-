"use strict";
// Frame (Editor): the canvas shape, a background layer, the picture moved / scaled / turned, and a mask with soft edges.
// Like CapCut: below, the same video fills the canvas (blurred, black or white); above, the video itself, and a mask
// can cut it to a strip, a rectangle, a circle or one side, so the blurred layer shows around it.
// The export draws it with FFmpeg (render/filters.py ItemBuilder._frame); here a <canvas> draws the same thing live.
const Frame = (function () {
  const RATIOS = [["original", "Original", null], ["16:9", "16:9", 16 / 9], ["4:3", "4:3", 4 / 3], ["2.35:1", "2.35:1", 2.35], ["2:1", "2:1", 2],
    ["1.85:1", "1.85:1", 1.85], ["9:16", "9:16", 9 / 16], ["3:4", "3:4", 3 / 4], ["1:1", "1:1", 1], ["4:5", "4:5", 4 / 5], ["5.8in", "5.8-inch", 9 / 19.5]];
  const MASKS = [["none", "None"], ["strip", "Strip"], ["vstrip", "Side strip"], ["rect", "Rectangle"], ["circle", "Circle"], ["split", "Split"]];
  const DEF = () => ({ ratio: "original", bg: "blur", blur: 50, scale: 1, x: 0, y: 0, rotate: 0,
    mask: { shape: "none", x: 0.5, y: 0.5, w: 1, h: 0.35, feather: 20, invert: false } });
  const F = { get: null, set: null, video: null, srcAspect: 16 / 9, cv: null, top: null, maskCv: null, maskKey: "", last: "", raf: 0, host: null };

  function full(fr) {
    const d = DEF();
    if (!fr) return d;
    return Object.assign(d, fr, { mask: Object.assign(d.mask, fr.mask || {}) });
  }
  function active(fr) {
    if (!fr) return false;
    const f = full(fr);
    return f.ratio !== "original" || Math.abs(f.scale - 1) > 1e-3 || Math.abs(f.x) > 1e-4 || Math.abs(f.y) > 1e-4 || Math.abs(f.rotate) > 1e-3 || f.mask.shape !== "none";
  }
  function aspect(fr) {
    const r = RATIOS.find((x) => x[0] === full(fr).ratio);
    return r && r[2] ? r[2] : F.srcAspect;
  }

  // ---------------------------------------------------------------- live drawing
  function maskCanvas(m, W, H) {
    const key = JSON.stringify(m) + W + "x" + H;
    if (key === F.maskKey && F.maskCv) return F.maskCv;
    const c = F.maskCv || document.createElement("canvas");
    c.width = W; c.height = H;
    const x = c.getContext("2d");
    x.clearRect(0, 0, W, H);
    const soft = m.feather / 100 * Math.min(W, H) * 0.08;
    x.filter = soft > 0.3 ? "blur(" + soft.toFixed(1) + "px)" : "none";
    x.fillStyle = "#fff";
    const cx = m.x * W, cy = m.y * H, hw = m.w * W / 2, hh = m.h * H / 2;
    const shape = () => {
      x.beginPath();
      if (m.shape === "rect") x.rect(cx - hw, cy - hh, hw * 2, hh * 2);
      else if (m.shape === "circle") x.ellipse(cx, cy, hw, hh, 0, 0, Math.PI * 2);
      else if (m.shape === "split") x.rect(-W, cy, W * 3, H * 2);
      else if (m.shape === "vstrip") x.rect(cx - hw, -H, hw * 2, H * 3);
      else x.rect(-W, cy - hh, W * 3, hh * 2);
      x.fill();
    };
    if (m.invert) { x.fillRect(-W, -H, W * 3, H * 3); x.globalCompositeOperation = "destination-out"; shape(); x.globalCompositeOperation = "source-over"; }
    else shape();
    x.filter = "none";
    F.maskCv = c; F.maskKey = key;
    return c;
  }
  function draw(force) {
    const fr = F.get(), v = F.video();
    if (!F.cv || !active(fr) || !v || !v.videoWidth) return;
    const f = full(fr);
    const box = F.cv.parentElement.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.max(2, Math.round(box.width * dpr)), H = Math.max(2, Math.round(box.height * dpr));
    const sig = [v.currentTime.toFixed(3), W, H, JSON.stringify(f), v.style.filter].join("|");
    if (!force && sig === F.last && v.paused) return;
    F.last = sig;
    if (F.cv.width !== W || F.cv.height !== H) { F.cv.width = W; F.cv.height = H; }
    if (!F.top) F.top = document.createElement("canvas");
    if (F.top.width !== W || F.top.height !== H) { F.top.width = W; F.top.height = H; }
    const ctx = F.cv.getContext("2d"), vw = v.videoWidth, vh = v.videoHeight;
    // the background layer: the video filling the canvas
    ctx.save();
    ctx.clearRect(0, 0, W, H);
    if (f.bg === "blur") {
      const kc = Math.max(W / vw, H / vh), dw = vw * kc, dh = vh * kc;
      const sigma = (1 + f.blur / 100 * Math.min(W, H) * 0.06);
      ctx.filter = "blur(" + sigma.toFixed(1) + "px)";
      ctx.drawImage(v, (W - dw) / 2, (H - dh) / 2, dw, dh);
      ctx.filter = "none";
    } else { ctx.fillStyle = f.bg === "white" ? "#fff" : "#000"; ctx.fillRect(0, 0, W, H); }
    ctx.restore();
    // the picture: fitted, then scaled, moved and turned
    const t = F.top.getContext("2d");
    t.save(); t.clearRect(0, 0, W, H);
    const k = Math.min(W / vw, H / vh) * f.scale;
    t.translate(W / 2 + f.x * W / 2, H / 2 + f.y * H / 2);
    t.rotate(f.rotate * Math.PI / 180);
    t.drawImage(v, -vw * k / 2, -vh * k / 2, vw * k, vh * k);
    t.restore();
    if (f.mask.shape !== "none") {
      t.save(); t.globalCompositeOperation = "destination-in"; t.drawImage(maskCanvas(f.mask, W, H), 0, 0); t.restore();
    }
    ctx.drawImage(F.top, 0, 0);
    F.cv.style.filter = v.style.filter;          // the piece's own look (colour, black and white ...)
  }
  function loop() { try { draw(false); } catch (e) { /* a frame that is not ready yet */ } F.raf = requestAnimationFrame(loop); }
  function show() {
    const on = active(F.get());
    F.host.classList.toggle("framed", on);
    F.cv.hidden = !on;
    if (on) draw(true);
  }

  // ---------------------------------------------------------------- the panel
  function change(fn) { const fr = full(F.get()); fn(fr); F.set(fr); renderPanel(); show(); }
  function slider(label, min, max, step, val, fmt, onv) {
    const r = el("input", { type: "range", class: "rng", min: String(min), max: String(max), step: String(step), value: String(val), "aria-label": label });
    const b = el("b", { class: "num", text: fmt(val) });
    r.addEventListener("input", () => { b.textContent = fmt(+r.value); const fr = full(F.get()); onv(fr, +r.value); F.set(fr, true); show(); });
    r.addEventListener("change", () => { const fr = full(F.get()); onv(fr, +r.value); F.set(fr); });
    const box = el("div", { class: "field" }, el("span", {}, label + " ", b), r);
    paintRange(r);
    return box;
  }
  function renderPanel() {
    const host = $("framePanel");
    if (!host || host.closest("section").hidden) return;
    const f = full(F.get());
    host.replaceChildren();
    // shape of the canvas
    const shapes = el("div", { class: "ratios" });
    for (const [k, name, a] of RATIOS) {
      const ar = a || F.srcAspect;
      const pic = el("i", { class: "ratio-pic" });
      if (ar >= 1) { pic.style.width = "26px"; pic.style.height = Math.max(8, Math.round(26 / ar)) + "px"; } else { pic.style.height = "26px"; pic.style.width = Math.max(8, Math.round(26 * ar)) + "px"; }
      shapes.append(el("button", { type: "button", class: "ratio" + (f.ratio === k ? " on" : ""), onclick: () => change((fr) => { fr.ratio = k; }) }, el("span", { class: "pic-box" }, pic), el("small", { text: name })));
    }
    host.append(el("div", { class: "field" }, el("span", { text: "Shape of the video" }), shapes));
    // background layer
    host.append(el("div", { class: "layer-h" }, icon("layers", "s"), el("b", { text: "Background layer" }), el("small", { text: "fills the frame" })));
    host.append(el("div", { class: "field" }, segmented([["blur", "Blur"], ["black", "Black"], ["white", "White"]], f.bg, (v) => change((fr) => { fr.bg = v; }))));
    if (f.bg === "blur") host.append(slider("Blur strength", 0, 100, 1, f.blur, (v) => Math.round(v) + "%", (fr, v) => { fr.blur = v; }));
    // the picture
    host.append(el("div", { class: "layer-h" }, icon("film", "s"), el("b", { text: "Video layer" }), el("small", { text: "on top" })));
    host.append(slider("Size", 10, 400, 1, Math.round(f.scale * 100), (v) => Math.round(v) + "%", (fr, v) => { fr.scale = v / 100; }));
    host.append(el("div", { class: "grid2" },
      slider("Left / right", -100, 100, 1, Math.round(f.x * 100), (v) => String(Math.round(v)), (fr, v) => { fr.x = v / 100; }),
      slider("Up / down", -100, 100, 1, Math.round(f.y * 100), (v) => String(Math.round(v)), (fr, v) => { fr.y = v / 100; })));
    host.append(slider("Turn", -180, 180, 1, f.rotate, (v) => Math.round(v) + "°", (fr, v) => { fr.rotate = v; }));
    const fill = () => { const a = aspect(F.get()), s = F.srcAspect; return Math.max(a / s, s / a); };
    host.append(el("div", { class: "btns wrap" },
      btn("Fit", { cls: "sm-txt", title: "The whole picture inside the frame", onclick: () => change((fr) => { fr.scale = 1; fr.x = 0; fr.y = 0; }) }),
      btn("Fill", { cls: "sm-txt", title: "Fill the frame (the edges are cut)", onclick: () => change((fr) => { fr.scale = +fill().toFixed(3); fr.x = 0; fr.y = 0; }) }),
      btn("Center", { cls: "sm-txt", onclick: () => change((fr) => { fr.x = 0; fr.y = 0; }) }),
      btn("Straight", { cls: "sm-txt", title: "Turn back to 0°", onclick: () => change((fr) => { fr.rotate = 0; }) })));
    // mask
    host.append(el("div", { class: "layer-h" }, icon("crop", "s"), el("b", { text: "Mask" }), el("small", { text: "shows the video layer only inside" })));
    const chips = el("div", { class: "chips mask-chips" });
    for (const [k, name] of MASKS) chips.append(el("button", { type: "button", class: "chip" + (f.mask.shape === k ? " on" : ""), text: name,
      onclick: () => change((fr) => { fr.mask.shape = k; if (k === "circle" && fr.mask.w > 0.9) { fr.mask.w = 0.5; fr.mask.h = 0.5 * aspect(fr); } }) }));
    host.append(chips);
    if (f.mask.shape !== "none") {
      const sh = f.mask.shape;
      if (sh !== "split") {
        host.append(el("div", { class: "grid2" },
          sh === "strip" ? null : slider("Width", 2, 150, 1, Math.round(f.mask.w * 100), (v) => Math.round(v) + "%", (fr, v) => { fr.mask.w = v / 100; }),
          sh === "vstrip" ? null : slider("Height", 2, 150, 1, Math.round(f.mask.h * 100), (v) => Math.round(v) + "%", (fr, v) => { fr.mask.h = v / 100; })));
      }
      host.append(el("div", { class: "grid2" },
        sh === "strip" || sh === "split" ? null : slider("Mask left / right", 0, 100, 1, Math.round(f.mask.x * 100), (v) => Math.round(v) + "%", (fr, v) => { fr.mask.x = v / 100; }),
        sh === "vstrip" ? null : slider("Mask up / down", 0, 100, 1, Math.round(f.mask.y * 100), (v) => Math.round(v) + "%", (fr, v) => { fr.mask.y = v / 100; })));
      host.append(slider("Soft edges", 0, 100, 1, f.mask.feather, (v) => Math.round(v) + "%", (fr, v) => { fr.mask.feather = v; }));
      const inv = el("input", { type: "checkbox", class: "sw" }); inv.checked = !!f.mask.invert;
      inv.addEventListener("change", () => change((fr) => { fr.mask.invert = inv.checked; }));
      host.append(el("label", { class: "swl" }, el("span", { text: "Turn the mask inside out" }), inv));
    }
    host.append(btn("Reset the frame", { icon: "restore", cls: "ghost", onclick: () => change((fr) => { Object.assign(fr, DEF()); }) }));
    host.append(el("p", { class: "hint", text: "The viewer shows the frame live. The export draws it in full quality." }));
  }

  /** get(): the project's frame; set(frame, live): store it (live = while dragging); video(): the playing video element. */
  function init(opts) {
    F.get = opts.get; F.set = opts.set; F.video = opts.video; F.host = opts.host; F.srcAspect = opts.srcAspect || 16 / 9;
    F.cv = el("canvas", { class: "frame-cv", hidden: true });
    F.host.prepend(F.cv);
    show();
    cancelAnimationFrame(F.raf); F.raf = requestAnimationFrame(loop);
  }
  return { init, active, aspect, renderPanel, show, full, draw };
})();
