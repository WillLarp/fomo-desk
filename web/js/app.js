/* Plan B desk UI. Secret path arrives once as #d=, then lives in localStorage
   and is stripped from the address bar. FOMO links come from the JSON only. */
(function () {
  "use strict";

  (function capture() {
    var m = location.hash.match(/[#&]d=([A-Za-z0-9_-]{32,128})/);
    if (m) {
      localStorage.setItem("desk_dp", m[1]);
      history.replaceState(null, "", location.pathname + location.search);
    }
  })();

  var DP = localStorage.getItem("desk_dp");
  var BASE = DP ? "d/" + DP + "/" : null;
  var PARAMS = new URLSearchParams(location.search);
  var S = {
    state: null, chart: null, activity: null, agents: null, account: null,
    tab: PARAMS.get("tab") || "home",
    range: "LIVE",
    coin: null, coinMint: null, audit: null, auditMint: null, auditLoaded: false,
    lastOk: 0, failures: 0, retryIn: 15, fixture: null, offline: false,
    carouselX: 0, lastBalance: null
  };
  var charts = {};
  var pollTimer = null, countTimer = null;
  var LAYER_NAMES = [
    "Mint authority", "Freeze authority", "Dev wallet", "Top 10 holders",
    "Liquidity", "Pool age", "Bundled wallets", "Delegations"
  ];
  var AGENTS = ["scanner", "auditor", "analyst", "executor", "risk"];
  var COLORS = { scanner: "#64d2ff", auditor: "#0a84ff", analyst: "#ffd60a", executor: "#00d35a", risk: "#ff6b9a" };
  var GLYPH = {
    scanner: '<svg viewBox="0 0 20 20"><circle cx="9" cy="9" r="6"/><path d="m13.5 13.5 4 4"/></svg>',
    auditor: '<svg viewBox="0 0 20 20"><path d="M10 2.5 16.5 6v5.2c0 3.7-2.8 5.9-6.5 7.3-3.7-1.4-6.5-3.6-6.5-7.3V6z"/></svg>',
    analyst: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7"/><path d="M10 10 10 5M10 10l3.5 2.5"/></svg>',
    executor: '<svg viewBox="0 0 20 20"><path d="M11.2 2.5 4.5 11h4.8l-1 6.5 6.8-8.6h-4.9z"/></svg>',
    risk: '<svg viewBox="0 0 20 20"><path d="M10 3.2 16.8 16H3.2z"/><path d="M10 8v4M10 13.5v.5"/></svg>'
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function el(html) {
    var t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function reduced() {
    return window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function money(v, d) {
    if (v == null || isNaN(v)) return "—";
    var n = Number(v), sign = n < 0 ? "-" : "";
    return sign + "$" + Math.abs(n).toLocaleString("en-US", {
      minimumFractionDigits: d == null ? 2 : d,
      maximumFractionDigits: d == null ? 2 : d
    });
  }
  function usdShort(v) {
    if (v == null || isNaN(v)) return "n/a";
    var n = Math.abs(Number(v)), sign = v < 0 ? "-" : "";
    if (n >= 1e9) return sign + "$" + (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return sign + "$" + (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return sign + "$" + (n / 1e3).toFixed(1) + "K";
    return money(v, n >= 1 ? 2 : 4);
  }
  function pct(v, digits) {
    if (v == null || isNaN(v)) return "—";
    var d = digits == null ? 2 : digits;
    return (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%";
  }
  function when(ts) {
    if (!ts) return "";
    var n = Number(ts);
    if (n < 1e12) n *= 1000;
    if (!isFinite(n)) { var d = Date.parse(ts); n = isNaN(d) ? 0 : d; }
    return new Date(n).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  function ageLabel(hours, migratedAt) {
    var h = hours;
    if (h == null && migratedAt) {
      var t = Number(migratedAt); if (t < 1e12) t *= 1000;
      h = (Date.now() - t) / 3600000;
    }
    if (h == null || isNaN(h)) return "";
    if (h < 1) return Math.max(1, Math.round(h * 60)) + "m old";
    if (h < 48) return Math.round(h) + "h old";
    return Math.round(h / 24) + "d old";
  }
  function titleCase(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }
  function toast(msg) {
    var t = $("toast") || document.getElementById("toast");
    if (!t) return;
    t.textContent = msg; t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(function () { t.hidden = true; }, 2200);
  }
  function fomoHandle() {
    var h = ((S.account && S.account.fomo_handle) || (S.state && S.state.handle) || "").replace(/^@/, "");
    return h;
  }
  function initial() {
    return (fomoHandle().charAt(0) || "G").toUpperCase();
  }
  function seenId() { return Number(localStorage.getItem("desk_seen_act") || 0); }
  function markSeen() {
    var id = (S.state && S.state.latestActivityId) || 0;
    if (id) localStorage.setItem("desk_seen_act", String(id));
  }
  function hasUnread() {
    var id = (S.state && S.state.latestActivityId) || 0;
    return id && id > seenId();
  }

  /* ---------------- odometer ---------------- */
  function formatBalance(n) {
    if (n == null || isNaN(n)) return "$0.00";
    var sign = n < 0 ? "-" : "";
    return sign + "$" + Math.abs(Number(n)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function setOdo(node, value) {
    if (!node) return;
    var text = formatBalance(value);
    node.classList.toggle("sm", text.replace(/[$,-]/g, "").length >= 7);
    var prev = node.getAttribute("data-txt");
    node.setAttribute("data-txt", text);
    if (reduced() || prev == null) {
      node.textContent = text;
      return;
    }
    if (prev === text) return;
    var parts = [];
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (/\d/.test(ch)) parts.push({ d: +ch, i: i });
      else parts.push({ s: ch });
    }
    var prevDigits = [];
    if (prev) for (var j = 0; j < prev.length; j++) if (/\d/.test(prev[j])) prevDigits.push(+prev[j]);
    node.innerHTML = "";
    var digitIndex = 0, totalDigits = parts.filter(function (p) { return p.d != null; }).length;
    parts.forEach(function (p) {
      if (p.s != null) {
        node.appendChild(document.createTextNode(p.s));
        return;
      }
      var col = document.createElement("span");
      col.className = "odo-d";
      var strip = document.createElement("span");
      strip.className = "odo-s";
      for (var n = 0; n <= 9; n++) {
        var sp = document.createElement("span");
        sp.textContent = String(n);
        strip.appendChild(sp);
      }
      var from = prevDigits[digitIndex];
      if (from == null) from = p.d;
      strip.style.transition = "none";
      strip.style.transform = "translateY(" + (-from) + "em)";
      col.appendChild(strip);
      node.appendChild(col);
      var delay = (totalDigits - 1 - digitIndex) * 30;
      digitIndex++;
      var dest = p.d;
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          strip.style.transition = "transform 600ms cubic-bezier(0.16, 1, 0.3, 1) " + delay + "ms";
          strip.style.transform = "translateY(" + (-dest) + "em)";
        });
      });
    });
  }

  /* ---------------- network ---------------- */
  function fetchJson(path, optional) {
    return fetch(BASE + path, { cache: "no-store" }).then(function (r) {
      if (!r.ok) { if (optional) return null; throw new Error("HTTP " + r.status); }
      return r.json();
    }).catch(function (e) { if (optional) return null; throw e; });
  }

  function lastUpdateLabel() {
    var ts = (S.state && S.state.updatedAt) || (S.lastOk / 1000);
    return ts ? when(ts) : "—";
  }

  function paintOffline() {
    $$(".offline").forEach(function (n) {
      if (!S.offline) { n.textContent = ""; return; }
      n.textContent = "Desk offline · retrying in " + S.retryIn + "s · last update " + lastUpdateLabel();
    });
  }

  function schedulePoll(sec) {
    clearTimeout(pollTimer);
    S.retryIn = sec;
    paintOffline();
    pollTimer = setTimeout(poll, sec * 1000);
  }

  function tickCountdown() {
    if (!S.offline) return;
    S.retryIn = Math.max(0, S.retryIn - 1);
    paintOffline();
  }

  function poll() {
    if (!BASE) { show(); return; }
    Promise.all([
      fetchJson("state.json"),
      fetchJson("chart.json", true),
      fetchJson("activity.json", true),
      fetchJson("agents.json", true),
      fetchJson("account.json", true)
    ]).then(function (res) {
      S.state = res[0];
      if (res[1]) S.chart = res[1];
      if (res[2]) S.activity = res[2];
      if (res[3]) S.agents = res[3];
      if (res[4]) S.account = res[4];
      S.lastOk = Date.now();
      S.failures = 0;
      S.offline = false;
      S.retryIn = 15;
      show();
      refreshOpen();
      schedulePoll(15);
    }).catch(function () {
      S.failures++;
      S.offline = true;
      var wait = Math.min(30, 5 * S.failures);
      S.retryIn = wait;
      if (S.state) { show(); paintOffline(); }
      else show();
      schedulePoll(wait);
    });
  }

  function agentCard(name) {
    var list = (S.state && S.state.agents) || [];
    for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
    return { name: name, status: "down", count: 0, subtitle: "", card: {} };
  }

  function statusDot(status) {
    var st = status === "paused" ? "paused" : (status === "running" ? "" : "down");
    return '<i class="sdot' + (st ? " " + st : "") + '"></i>';
  }

  /* ---------------- chrome ---------------- */
  function navHome() {
    var unread = hasUnread() ? '<i class="dot"></i>' : "";
    return '<div class="nav">' +
      '<button class="iconbtn" data-go="account" aria-label="Account"><span class="avatar">' + esc(initial()) + "</span></button>" +
      '<div class="grow"><button class="nav-title" data-go="account">' + esc(fomoHandle() || "Grok Bot") + ' <span class="chev">›</span></button></div>' +
      '<button class="iconbtn" data-search aria-label="Search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 5 5"/></svg></button>' +
      '<button class="iconbtn" data-go="activity" aria-label="Notifications"><svg viewBox="0 0 24 24"><path d="M6.4 9.2a5.6 5.6 0 1 1 11.2 0c0 3.4 1.3 5.3 2.1 6.6H4.3c.8-1.3 2.1-3.2 2.1-6.6z"/><path d="M10 18.2a2 2 0 0 0 4 0"/></svg>' + unread + "</button>" +
      "</div>";
  }
  function navBack(title) {
    return '<div class="nav"><button class="back" data-back aria-label="Back"><svg viewBox="0 0 24 24"><path d="M15 5 8 12l7 7"/></svg>Back</button>' +
      '<div class="nav-center">' + esc(title || "") + "</div></div>";
  }
  function offlineRow() { return '<div class="offline"></div>'; }
  function fixtureFlag() {
    return S.fixture ? '<div class="fixture-flag" id="fixtureFlag">FIXTURE · ' + esc(S.fixture) + "</div>" : "";
  }

  function actRow(r, asButton) {
    var tag = asButton === false ? "div" : "button";
    var kind = r.kind || "";
    var title = r.title || "";
    var sub = r.subtitle || "";
    var val = ((r.value || "") + (r.value2 ? " " + r.value2 : "")).trim();
    var mint = r.mint || "";
    var reject = kind === "rejected" || /rejected/i.test(title);
    var cls = "";
    if (/^\+/.test(val) || kind === "sold") cls = "up";
    else if (/^-/.test(val) || kind === "stopped") cls = "down";
    else if (reject || val.toLowerCase() === "skipped") cls = "muted";
    return "<" + tag + ' class="row" type="button" data-act="' + esc(kind) + '" data-mint="' + esc(mint) + '">' +
      '<div class="rt"><b>' + esc(title) + "</b><span>" + esc(sub) + "</span></div>" +
      '<div class="rv"><b class="' + cls + '">' + esc(val) + "</b></div></" + tag + ">";
  }

  /* ---------------- agent cards ---------------- */
  function cardShell(name, inner) {
    var a = agentCard(name);
    var color = COLORS[name];
    return '<article class="acard" data-agent="' + name + '">' +
      '<div class="ahead"><span class="glyph" style="background:rgba(255,255,255,0.06);color:' + color + '">' + GLYPH[name] + "</span>" +
      '<span class="aname">' + titleCase(name) + "</span>" +
      '<span class="astat">' + statusDot(a.status) + esc(a.status || "") + "</span></div>" +
      inner + "</article>";
  }

  function renderScannerBody(a) {
    var c = a.card || {};
    var feed = c.feed || [];
    var pools = c.pools != null ? c.pools : a.count;
    var html = '<div class="abig">' + esc(String(pools || 0)) + " <small>pools</small></div>";
    if (!feed.length) {
      html += '<div class="empty" style="padding:20px 0 0"><div class="dash"></div>Listening for graduations</div>';
      return html;
    }
    html += '<div class="abody"><div class="feed">';
    feed.slice(0, 6).forEach(function (r) {
      var kill = r.verdict === "kill" || r.verdict === "dropped";
      html += '<button class="fr ' + (kill ? "kill" : "pass") + '" type="button" data-mint="' + esc(r.mint || "") + '">' +
        '<span class="fn">' + esc(r.name || r.symbol || r.mint) + "</span>" +
        '<span class="fv">' + esc(kill ? (r.reason || "") : "→ auditor") + "</span></button>";
    });
    html += "</div></div>";
    return html;

  }

  function renderAuditorBody(a) {
    var c = a.card || {};
    var cur = c.current;
    var html = '<div class="abig">' + esc(String(c.takenApart || a.count || 0)) + " <small>taken apart</small></div>";
    html += '<div class="strips">';
    var layers = (cur && cur.layers) || [];
    for (var n = 1; n <= 8; n++) {
      var layer = null;
      layers.forEach(function (l) { if (l.n === n) layer = l; });
      var cls = "strip";
      if (cur && !layer) cls += " reading";
      else if (layer && layer.pass === true) cls += " ok";
      else if (layer && layer.pass === false) cls += " bad";
      html += '<div class="' + cls + '">' + n + "</div>";
    }
    html += "</div>";
    var line = "—";
    if (cur) line = "taking apart...";
    else if (c.last) line = c.last;
    if (c.last && !cur && (c.rejected || c.takenApart)) {
      /* last result line, e.g. 8/8 → Executor or the reason */
    }
    html += '<div class="aline">' + esc(line) + "</div>";
    html += '<div class="afoot"><span>' + esc((c.rejected || 0) + " rejected") + "</span></div>";
    return html;
  }

  function renderAnalystBody(a) {
    var c = a.card || {};
    var sc = c.scoring || {};
    var html = '<div class="abig">Scoring</div><div class="ringbox">';
    html += '<canvas class="score-ring" width="132" height="132"></canvas>';
    html += '<div class="ringmeta"><div class="cn">' + esc(sc.name || "Waiting") + "</div>";
    html += '<div class="cs">' + esc((c.mode || "") + (c.scored ? " · " + c.scored + " scored" : "")) + "</div></div></div>";
    return html;
  }

  function renderExecutorBody(a) {
    var c = a.card || {};
    var html = '<div class="abig">' + esc(String(c.trades || 0)) + " <small>trades</small></div>";
    var size = c.lastSize != null ? money(c.lastSize) + " · " + (c.stackPct != null ? c.stackPct : (S.state && S.state.stackPct) || 8) + "% of stack" : "—";
    html += '<div class="xline"><span>' + esc(size) + "</span></div>";
    var progress = c.progress || 0;
    var label = c.state === "filled" ? "✓ Filled" : (c.state === "executing" ? "Executing... " + progress + "%" : "");
    html += '<div class="bar"><i style="width:' + esc(String(progress)) + '%"></i></div>';
    html += '<div class="aline">' + esc(label) + "</div>";
    return html;
  }

  function renderRiskBody(a) {
    var c = a.card || {};
    var stop = c.stopPct != null ? c.stopPct : (S.state && S.state.stopPct) || 6;
    var trail = c.trailPct != null ? c.trailPct : (S.state && S.state.trailPct) || 12;
    var sub = "Guarding";
    if (c.last && /trail/i.test(c.last)) sub = "Trailed out";
    else if (c.last && /stop/i.test(c.last)) sub = "Stopped";
    else if (!c.guarding && c.last) sub = c.last;
    var html = '<div class="abig">' + esc(sub) + "</div>";
    html += '<div class="abody"><div class="sub-row">';
    html += '<span class="pill red mono">stop -' + esc(String(stop)) + "%</span>";
    html += '<span class="pill green mono">trail ' + esc(String(trail)) + "%</span>";
    html += "</div></div>";
    return html;
  }

  function renderAgentCard(name) {
    var a = agentCard(name);
    var body =
      name === "scanner" ? renderScannerBody(a) :
      name === "auditor" ? renderAuditorBody(a) :
      name === "analyst" ? renderAnalystBody(a) :
      name === "executor" ? renderExecutorBody(a) :
      renderRiskBody(a);
    return cardShell(name, body);
  }

  function mountRings(root) {
    $$("canvas.score-ring", root).forEach(function (cv) {
      var card = agentCard("analyst");
      var score = card.card && card.card.scoring && card.card.scoring.score;
      var ring = new window.Charts.ScoreRing(cv);
      ring.set(score == null ? null : score);
      charts.rings = charts.rings || [];
      charts.rings.push(ring);
    });
  }

  /* ---------------- pages ---------------- */
  function destroyCharts() {
    Object.keys(charts).forEach(function (k) {
      var c = charts[k];
      if (Array.isArray(c)) c.forEach(function (x) { if (x && x.destroy) x.destroy(); });
      else if (c && c.destroy) c.destroy();
    });
    charts = {};
  }

  function chartDoc() {
    var ch = S.chart || { points: [], marks: [], start: null };
    if (ch.ranges && ch.ranges[S.range]) return ch.ranges[S.range];
    return ch;
  }

  function renderHome() {
    var st = S.state;
    var running = (st && st.agentsRunning != null)
      ? st.agentsRunning
      : (st && st.agents ? st.agents.filter(function (a) { return a.status === "running"; }).length : 0);
    var html = '<section class="page" data-page="home">' + navHome() + offlineRow() + fixtureFlag();
    if (!st && !S.offline) {
      html += '<div class="hero"><div class="sk" style="width:160px;height:18px"></div>' +
        '<div class="sk" style="width:220px;height:72px;margin:10px 0"></div>' +
        '<div class="sk" style="width:180px;height:24px"></div></div>' +
        '<div class="chart-wrap"><div class="sk" style="position:absolute;inset:16px;border-radius:12px"></div></div>';
      html += "</section>";
      return html;
    }
    html += '<div class="hero">';
    html += '<div class="balance" id="odo"></div>';
    html += '<div class="sub-row">';
    var today = st ? st.todayPct : 0;
    html += '<span class="' + (today > 0 ? "up" : today < 0 ? "down" : "muted") + '">' + esc(pct(today, 2)) + " today</span>";
    var mult = st && st.multiplier != null ? Number(st.multiplier).toFixed(2) : "1.00";
    html += '<span class="pill green" id="multPill">' + esc(mult) + "×</span>";
    if (st && st.mode !== "LIVE") html += '<span class="pill orange">PAPER</span>';
    html += "</div></div>";
    html += '<div class="chart-wrap"><canvas id="equityChart"></canvas>';
    var doc = chartDoc();
    var empty = !st || !((doc.marks && doc.marks.length) || ((S.activity && S.activity.rows) || []).some(function (r) { return /bought|sold|stopped/i.test(r.kind || r.title || ""); }));
    html += '<div class="chart-empty" id="chartEmpty"' + (empty ? "" : " hidden") + '><div class="dash"></div><span>Waiting for the first trade</span></div>';
    html += "</div>";
    html += '<div class="ranges">';
    ["LIVE", "1H", "4H", "1D", "1W", "ALL"].forEach(function (r) {
      html += '<button type="button" data-range="' + r + '" class="' + (S.range === r ? "on" : "") + '">' + r + "</button>";
    });
    html += "</div>";
    html += '<div class="section-h"><h2>Agents · ' + running + ' running</h2></div>';
    html += '<div class="carousel">';
    AGENTS.forEach(function (n) { html += renderAgentCard(n); });
    html += '</div><div class="dots">' + AGENTS.map(function (_, i) { return "<i" + (i === 0 ? ' class="on"' : "") + "></i>"; }).join("") + "</div>";
    var rows = ((S.activity && S.activity.rows) || []).slice(0, 5);
    html += '<div class="section-h"><h2>Activity</h2><button type="button" data-go="activity">See all</button></div>';
    html += '<div class="list">';
    if (!rows.length) html += '<div class="empty"><div class="dash"></div>Nothing yet tonight</div>';
    else rows.forEach(function (r) { html += actRow(r); });
    html += "</div></section>";
    return html;
  }

  function bindHome(root) {
    var odo = $("#odo", root);
    if (odo && S.state) setOdo(odo, S.state.balance);
    var canvas = $("#equityChart", root);
    var emptyEl = $("#chartEmpty", root);
    if (canvas && window.Charts && !(emptyEl && !emptyEl.hidden)) {
      charts.line = new window.Charts.LineChart(canvas);
      charts.line.setData(chartDoc());
    }
    mountRings(root);
    var car = $(".carousel", root);
    if (car) {
      car.scrollLeft = S.carouselX || 0;
      car.addEventListener("scroll", function () {
        S.carouselX = car.scrollLeft;
        var w = car.firstElementChild ? car.firstElementChild.offsetWidth + 10 : 1;
        var i = Math.round(car.scrollLeft / w);
        $$(".dots i", root).forEach(function (d, n) { d.classList.toggle("on", n === i); });
      });
      if (PARAMS.get("card") != null) {
        var n = Number(PARAMS.get("card")) || 0;
        car.style.scrollBehavior = "auto";
        var w = car.firstElementChild ? car.firstElementChild.offsetWidth + 10 : 0;
        car.scrollLeft = n * w;
        car.dispatchEvent(new Event("scroll"));
      }
    }
    $$("[data-range]", root).forEach(function (b) {
      b.addEventListener("click", function () {
        S.range = b.getAttribute("data-range");
        show();
      });
    });
  }

  function decisionRows(name) {
    var pack = S.agents && S.agents[name];
    return (pack && (pack.rows || pack)) || [];
  }

  function renderAgents() {
    var html = '<section class="page" data-page="agents">' + navHome() + offlineRow() + fixtureFlag();
    html += '<h1 class="large-title">Agents</h1>';
    if (!S.state) {
      html += '<div class="bigcard"><div class="sk" style="height:80px"></div></div></section>';
      return html;
    }
    var openPos = ((S.state && S.state.positions) || []).length > 0;
    AGENTS.forEach(function (name) {
      var a = agentCard(name);
      var rows = decisionRows(name).slice(0, 20);
      var locked = name === "risk" && openPos;
      html += '<div class="bigcard" id="agent-' + name + '">';
      html += '<div class="ahead"><span class="glyph" style="color:' + COLORS[name] + ';background:rgba(255,255,255,0.06)">' + GLYPH[name] + "</span>";
      html += '<div><div class="aname">' + titleCase(name) + "</div>";
      html += '<div class="astat">' + statusDot(a.status) + esc(a.status || "") + " · " + esc(String(a.count || 0)) + "</div></div>";
      html += '<button class="more" type="button" data-settings="' + name + '" style="border:0;width:auto;margin-left:auto;padding:0 8px">Settings</button>';
      html += '<label class="switch" title="Pause and resume go through Telegram">';
      html += '<input type="checkbox" disabled ' + (a.status === "running" && !a.paused ? "checked" : "") + (locked ? " data-locked" : "") + "><i></i></label></div>";
      if (locked) html += '<div class="group-f" style="padding:8px 0 0">Risk is locked while a position is open.</div>';
      if (!rows.length) {
        var empty = name === "scanner" ? "Listening for graduations" : name === "auditor" ? "No coin taken apart yet" : "Nothing yet tonight";
        html += '<div class="empty" style="padding:18px 0">' + empty + "</div>";
      } else {
        rows.forEach(function (r) {
          var url = r.coinUrl || "";
          var nameHtml = url
            ? '<a class="dn" href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(r.name || r.symbol || (r.mint || "").slice(0, 6)) + "</a>"
            : '<span class="dn">' + esc(r.name || r.symbol || (r.mint || "").slice(0, 6)) + "</span>";
          html += '<div class="dec" data-mint="' + esc(r.mint || "") + '" data-verdict="' + esc(r.verdict || "") + '">' +
            '<span class="dt">' + esc(when(r.ts)) + "</span>" + nameHtml +
            '<span class="dr">' + esc(r.verdict || "") + (r.reason ? " · " + r.reason : "") + "</span></div>";
        });
      }
      html += "</div>";
    });
    html += "</section>";
    return html;
  }

  function renderActivity() {
    var rows = (S.activity && S.activity.rows) || [];
    var html = '<section class="page" data-page="activity">' + navHome() + offlineRow() + fixtureFlag();
    html += '<h1 class="large-title">Activity</h1><div class="list">';
    if (!rows.length) html += '<div class="empty" id="emptyActivity"><div class="dash"></div>Nothing yet tonight</div>';
    else rows.forEach(function (r) { html += actRow(r); });
    html += "</div></section>";
    return html;
  }

  function settingControl(key, s) {
    var val = s.value;
    var label = s.label || (key.split(".")[1] || key).replace(/_/g, " ");
    var kind = s.kind || "";
    var html = '<div class="cell stack"><div class="cl">' + esc(label) + "</div>";
    if (kind === "bool" || (s.choices && s.choices.length === 2 && (s.choices[0] === true || s.choices[0] === "true"))) {
      html += '<label class="switch"><input type="checkbox" disabled ' + (val === true || val === "true" ? "checked" : "") + "><i></i></label></div>";
      return html;
    }
    if (s.choices && s.choices.length) {
      html += '<div class="cv">' + esc(String(val)) + "</div></div>";
      return html;
    }
    if (s.min != null && s.max != null && typeof val === "number") {
      var p = ((val - s.min) / ((s.max - s.min) || 1)) * 100;
      html += '<div class="setrow"><input type="range" disabled min="' + s.min + '" max="' + s.max + '" value="' + esc(String(val)) + '" style="--p:' + p + '%">';
      html += '<input class="numf" disabled value="' + esc(String(val)) + '"></div></div>';
      return html;
    }
    html += '<div class="cv">' + esc(String(val)) + "</div></div>";
    return html;
  }

  function renderAccount() {
    var a = S.account || {}, st = S.state || {};
    var mode = a.mode || st.mode || "PAPER";
    var html = '<section class="page" data-page="account">' + navHome() + offlineRow() + fixtureFlag();
    html += '<h1 class="large-title">Account</h1>';
    html += '<div class="group">';
    html += '<div class="cell"><div class="cl">Mode</div><div class="seg">';
    html += '<button type="button" class="' + (mode === "PAPER" ? "on" : "") + '" disabled>PAPER</button>';
    html += '<button type="button" class="' + (mode === "LIVE" ? "on" : "") + '" disabled>LIVE</button>';
    html += "</div></div>";
    html += '<div class="cell"><div class="cl">Stack</div><div class="cv">' + esc(money(a.stack != null ? a.stack : st.balance)) + "</div></div>";
    html += "</div>";
    html += '<div class="group-f">Settings, pause and mode changes go through Telegram. This tab is read-only on GitHub Pages.</div>';

    var profile = a.profile_url;
    var signup = a.signup_url;
    html += '<div class="group-h">FOMO</div><div class="group">';
    if (profile) {
      html += '<a class="cell link" href="' + esc(profile) + '" target="_blank" rel="noopener"><div class="cl">Your FOMO profile<small>' + esc(a.fomo_handle || "") + '</small></div><span class="chev">›</span></a>';
    } else {
      html += '<div class="cell"><div class="cl">Your FOMO profile</div><div class="cv">—</div></div>';
    }
    html += '<button class="cell link" type="button" data-invite><div class="cl">Invite a friend<small>Share your FOMO referral link</small></div><span class="chev">›</span></button>';
    html += "</div>";

    html += '<div class="group-h">Telegram</div><div class="group">';
    var connected = a.telegramConnected != null ? a.telegramConnected : true;
    html += '<div class="cell"><div class="cl">Telegram</div><div class="cv">' + (connected ? "connected" : "not connected") + "</div></div>";
    html += '<div class="cell"><div class="cl">Send test push<small>Not available on GitHub Pages. Telegram is the only notifier.</small></div></div>';
    html += "</div>";
    html += '<div class="group-f">No web push on plan B. Telegram is the only notifier.</div>';

    if (a.ticket_warning) html += '<div class="group-f" style="color:var(--orange)">' + esc(a.ticket_warning) + "</div>";

    var settings = a.settings || {};
    var groups = ["scanner", "auditor", "analyst", "executor", "risk", "desk"];
    groups.forEach(function (g) {
      var hide = { "desk.publish_s":1, "desk.pages_push_s":1, "desk.paused":1, "desk.rpc_url":1,
        "scanner.dex_limit_per_min":1, "scanner.ws_silence_s":1, "scanner.dex_429_wait_s":1,
        "auditor.retry_gap_s":1, "auditor.liq_drop_pct":1,
        "analyst.retry_gap_s":1, "analyst.queue_429_s":1,
        "executor.paper_bar_s":1, "risk.no_price_polls":1, "risk.dex_429_wait_s":1 };
      var keys = Object.keys(settings).filter(function (k) { return k.split(".")[0] === g && !hide[k]; });
      if (!keys.length) return;
      html += '<div class="group-h" id="settings-' + g + '">' + titleCase(g) + '</div><div class="group">';
      keys.forEach(function (k) { html += settingControl(k, settings[k] || {}); });
      html += "</div>";
    });
    html += "</section>";
    return html;
  }

  function renderCoin() {
    var c = S.coin, mint = S.coinMint;
    var pos0 = c && c.position;
    var html = '<section class="page pushed' + (pos0 ? ' has-pos' : '') + '" data-page="coin">' + navBack((c && c.name) || "Coin") + offlineRow() + fixtureFlag();
    if (!c) {
      html += '<div class="hero"><div class="sk" style="width:140px;height:28px"></div><div class="sk" style="width:200px;height:44px;margin-top:16px"></div></div>';
      html += '<div class="candles"><div class="sk" style="position:absolute;inset:16px;border-radius:12px"></div></div></section>';
      return html;
    }
    var symbol = c.symbol || "";
    var age = ageLabel(c.ageH, c.migratedAt);
    var meta = [symbol, "Solana", age].filter(Boolean).join(" · ");
    var shield = c.shield;
    var shieldCls = c.auditPassed === false ? "bad" : (c.auditPassed === true || (shield && /^8\//.test(shield)) ? "ok" : "");
    html += '<div class="coin-head"><div class="grow"><h1>' + esc(c.name || mint) + "</h1>";
    html += '<div class="meta">' + esc(meta);
    if (shield) html += '<span class="shield ' + shieldCls + '">' + esc(shield) + "</span>";
    if ((S.state && S.state.mode) !== "LIVE") html += '<span class="pill orange">PAPER</span>';
    html += "</div></div></div>";
    var price = (window.Charts && window.Charts.fmtPrice) ? window.Charts.fmtPrice(c.price) : money(c.price, 6);
    html += '<div class="price">' + esc(price) + "</div>";
    if (c.sinceEntryPct != null) {
      html += '<div class="price-sub ' + (c.sinceEntryPct >= 0 ? "up" : "down") + '">▲ ' + esc(pct(c.sinceEntryPct, 2).replace("+", "")) + " since entry</div>";
    }
    html += '<div class="candles"><canvas id="coinChart"></canvas></div>';
    function stat(label, value) {
      return "<div><span>" + label + "</span><b>" + esc(value) + "</b></div>";
    }
    html += '<div class="stats-grid">';
    html += stat("Market cap", c.marketCap == null ? "n/a" : usdShort(c.marketCap));
    html += stat("Liquidity", c.liquidity == null ? "n/a" : usdShort(c.liquidity));
    html += stat("24h volume", c.volume24h == null ? "n/a" : usdShort(c.volume24h));
    html += stat("Holders", c.holders == null ? "n/a" : Number(c.holders).toLocaleString("en-US"));
    html += "</div>";
    var pos = c.position;
    if (pos) {
      html += '<div class="card pos-card" style="margin-top:16px"><h3>Your position · ' + esc(money(pos.sizeUsd)) + " in · Executor</h3>";
      html += '<div class="kv"><span>Stop line</span><b>' + esc((window.Charts ? window.Charts.fmtPrice(pos.stopPrice) : money(pos.stopPrice, 6))) + "</b></div>";
      html += '<div class="kv"><span>Trail line</span><b>' + esc(pos.trailActive && pos.trailPrice != null ? (window.Charts ? window.Charts.fmtPrice(pos.trailPrice) : money(pos.trailPrice, 6)) : "—") + "</b></div>";
      var left = "";
      if (pos.timeoutAt) {
        var t = Number(pos.timeoutAt); if (t < 1e12) t *= 1000;
        if (isNaN(t)) t = Date.parse(pos.timeoutAt);
        var ms = t - Date.now();
        left = ms <= 0 ? "timed out" : Math.floor(ms / 3600000) + "h " + Math.floor((ms % 3600000) / 60000) + "m left";
      }
      html += '<div class="kv"><span>Time left to timeout</span><b>' + esc(left || "—") + "</b></div></div>";
    }
    html += '<div style="height:20px"></div></section>';
    if (c.coinUrl) {
      var label = pos ? "Sell on FOMO" : "Buy on FOMO";
      html += '<div class="cta-bar"><a class="cta' + (pos ? " red" : "") + '" href="' + esc(c.coinUrl) + '" target="_blank" rel="noopener">' + label + "</a></div>";
    }
    return html;
  }

  function bindCoin(root) {
    var canvas = $("#coinChart", root);
    if (canvas && S.coin && window.Charts) {
      charts.candles = new window.Charts.CandleChart(canvas);
      var pos = S.coin.position;
      var exitLine = S.coin.exitLine;
      if (exitLine == null && pos) {
        exitLine = (pos.trailActive && pos.trailPrice != null) ? pos.trailPrice : pos.stopPrice;
      }
      charts.candles.setData(S.coin.candles || [], exitLine, pos && pos.entryPrice);
    }
  }

  /* ---------------- auditor discs ---------------- */
  function defaultLayerName(n) { return LAYER_NAMES[n - 1] || ("Layer " + n); }

  function renderAuditor() {
    var html = '<section class="page pushed" data-page="auditor">' + navBack("Auditor") + offlineRow() + fixtureFlag();
    html += '<div data-c></div></section>';
    return html;
  }

  function mountAuditor(root) {
    var box = $("[data-c]", root);
    var mint = S.auditMint;
    if (!S.audit && S.audit !== undefined) {
      /* still loading */
    }
    if (!BASE) return;
    var NL = 8, DX = 150, R = 104, RY = 17, TH = 9, Y0 = 26;
    function gap() { return Math.min(37, 259 / Math.max(1, NL - 1)); }
    function disc(n) {
      var cy = Y0 + (n - 1) * gap();
      return '<g data-disc="' + n + '">' +
        '<path d="M' + (DX - R) + " " + cy + " v" + TH + " a" + R + " " + RY + " 0 0 0 " + (2 * R) + " 0 v-" + TH + ' z" fill="#232325" data-side/>' +
        '<ellipse cx="' + DX + '" cy="' + cy + '" rx="' + R + '" ry="' + RY + '" fill="#2c2c2e" stroke="#3a3a3c" stroke-width="1" data-face/>' +
        '<ellipse cx="' + DX + '" cy="' + cy + '" rx="' + (R * 0.62) + '" ry="' + (RY * 0.62) + '" fill="none" stroke="rgba(235,235,245,0.08)" stroke-width="1"/>' +
        '<text x="' + DX + '" y="' + (cy + 6) + '" text-anchor="middle" font-size="12" font-weight="700" fill="rgba(235,235,245,0.3)" data-num>' + n + "</text>" +
        '<g opacity="0" data-mark transform="translate(' + (DX + R + 22) + " " + (cy + 4) + ')"><circle r="11" data-mc/><path data-mp fill="none" stroke="#000" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></g>' +
        "</g>";
    }
    var L = {}, meta = null, cursor = 1, busy = false, done = false, timers = [];
    var hold = PARAMS.get("shot") === "beam";
    function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
    function layerRow(n) {
      var l = L[n];
      var st = !l ? "pending" : l.pass === true ? "ok" : l.pass === false ? "bad" : "na";
      var shown = n < cursor || done;
      return '<div class="row ' + (shown ? "" : "pending") + '" data-row="' + n + '">' +
        '<span class="ln ' + (shown ? (st === "ok" ? "ok" : st === "bad" ? "bad" : "") : (n === cursor && busy ? "reading" : "")) + '">' + n + "</span>" +
        '<div class="rt"><b>' + esc((l && l.name) || defaultLayerName(n)) + "</b>" +
        "<span>" + (shown ? (l && l.pass == null ? "not checked" : "") : n === cursor ? "reading..." : "waiting") + "</span></div>" +
        '<div class="rv"><b class="' + (shown && st === "bad" ? "down" : shown && st === "na" ? "faint" : "") + '">' +
        (shown && l ? esc(l.value == null || l.value === "" ? "n/a" : l.value) : "...") + "</b></div></div>";
    }
    function build() {
      var name = meta && (meta.name || meta.symbol) || "";
      var center = $(".nav-center", root);
      if (center) center.textContent = name ? "Auditor · " + name : "Auditor";
      var discs = [];
      for (var i = NL; i >= 1; i--) discs.push(disc(i));
      var rows = [];
      for (var n = 1; n <= NL; n++) rows.push(layerRow(n));
      box.innerHTML =
        '<div class="verdict" data-v></div>' +
        '<div class="stackbox"><svg viewBox="0 0 300 310" aria-hidden="true">' + discs.join("") +
        '<g data-beam opacity="0"><rect x="18" y="' + (Y0 - 21) + '" width="264" height="42" rx="10" fill="rgba(100,210,255,0.12)"/>' +
        '<rect x="18" y="' + (Y0 + 4) + '" width="264" height="2" rx="1" fill="#64d2ff"/></g></svg></div>' +
        '<div class="list layer-rows" data-rows>' + rows.join("") + "</div>" +
        '<div class="group-h">Auditor</div>' +
        '<div class="group"><div class="cell"><div class="cl">Take apart before buying<small>8 parts per coin, every coin</small></div>' +
        '<label class="switch"><input type="checkbox" data-en disabled ' + ((meta && meta.enabled !== false) ? "checked" : "") + "><i></i></label></div></div>" +
        '<div class="group-f">Change this in Telegram. Turning it off is not available from the Pages app.</div>';
      paintCta();
      paintVerdict();
    }
    function paintVerdict() {
      var v = $("[data-v]", box); if (!v) return;
      var name = esc((meta && (meta.name || meta.symbol)) || "this coin");
      if (!done) {
        v.innerHTML = '<h1 style="color:var(--cyan)">taking apart...</h1><p>' + name + " · part " + Math.min(cursor, NL) + " of " + NL + "</p>";
        return;
      }
      var failed = Object.keys(L).map(function (k) { return L[k]; }).filter(function (l) { return l && l.pass === false; });
      var checked = Object.keys(L).map(function (k) { return L[k]; }).filter(function (l) { return l && l.pass === true; }).length;
      if ((meta && meta.passed === false) || failed.length) {
        var reason = (meta && meta.reason) || (failed[0] ? failed[0].value || failed[0].name : "failed");
        v.innerHTML = '<h1 class="bad">Rejected · ' + esc(reason) + "</h1><p>Grok Bot won't buy " + name + "</p>";
      } else {
        var sized = meta && meta.sizeUsd != null ? "Sent to Executor · " + money(meta.sizeUsd) : "Sent to Executor";
        v.innerHTML = '<h1 class="ok">Cleared · ' + checked + "/" + NL + " parts</h1><p>" + sized + "</p>";
      }
    }
    var beam = { y: 0, op: 0, raf: 0 };
    function setBeam(n, show) {
      var b = $("[data-beam]", box); if (!b) return;
      var y1 = (n - 1) * gap(), o1 = show ? 1 : 0;
      var y0 = beam.y, o0 = beam.op, t0 = performance.now();
      cancelAnimationFrame(beam.raf);
      if (reduced()) {
        beam.y = y1; beam.op = o1;
        b.setAttribute("transform", "translate(0 " + y1 + ")");
        b.setAttribute("opacity", String(o1));
        return;
      }
      function tick() {
        var k = Math.min(1, (performance.now() - t0) / 350), e = 1 - Math.pow(1 - k, 3);
        beam.y = y0 + (y1 - y0) * e;
        beam.op = o0 + (o1 - o0) * Math.min(1, k * 1.75);
        b.setAttribute("transform", "translate(0 " + beam.y.toFixed(2) + ")");
        b.setAttribute("opacity", beam.op.toFixed(3));
        if (k < 1) beam.raf = requestAnimationFrame(tick);
      }
      tick();
    }
    function reveal(n) {
      var g = $('[data-disc="' + n + '"]', box); if (!g) return;
      var l = L[n]; if (!l) return;
      var col = l.pass === true ? "#00d35a" : l.pass === false ? "#ff453a" : "#636366";
      $("[data-side]", g).setAttribute("fill", l.pass === true ? "#0f3d24" : l.pass === false ? "#4a1916" : "#2c2c2e");
      $("[data-face]", g).setAttribute("stroke", col);
      $("[data-num]", g).setAttribute("fill", col);
      var m = $("[data-mark]", g); m.setAttribute("opacity", "1");
      $("[data-mc]", g).setAttribute("fill", col);
      $("[data-mp]", g).setAttribute("d", l.pass === true ? "M-4.5 0.2 -1.3 3.4 4.8-3.6" : l.pass === false ? "M-3.8-3.8 3.8 3.8M3.8-3.8-3.8 3.8" : "M-4 0H4");
    }
    function paintRow(n) {
      var r = $('[data-row="' + n + '"]', box);
      if (r) r.outerHTML = layerRow(n);
    }
    function step() {
      if (done || busy) return;
      if (cursor > NL) { finish(); return; }
      if (!L[cursor]) { setBeam(cursor, true); paintRow(cursor); return; }
      if (hold && cursor >= 3) { setBeam(cursor, true); paintRow(cursor); return; }
      busy = true; setBeam(cursor, true); paintRow(cursor); paintVerdict();
      var wait = reduced() ? 0 : 350;
      var flash = reduced() ? 0 : 150;
      later(function () {
        later(function () {
          reveal(cursor);
          var n = cursor; cursor++; busy = false;
          paintRow(n); paintRow(cursor); paintVerdict(); step();
        }, flash);
      }, wait);
    }
    function finish() {
      done = true; setBeam(NL, false);
      for (var n = 1; n <= NL; n++) { if (L[n]) reveal(n); paintRow(n); }
      paintVerdict();
    }
    function ingest(a) {
      if (!a) return;
      meta = Object.assign(meta || {}, a);
      (a.layers || []).forEach(function (l) { L[+l.n] = Object.assign({}, L[+l.n], l); });
      if (typeof (meta && meta.passed) === "boolean") {
        for (var n = 1; n <= NL; n++) if (!L[n]) L[n] = { n: n, name: defaultLayerName(n), pass: null, value: "not checked" };
      }
    }
    function paintCta() {
      var bar = $(".cta-bar", root);
      if (!bar) { bar = el('<div class="cta-bar"></div>'); root.appendChild(bar); }
      var url = meta && meta.coinUrl;
      bar.innerHTML = url ? '<a class="cta" href="' + esc(url) + '" target="_blank" rel="noopener">Open on FOMO</a>' : "";
    }

    if (!S.auditLoaded && !S.audit) {
      box.innerHTML = '<div class="hero"><div class="sk" style="width:70%;height:34px;margin:8px 16px"></div></div><div class="stackbox"><div class="sk" style="width:220px;height:220px;border-radius:50%;margin:20px auto"></div></div>';
      return;
    }
    if (!S.audit) {
      box.innerHTML = '<div class="empty" id="auditorEmpty" style="padding-top:80px"><div class="dash"></div><b>No coin taken apart yet</b></div>';
      return;
    }
    ingest(S.audit);
    build();
    if (reduced() || PARAMS.get("instant") === "1") finish();
    else step();
    root._auditTimers = timers;
  }

  /* ---------------- search / invite ---------------- */
  function openSearch() {
    var root = document.getElementById("sheet-root");
    root.innerHTML = '<div class="sheet-bg" data-close></div><div class="sheet full"><div class="grabber"></div>' +
      '<div class="searchbar"><input id="q" type="search" placeholder="Name, symbol or mint" autocomplete="off"><button type="button" data-close>Cancel</button></div>' +
      '<div class="list" id="hits"></div></div>';
    var input = $("#q", root), hits = $("#hits", root);
    function run() {
      var q = (input.value || "").trim().toLowerCase();
      var coins = (S.state && S.state.coins) || [];
      var rows = !q ? coins.slice(0, 12) : coins.filter(function (c) {
        return (c.name || "").toLowerCase().indexOf(q) >= 0 ||
          (c.symbol || "").toLowerCase().indexOf(q) >= 0 ||
          (c.mint || "").toLowerCase().indexOf(q) >= 0;
      }).slice(0, 20);
      if (!rows.length) { hits.innerHTML = '<div class="empty">No coins match</div>'; return; }
      hits.innerHTML = rows.map(function (c) {
        return '<button class="row" type="button" data-mint="' + esc(c.mint) + '"><div class="rt"><b>' +
          esc(c.name || c.symbol || c.mint) + "</b><span>" + esc(c.symbol || c.mint) + "</span></div></button>";
      }).join("");
      $$("[data-mint]", hits).forEach(function (b) {
        b.addEventListener("click", function () { closeSheet(); openCoin(b.getAttribute("data-mint")); });
      });
    }
    input.addEventListener("input", run);
    run();
    input.focus();
    $$("[data-close]", root).forEach(function (b) { b.addEventListener("click", closeSheet); });
  }
  function closeSheet() { document.getElementById("sheet-root").innerHTML = ""; }

  function invite() {
    var url = S.account && S.account.signup_url;
    if (!url) { toast("No invite link"); return; }
    function copied() { toast("Link copied"); }
    if (navigator.share) {
      navigator.share({ title: "FOMO", url: url }).catch(function () { copyText(url).then(copied); });
    } else {
      copyText(url).then(copied);
    }
  }
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve) {
      var i = document.createElement("textarea");
      i.value = text; document.body.appendChild(i); i.select();
      try { document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(i); resolve();
    });
  }

  /* ---------------- navigation ---------------- */
  function openCoin(mint) {
    if (!mint) return;
    S.tab = "coin"; S.coinMint = mint; S.coin = S.coinMint === mint ? S.coin : null;
    if (S.coin && S.coin.mint !== mint) S.coin = null;
    show();
    if (!BASE) return;
    fetchJson("coin/" + mint + ".json", true).then(function (doc) {
      S.coin = doc || { mint: mint, name: mint, holders: null, marketCap: null, liquidity: null, volume24h: null, candles: [] };
      if (S.tab === "coin") show();
    });
  }
  function openAuditor(mint) {
    if (!mint) return;
    S.tab = "auditor"; S.auditMint = mint; S.audit = null; S.auditLoaded = false;
    show();
    if (!BASE) return;
    fetchJson("audit/" + mint + ".json", true).then(function (doc) {
      S.audit = doc || null;
      S.auditLoaded = true;
      if (S.tab === "auditor") show();
    });
  }
  function openActivityItem(kind, mint) {
    if (!mint) return;
    if (kind === "rejected" || /reject|skip|kill/i.test(kind || "")) openAuditor(mint);
    else openCoin(mint);
  }
  function refreshOpen() {
    if (S.tab === "coin" && S.coinMint) {
      fetchJson("coin/" + S.coinMint + ".json", true).then(function (doc) {
        if (doc) { S.coin = doc; if (S.tab === "coin") show(); }
      });
    }
    if (S.tab === "auditor" && S.auditMint) {
      fetchJson("audit/" + S.auditMint + ".json", true).then(function (doc) {
        if (doc) { S.audit = doc; if (S.tab === "auditor") show(); }
      });
    }
  }

  function setTab(tab) {
    S.tab = tab;
    if (tab === "activity") markSeen();
    $$("#tabbar [data-tab]").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-tab") === tab);
    });
    var bar = document.getElementById("tabbar");
    if (bar) bar.style.display = (tab === "coin" || tab === "auditor") ? "none" : "";
    show();
  }

  function bindChrome(root) {
    $$("[data-go]", root).forEach(function (b) {
      b.addEventListener("click", function () { setTab(b.getAttribute("data-go")); });
    });
    $$("[data-search]", root).forEach(function (b) { b.addEventListener("click", openSearch); });
    $$("[data-back]", root).forEach(function (b) {
      b.addEventListener("click", function () { setTab("home"); });
    });
    $$("[data-invite]", root).forEach(function (b) { b.addEventListener("click", invite); });
    $$("[data-settings]", root).forEach(function (b) {
      b.addEventListener("click", function () {
        setTab("account");
        setTimeout(function () {
          var id = "settings-" + b.getAttribute("data-settings");
          var n = document.getElementById(id);
          if (n) n.scrollIntoView({ block: "start" });
        }, 50);
      });
    });
    $$("[data-act][data-mint], button.row[data-mint], .fr[data-mint]", root).forEach(function (b) {
      b.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest("a")) return;
        var mint = b.getAttribute("data-mint");
        var kind = b.getAttribute("data-act") || b.getAttribute("data-verdict") || "";
        openActivityItem(kind, mint);
      });
    });
    $$(".dec[data-mint]", root).forEach(function (b) {
      b.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest("a")) return;
        openActivityItem(b.getAttribute("data-verdict"), b.getAttribute("data-mint"));
      });
    });
  }

  function show() {
    destroyCharts();
    var pages = document.getElementById("pages");
    if (!BASE && !S.fixture) {
      pages.innerHTML = '<section class="page">' + navHome() +
        '<div class="empty" id="noKey"><b>Open the desk link once</b>The link with the secret fragment is remembered in this browser.</div></section>';
      bindChrome(pages);
      return;
    }
    var html;
    if (S.tab === "coin") html = renderCoin();
    else if (S.tab === "auditor") html = renderAuditor();
    else if (S.tab === "agents") html = renderAgents();
    else if (S.tab === "activity") html = renderActivity();
    else if (S.tab === "account") html = renderAccount();
    else html = renderHome();
    pages.innerHTML = html;
    var bar = document.getElementById("tabbar");
    if (bar) bar.style.display = (S.tab === "coin" || S.tab === "auditor") ? "none" : "";
    $$("#tabbar [data-tab]").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-tab") === S.tab || (S.tab === "home" && b.getAttribute("data-tab") === "home"));
    });
    bindChrome(pages);
    if (S.tab === "home") bindHome(pages);
    if (S.tab === "coin") bindCoin(pages);
    if (S.tab === "auditor") mountAuditor(pages);
    paintOffline();
    if (PARAMS.get("group")) {
      var g = document.getElementById("settings-" + PARAMS.get("group"));
      if (g) g.scrollIntoView();
    }
  }

  document.querySelectorAll("#tabbar [data-tab]").forEach(function (b) {
    b.addEventListener("click", function () { setTab(b.getAttribute("data-tab")); });
  });

  /* screenshot / empty / error helpers */
  if (PARAMS.get("empty") === "1") {
    BASE = BASE || "d/fixture/";
    S.state = { balance: 200, todayPct: 0, todayUsd: 0, multiplier: 1, mode: "PAPER", agents: AGENTS.map(function (n) { return { name: n, status: "running", count: 0, card: {} }; }), positions: [], coins: [], agentsRunning: 5, updatedAt: Date.now() / 1000, latestActivityId: 0 };
    S.chart = { points: [[Date.now() / 1000 - 60, 200], [Date.now() / 1000, 200]], start: 200, marks: [], range: "LIVE" };
    S.activity = { rows: [] };
    S.account = { mode: "PAPER", stack: 200, fomo_handle: "WillLarp", settings: {}, signup_url: null, profile_url: null, telegramConnected: true, pushNote: "No web push on plan B. Telegram is the only notifier.", readonly: true };
    S.lastOk = Date.now();
    S.offline = false;
  }
  if (PARAMS.get("error") === "1") {
    if (!S.state) {
      S.state = { balance: 200, todayPct: 0, todayUsd: 0, multiplier: 1, mode: "PAPER", agents: AGENTS.map(function (n) { return { name: n, status: "running", count: 0, card: {} }; }), positions: [], coins: [], agentsRunning: 5, updatedAt: (Date.now() / 1000) - 600 };
      S.chart = { points: [[Date.now() / 1000 - 3600, 200], [Date.now() / 1000 - 600, 200]], start: 200, marks: [], range: "LIVE" };
      S.activity = { rows: [] };
      S.account = { mode: "PAPER", stack: 200, fomo_handle: "WillLarp", settings: {}, telegramConnected: true, pushNote: "No web push on plan B. Telegram is the only notifier." };
    }
    S.offline = true;
    S.retryIn = 8;
    S.lastOk = Date.now() - 600000;
    BASE = BASE || "d/offline-fixture/";
  }

  show();
  if (BASE && PARAMS.get("empty") !== "1" && PARAMS.get("error") !== "1") poll();
  countTimer = setInterval(tickCountdown, 1000);

  window.Desk = {
    openCoin: openCoin,
    openAuditor: openAuditor,
    setTab: setTab,
    show: show,
    invite: invite,
    S: S,
    setFixture: function (label, data) {
      S.fixture = label || "sample data";
      if (data) {
        if (data.state) S.state = data.state;
        if (data.chart) S.chart = data.chart;
        if (data.activity) S.activity = data.activity;
        if (data.account) S.account = data.account;
        if (data.agents) S.agents = data.agents;
        if (data.coin) { S.coin = data.coin; S.coinMint = data.coin.mint; }
        if (data.audit) { S.audit = data.audit; S.auditMint = data.audit.mint; S.auditLoaded = true; }
      }
    }
  };
})();

