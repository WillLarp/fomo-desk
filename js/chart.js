/* Canvas charts: Home line, Coin candles, Analyst score ring. No library. */
(function (global) {
  "use strict";

  var C = {
    green: "#00d35a", red: "#ff453a", orange: "#ff9f0a", blue: "#0a84ff",
    cyan: "#64d2ff", yellow: "#ffd60a",
    text2: "rgba(235,235,245,0.6)", text3: "rgba(235,235,245,0.3)", card2: "#2c2c2e"
  };
  var FONT = '-apple-system, "SF Pro Text", BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif';

  function reduced() {
    return window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function hexA(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }
  function toMs(ts) {
    if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
    var t = Date.parse(ts);
    return isNaN(t) ? 0 : t;
  }
  function fit(canvas) {
    var r = canvas.getBoundingClientRect();
    var dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    var w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr; canvas.height = h * dpr;
    }
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: w, h: h };
  }
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  var SUB = "₀₁₂₃₄₅₆₇₈₉";
  function fmtPrice(v) {
    if (v == null || !isFinite(v)) return "n/a";
    var a = Math.abs(v);
    if (a >= 1000) return "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (a >= 1) return "$" + v.toFixed(2);
    if (a >= 0.01) return "$" + v.toFixed(4);
    if (a === 0) return "$0";
    var s = a.toFixed(20).slice(2);
    var zeros = s.match(/^0*/)[0].length;
    var sig = s.slice(zeros, zeros + 4).replace(/0+$/, "") || "0";
    if (zeros < 4) return "$0." + "0".repeat(zeros) + sig;
    return "$0.0" + String(zeros).split("").map(function (d) { return SUB[+d]; }).join("") + sig;
  }

  function LineChart(canvas, opts) {
    this.c = canvas;
    this.opts = opts || {};
    this.points = [];
    this.start = null;
    this.marks = [];
    this.markBorn = {};
    this.slide = null;
    this.alive = true;
    this._loop = this._loop.bind(this);
    var self = this;
    this.ro = new ResizeObserver(function () { self.draw(); });
    this.ro.observe(canvas);
    requestAnimationFrame(this._loop);
  }
  LineChart.prototype.destroy = function () {
    this.alive = false;
    if (this.ro) this.ro.disconnect();
  };
  LineChart.prototype.setData = function (d) {
    var pts = (d.points || []).filter(function (p) { return p && p[1] != null; });
    this.points = pts.map(function (p) { return [toMs(p[0]), +p[1]]; });
    this.start = d.start == null ? null : +d.start;
    this.setMarks(d.marks || [], true);
    this.slide = null;
    this.draw();
  };
  LineChart.prototype.setMarks = function (marks, initial) {
    this.marks = (marks || []).map(function (m) { return Object.assign({}, m, { t: toMs(m.ts) }); });
    var now = performance.now();
    for (var i = 0; i < this.marks.length; i++) {
      var m = this.marks[i];
      var k = m.t + "|" + m.label;
      if (this.markBorn[k] == null) this.markBorn[k] = initial ? now - 1000 : now;
    }
  };
  LineChart.prototype.push = function (p) {
    var pt = [toMs(p[0]), +p[1]];
    var last = this.points[this.points.length - 1];
    if (last && pt[0] <= last[0]) { last[1] = pt[1]; this.draw(); return; }
    this.slide = (reduced() || !last) ? null : { t0: performance.now(), from: last.slice() };
    this.points.push(pt);
  };
  LineChart.prototype._loop = function () {
    if (!this.alive) return;
    if (this.c.isConnected) this.draw();
    requestAnimationFrame(this._loop);
  };
  LineChart.prototype._valueAt = function (series, t) {
    if (t <= series[0][0]) return series[0][1];
    for (var i = 1; i < series.length; i++) {
      if (series[i][0] >= t) {
        var a = series[i - 1], b = series[i];
        var k = b[0] === a[0] ? 1 : (t - a[0]) / (b[0] - a[0]);
        return lerp(a[1], b[1], k);
      }
    }
    return series[series.length - 1][1];
  };
  LineChart.prototype.draw = function () {
    var fitd = fit(this.c), ctx = fitd.ctx, w = fitd.w, h = fitd.h;
    ctx.clearRect(0, 0, w, h);
    var pts = this.points;
    if (pts.length < 2) return;
    var now = performance.now();
    var series = pts;
    if (this.slide) {
      var k = Math.min(1, (now - this.slide.t0) / 250);
      var e = easeOut(k);
      var last = pts[pts.length - 1], from = this.slide.from;
      series = pts.slice(0, -1).concat([[lerp(from[0], last[0], e), lerp(from[1], last[1], e)]]);
      if (k >= 1) this.slide = null;
    }
    var x0 = series[0][0], x1 = series[series.length - 1][0];
    var top = 74, bot = 16;
    var lo = Infinity, hi = -Infinity, i, p;
    for (i = 0; i < series.length; i++) {
      if (series[i][1] < lo) lo = series[i][1];
      if (series[i][1] > hi) hi = series[i][1];
    }
    if (this.start != null) { lo = Math.min(lo, this.start); hi = Math.max(hi, this.start); }
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    var span = hi - lo; hi += span * 0.06; lo -= span * 0.06;
    function X(t) { return x1 === x0 ? 0 : ((t - x0) / (x1 - x0)) * (w - 14); }
    function Y(v) { return top + (1 - (v - lo) / (hi - lo)) * (h - top - bot); }

    if (this.start != null) {
      ctx.save();
      ctx.setLineDash([2, 5]);
      ctx.strokeStyle = C.text3; ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(0, Math.round(Y(this.start)) + 0.5);
      ctx.lineTo(w, Math.round(Y(this.start)) + 0.5);
      ctx.stroke();
      ctx.restore();
    }

    var grad = ctx.createLinearGradient(0, top, 0, h);
    grad.addColorStop(0, "rgba(0,211,90,0.26)");
    grad.addColorStop(1, "rgba(0,211,90,0)");
    ctx.beginPath();
    for (i = 0; i < series.length; i++) {
      p = series[i];
      i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]));
    }
    ctx.lineTo(X(series[series.length - 1][0]), h);
    ctx.lineTo(X(series[0][0]), h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.lineWidth = 2.25;
    ctx.beginPath();
    for (i = 0; i < series.length; i++) {
      p = series[i];
      i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]));
    }
    ctx.strokeStyle = C.green;
    ctx.stroke();

    this._drawMarks(ctx, series, X, Y, x0, x1, w, top, now);

    var lp = series[series.length - 1];
    ctx.fillStyle = C.green;
    ctx.beginPath();
    ctx.arc(X(lp[0]), Y(lp[1]), 4.5, 0, Math.PI * 2);
    ctx.fill();
  };
  LineChart.prototype._drawMarks = function (ctx, series, X, Y, x0, x1, w, top, now) {
    var ms = this.marks.filter(function (m) { return m.t >= x0 && m.t <= x1; })
      .sort(function (a, b) { return a.t - b.t; });
    if (!ms.length) return;
    ctx.font = "600 12px " + FONT;
    var H = 22, gap = 4, rowH = H + gap;
    var placed = [];
    var items = [];
    function hits(x0l, y0, w0) {
      for (var i = 0; i < placed.length; i++) {
        var p = placed[i];
        if (!(x0l + w0 + 4 < p.x0 || x0l > p.x1 + 4 || y0 + H + gap <= p.y0 || y0 >= p.y1 + gap)) return true;
      }
      return false;
    }
    var self = this;
    ms.forEach(function (m) {
      var x = X(m.t), y = Y(self._valueAt(series, m.t));
      var tw = ctx.measureText(m.label || "").width + 16;
      var lx = Math.max(4, Math.min(w - tw - 4, x - tw / 2));
      var ly = Math.min(y - 14 - H, top - 4);
      while (ly >= 2 && hits(lx, ly, tw)) ly -= rowH;
      if (ly < 2) { items.push({ m: m, x: x, y: y, dotOnly: true }); return; }
      placed.push({ x0: lx, x1: lx + tw, y0: ly, y1: ly + H });
      items.push({ m: m, x: x, y: y, lx: lx, ly: ly, tw: tw });
    });
    function alphaOf(it) {
      var born = self.markBorn[it.m.t + "|" + it.m.label] || 0;
      return reduced() ? 1 : Math.min(1, (now - born) / 200);
    }
    function colOf(it) {
      return it.m.kind === "buy" ? C.blue : it.m.kind === "win" ? C.green : C.red;
    }
    ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
    items.forEach(function (it) {
      if (it.dotOnly) return;
      ctx.globalAlpha = alphaOf(it) * 0.45; ctx.strokeStyle = colOf(it);
      ctx.beginPath(); ctx.moveTo(it.x + 0.5, it.ly + H); ctx.lineTo(it.x + 0.5, it.y - 5); ctx.stroke();
    });
    ctx.setLineDash([]);
    items.forEach(function (it) {
      ctx.globalAlpha = alphaOf(it);
      ctx.fillStyle = "#000"; ctx.beginPath(); ctx.arc(it.x, it.y, 5, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = colOf(it); ctx.beginPath(); ctx.arc(it.x, it.y, 3.5, 0, Math.PI * 2); ctx.fill();
    });
    ctx.textBaseline = "middle";
    items.forEach(function (it) {
      if (it.dotOnly) return;
      var col = colOf(it);
      ctx.globalAlpha = alphaOf(it);
      roundRect(ctx, it.lx, it.ly, it.tw, H, H / 2);
      ctx.fillStyle = "#000"; ctx.fill();
      ctx.fillStyle = hexA(col, 0.2); ctx.fill();
      ctx.fillStyle = it.m.kind === "buy" ? "#4da3ff" : col;
      ctx.fillText(it.m.label, it.lx + 8, it.ly + H / 2 + 0.5);
    });
    ctx.globalAlpha = 1;
  };

  function CandleChart(canvas) {
    this.c = canvas; this.candles = []; this.exitLine = null; this.entry = null;
    var self = this;
    this.ro = new ResizeObserver(function () { self.draw(); });
    this.ro.observe(canvas);
  }
  CandleChart.prototype.destroy = function () { if (this.ro) this.ro.disconnect(); };
  CandleChart.prototype.setData = function (candles, exitLine, entry) {
    this.candles = (candles || []).filter(function (c) { return c && c[4] != null; });
    this.exitLine = exitLine == null ? null : +exitLine;
    this.entry = entry == null ? null : +entry;
    this.draw();
  };
  CandleChart.prototype.draw = function () {
    var fitd = fit(this.c), ctx = fitd.ctx, w = fitd.w, h = fitd.h;
    ctx.clearRect(0, 0, w, h);
    var cs = this.candles;
    if (cs.length < 1) return;
    var maxBars = Math.floor((w - 100) / 7);
    if (cs.length > maxBars) {
      var k = Math.ceil(cs.length / maxBars), out = [], i, g;
      for (i = 0; i < cs.length; i += k) {
        g = cs.slice(i, i + k);
        out.push([
          g[0][0], g[0][1],
          Math.max.apply(null, g.map(function (x) { return x[2]; })),
          Math.min.apply(null, g.map(function (x) { return x[3]; })),
          g[g.length - 1][4],
          g.reduce(function (s, x) { return s + (+x[5] || 0); }, 0)
        ]);
      }
      cs = out;
    }
    ctx.font = "500 11px " + FONT;
    var lo = Infinity, hi = -Infinity, vmax = 0, i, c;
    for (i = 0; i < cs.length; i++) {
      c = cs[i];
      lo = Math.min(lo, c[3]); hi = Math.max(hi, c[2]);
      vmax = Math.max(vmax, +c[5] || 0);
    }
    if (this.exitLine != null) { lo = Math.min(lo, this.exitLine); hi = Math.max(hi, this.exitLine); }
    if (this.entry != null) { lo = Math.min(lo, this.entry); hi = Math.max(hi, this.entry); }
    if (hi - lo <= 0) { hi = hi * 1.01 + 1e-12; lo = lo * 0.99; }
    var sp = hi - lo; hi += sp * 0.08; lo -= sp * 0.08;
    var padL = 12, lw = 0;
    for (i = 0; i <= 3; i++) lw = Math.max(lw, ctx.measureText(fmtPrice(lo + ((hi - lo) * i) / 3)).width);
    var padR = Math.ceil(lw) + 22, top = 16, volH = 52, gap = 12;
    var priceB = h - volH - gap;
    function Y(v) { return top + (1 - (v - lo) / (hi - lo)) * (priceB - top); }
    var step = (w - padL - padR) / cs.length;
    var bw = Math.max(2, Math.min(9, step * 0.62));
    ctx.textBaseline = "middle"; ctx.fillStyle = C.text3;
    for (i = 0; i <= 3; i++) {
      var v = lo + ((hi - lo) * i) / 3;
      var y = Math.round(Y(v)) + 0.5;
      ctx.strokeStyle = "rgba(84,84,88,0.35)"; ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR + 6, y); ctx.stroke();
      ctx.fillText(fmtPrice(v), w - padR + 10, y);
    }
    for (i = 0; i < cs.length; i++) {
      c = cs[i];
      var x = padL + step * i + step / 2;
      var up = c[4] >= c[1];
      var col = up ? C.green : C.red;
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, Y(c[2])); ctx.lineTo(Math.round(x) + 0.5, Y(c[3])); ctx.stroke();
      var yo = Y(c[1]), yc = Y(c[4]);
      var bh = Math.max(1, Math.abs(yc - yo));
      roundRect(ctx, x - bw / 2, Math.min(yo, yc), bw, bh, Math.min(1.5, bw / 3));
      ctx.fill();
      if (vmax > 0) {
        var vh = Math.max(1, ((+c[5] || 0) / vmax) * volH);
        ctx.fillStyle = up ? "rgba(0,211,90,0.35)" : "rgba(255,69,58,0.35)";
        roundRect(ctx, x - bw / 2, h - vh, bw, vh, Math.min(1.5, bw / 3));
        ctx.fill();
      }
    }
    var self = this;
    function hline(v, color, label, dash) {
      var y = Math.round(Y(v)) + 0.5;
      ctx.save(); ctx.setLineDash(dash); ctx.strokeStyle = color; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR + 4, y); ctx.stroke(); ctx.restore();
      ctx.font = "600 11px " + FONT;
      var tw = ctx.measureText(label).width + 12;
      roundRect(ctx, padL + 2, y - 22, tw, 18, 9);
      ctx.fillStyle = "#000"; ctx.fill();
      ctx.fillStyle = hexA(color, 0.2); ctx.fill();
      ctx.fillStyle = color; ctx.fillText(label, padL + 8, y - 13);
    }
    if (self.entry != null) hline(self.entry, "#4da3ff", "Entry", [1, 3]);
    if (self.exitLine != null) hline(self.exitLine, C.orange, "Risk trailing stop", [5, 4]);
  };

  function ScoreRing(canvas) {
    this.c = canvas; this.v = 0; this.target = null; this.t0 = 0; this.from = 0;
  }
  ScoreRing.prototype.set = function (score) {
    var self = this;
    if (score == null) { this.target = null; this.draw(0); return; }
    this.from = this.v;
    this.target = Math.max(0, Math.min(100, +score));
    this.t0 = performance.now();
    function tick() {
      var k = reduced() ? 1 : Math.min(1, (performance.now() - self.t0) / 700);
      self.v = lerp(self.from, self.target, easeOut(k));
      self.draw(self.v);
      if (k < 1 && self.c.isConnected) requestAnimationFrame(tick);
    }
    tick();
  };
  ScoreRing.prototype.draw = function (v) {
    var fitd = fit(this.c), ctx = fitd.ctx, w = fitd.w, h = fitd.h;
    ctx.clearRect(0, 0, w, h);
    var cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 8, lw = 11;
    ctx.lineCap = "round"; ctx.lineWidth = lw;
    ctx.strokeStyle = C.card2;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    if (this.target != null) {
      ctx.strokeStyle = v >= 70 ? C.green : C.yellow;
      ctx.beginPath();
      ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * v) / 100);
      ctx.stroke();
    }
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
    ctx.font = '700 40px -apple-system, "SF Pro Display", BlinkMacSystemFont, sans-serif';
    ctx.fillText(this.target == null ? "—" : String(Math.round(v)), cx, cy + 10);
    ctx.font = "500 12px " + FONT; ctx.fillStyle = C.text2;
    ctx.fillText("of 100", cx, cy + 30);
    ctx.textAlign = "left";
  };

  global.Charts = { LineChart: LineChart, CandleChart: CandleChart, ScoreRing: ScoreRing, fmtPrice: fmtPrice, toMs: toMs };
})(window);
