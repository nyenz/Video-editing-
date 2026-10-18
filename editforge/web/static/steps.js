"use strict";
// Steps: stacked patterns on the SAME parent video, with nothing rendered in between.
// Every piece is only a note "from this second to that second of the parent video". Pinning a step lays its pieces
// end to end; the next step cuts that again into new pieces. Extending a piece in any step grows it into the real
// footage of the parent, also footage an earlier step removed. The same rules live in editforge/project.py
// (recut, units, apply_pattern, extend_limits) and the tests check that both agree.
(function (root) {
  const SNAP = 0.08, MIN = 0.04;
  const outLen = (p) => (p.end - p.start) / (p.speed || 1);
  const r4 = (x) => Math.round(x * 1e4) / 1e4;
  const copy = (x) => JSON.parse(JSON.stringify(x));

  /** Length of a step as it plays (removed pieces do not count). */
  function stepLength(pieces) { return pieces.reduce((a, p) => a + (p.off ? 0 : outLen(p)), 0); }

  /** Cut the kept pieces, laid end to end, into new pieces of `interval` seconds of playing time. */
  function recut(pieces, interval) {
    if (!(interval >= 0.1)) throw new Error("Pieces must be at least 0.1 seconds long.");
    const kept = pieces.filter((p) => !p.off);
    const total = kept.reduce((a, p) => a + outLen(p), 0);
    const n = Math.floor(total / interval + 1e-9), cuts = [];
    for (let k = 1; k <= n; k++) if (total - k * interval >= SNAP) cuts.push(k * interval);
    if (cuts.length + 1 > 5000) throw new Error("That would make " + (cuts.length + 1) + " pieces (the most is 5,000). Choose a longer time.");
    const out = [];
    let o0 = 0;
    for (const p of kept) {
      const o1 = o0 + outLen(p), sp = p.speed || 1;
      const pts = [o0].concat(cuts.filter((c) => c > o0 + SNAP && c < o1 - SNAP), [o1]);
      for (let i = 0; i + 1 < pts.length; i++) {
        const a = pts[i], b = pts[i + 1];
        let s0, s1;
        if (p.reverse) { s0 = p.end - (b - o0) * sp; s1 = p.end - (a - o0) * sp; } else { s0 = p.start + (a - o0) * sp; s1 = p.start + (b - o0) * sp; }
        const q = copy(p);
        q.start = r4(Math.max(p.start, s0)); q.end = r4(Math.min(p.end, s1)); q.off = false;
        q.unit = cuts.filter((c) => c <= a + SNAP).length;
        if (q.end - q.start >= MIN) out.push(q);
      }
      o0 = o1;
    }
    return out;
  }

  /** The pattern number of each piece: neighbours with the same unit count as one piece. */
  function units(pieces) {
    const out = []; let n = -1, prev;
    for (const p of pieces) { if (p.unit === undefined || p.unit === null || p.unit !== prev) n++; out.push(n); prev = p.unit; }
    return out;
  }
  /** The kept pieces a pattern picks ("in every group of `group`, number `picks`"). */
  function patternHits(kept, group, picks) {
    const u = units(kept), set = new Set(picks);
    return kept.filter((p, i) => set.has((u[i] % group) + 1));
  }
  function countUnits(kept) { const u = units(kept); return u.length ? u[u.length - 1] + 1 : 0; }

  /** How far a piece may grow, in parent time: it stops at the nearest other kept piece of the same step. */
  function extendLimits(pieces, p, duration) {
    let lo = 0, hi = duration;
    for (const q of pieces) {
      if (q === p || q.off) continue;
      if (q.start >= p.end - 1e-6) hi = Math.min(hi, q.start);
      else if (q.end <= p.start + 1e-6) lo = Math.max(lo, q.end);
    }
    return { lo, hi };
  }

  /** A short line describing how a step was made. */
  function ruleText(rule) {
    if (!rule) return "Hand edits";
    const bits = [];
    if (rule.interval) bits.push(+rule.interval.toFixed(2) + " s pieces");
    if (rule.action) bits.push((rule.action === "remove" ? "removed " : "kept ") + rule.picks.join(", ") + " of " + rule.group);
    if (rule.hand) bits.push("hand edits");
    return bits.join(" · ") || "Whole video";
  }

  const api = { recut, units, patternHits, countUnits, extendLimits, stepLength, ruleText, outLen };
  root.Steps = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
