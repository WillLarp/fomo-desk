(function (global) {
  "use strict";
  function drawLine(canvas, points, marks, exitLine) {
    if (!canvas || !points || points.length < 2) return;
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    var ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    var xs = points.map(function (p) { return p[0]; });
    var ys = points.map(function (p) { return p[1]; });
    if (exitLine != null) ys = ys.concat([exitLine]);
    var min = Math.min.apply(null, ys), max = Math.max.apply(null, ys);
    var pad = (max - min) * 0.15 || 1;
    min -= pad; max += pad;
    var x0 = xs[0], x1 = xs[xs.length - 1] || x0 + 1;
    function X(t) { return ((t - x0) / (x1 - x0 || 1)) * w; }
    function Y(v) { return h - ((v - min) / (max - min || 1)) * h; }
    ctx.beginPath();
    points.forEach(function (p, i) { i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1])); });
    var up = points[points.length - 1][1] >= points[0][1];
    ctx.strokeStyle = up ? "#3dff9a" : "#ff4d5a";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fillStyle = up ? "rgba(61,255,154,0.12)" : "rgba(255,77,90,0.12)";
    ctx.fill();
    if (exitLine != null) {
      ctx.beginPath();
      ctx.setLineDash([5, 4]);
      ctx.moveTo(0, Y(exitLine));
      ctx.lineTo(w, Y(exitLine));
      ctx.strokeStyle = "#ff9f43";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    (marks || []).forEach(function (m) {
      if (m.ts == null) return;
      ctx.beginPath();
      ctx.fillStyle = m.kind === "loss" ? "#ff4d5a" : (m.kind === "buy" ? "#3ee0ff" : "#3dff9a");
      ctx.arc(X(m.ts), 8, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }
  function drawCandles(canvas, candles, exitLine) {
    if (!canvas || !candles || !candles.length) return;
    var pts = candles.map(function (c) { return [c[0], c[4]]; });
    drawLine(canvas, pts, [], exitLine);
  }
  global.DeskChart = { drawLine: drawLine, drawCandles: drawCandles };
})(window);
