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
  var S = {
    state: null, chart: null, activity: null, agents: null, account: null,
    tab: "home", coin: null, coinMint: null, audit: null, auditMint: null,
    lastOk: 0, failures: 0, fixture: null
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function money(v, d) {
    if (v == null || isNaN(v)) return "—";
    var n = Number(v), sign = n < 0 ? "-" : "";
    return sign + "$" + Math.abs(n).toLocaleString("en-US", {
      minimumFractionDigits: d == null ? 2 : d,
      maximumFractionDigits: d == null ? 2 : d
    });
  }
  function signed(v) {
    if (v == null || isNaN(v)) return "—";
    return (v >= 0 ? "+" : "") + money(v);
  }
  function pct(v) {
    if (v == null || isNaN(v)) return "—";
    return (v > 0 ? "+" : "") + Number(v).toFixed(1) + "%";
  }
  function cls(v) { return v > 0 ? "up" : (v < 0 ? "down" : "flat"); }
  function when(ts) {
    if (!ts) return "";
    var n = Number(ts);
    if (n < 1e12) n *= 1000;
    return new Date(n).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  function toast(msg) {
    var t = $("toast"); if (!t) return;
    t.textContent = msg; t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(function () { t.hidden = true; }, 2000);
  }

  function fetchJson(path, optional) {
    return fetch(BASE + path, { cache: "no-store" }).then(function (r) {
      if (!r.ok) { if (optional) return null; throw new Error("HTTP " + r.status); }
      return r.json();
    }).catch(function (e) { if (optional) return null; throw e; });
  }

  function poll() {
    if (!BASE) { render(); return; }
    Promise.all([
      fetchJson("state.json"),
      fetchJson("chart.json", true),
      fetchJson("activity.json", true),
      fetchJson("agents.json", true),
      fetchJson("account.json", true)
    ]).then(function (res) {
      S.state = res[0]; S.chart = res[1]; S.activity = res[2];
      if (res[3]) S.agents = res[3];
      if (res[4]) S.account = res[4];
      S.lastOk = Date.now(); S.failures = 0;
      $("offlineBar").hidden = true;
      render();
      refreshOpen();
    }).catch(function () {
      S.failures++;
      $("offlineBar").hidden = S.failures < 2;
      if (!S.state) render();
      var dot = $("liveDot");
      if (dot) { dot.className = "dot off"; dot.textContent = "offline"; }
    });
  }

  function agentCard(name) {
    var list = (S.state && S.state.agents) || [];
    for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
    return { name: name, status: "down", count: 0, subtitle: "", card: {} };
  }

  function renderHome() {
    var st = S.state;
    if (!st) return '<div class="card empty" id="emptyHome">Waiting for the first snapshot.</div>';
    var html = "";
    html += '<div class="card" id="homeHero"><div class="kicker">Stack</div>';
    html += '<div class="balance">' + esc(money(st.balance)) + '</div>';
    html += '<div class="pnl ' + cls(st.todayPct) + '">' + esc(pct(st.todayPct)) + ' today · ' + esc(signed(st.todayUsd)) + '</div>';
    html += '<canvas class="chart" id="equityChart"></canvas></div>';

    var sc = agentCard("scanner");
    html += renderScannerCard(sc);
    html += renderAuditorCard(agentCard("auditor"));
    html += renderAnalystCard(agentCard("analyst"));
    html += renderExecutorCard(agentCard("executor"));
    html += renderRiskCard(agentCard("risk"));

    var pos = st.positions || [];
    if (pos.length) {
      html += '<div class="card"><h2>Open</h2>';
      pos.forEach(function (p) {
        html += '<button class="feed-row pass" data-mint="' + esc(p.mint) + '"><span class="name">' +
          esc(p.name || p.mint) + '</span><span class="' + cls(p.pnlPct) + '">' +
          esc(pct(p.pnlPct)) + '</span></button>';
      });
      html += '</div>';
    }
    return html;
  }

  function renderScannerCard(a) {
    var c = a.card || {};
    var feed = c.feed || [];
    var html = '<div class="card agent-card" id="cardScanner"><div class="agent-head">';
    html += '<div class="agent-name">Scanner</div><div class="agent-sub">' + esc((c.pools != null ? c.pools : a.count) + " pools") + '</div></div>';
    if (!feed.length) html += '<div class="muted">Listening for graduations.</div>';
    feed.slice(0, 6).forEach(function (r) {
      var kill = r.verdict === "kill";
      html += '<div class="feed-row ' + (kill ? "kill" : "pass") + '" data-mint="' + esc(r.mint || "") + '">';
      html += '<span class="name">' + esc(r.name || r.symbol || r.mint) +
        (kill ? "" : " · liq " + money(r.liq || r.liquidityUsd, 0) + " · → auditor") + "</span>";
      if (kill) html += '<span class="reason">' + esc(r.reason || "") + "</span>";
      html += "</div>";
    });
    return html + "</div>";
  }

  function renderAuditorCard(a) {
    var c = a.card || {};
    var html = '<div class="card agent-card" id="cardAuditor"><div class="agent-head">';
    html += '<div class="agent-name">Auditor</div><div class="agent-sub">' +
      esc((c.takenApart || 0) + " taken apart") + "</div></div>";
    var cur = c.current;
    if (cur && cur.layers && cur.layers.length) {
      html += '<div class="muted">taking apart…</div><div class="layers">';
      for (var n = 1; n <= 8; n++) {
        var layer = null;
        (cur.layers || []).forEach(function (l) { if (l.n === n) layer = l; });
        var clsL = "layer";
        if (!layer) clsL += " beam";
        else if (layer.pass === true) clsL += " ok";
        else if (layer.pass === false) clsL += " red";
        html += '<div class="' + clsL + '">' + n + "</div>";
      }
      html += "</div>";
    } else {
      html += '<div class="layers">';
      for (var i = 1; i <= 8; i++) html += '<div class="layer">' + i + "</div>";
      html += "</div>";
    }
    html += '<div class="muted" style="margin-top:8px">' + esc(c.last || "") +
      (c.rejected ? " · " + c.rejected + " rejected" : "") + "</div></div>";
    return html;
  }

  function renderAnalystCard(a) {
    var c = a.card || {};
    var sc = c.scoring || {};
    var score = sc.score != null ? sc.score : 0;
    var green = score >= 70;
    var dash = 2 * Math.PI * 36;
    var off = dash * (1 - Math.max(0, Math.min(100, score)) / 100);
    var html = '<div class="card agent-card" id="cardAnalyst"><div class="agent-head">';
    html += '<div class="agent-name">Analyst</div><div class="agent-sub">Scoring</div></div>';
    html += '<div class="ring-wrap"><svg class="ring" viewBox="0 0 84 84">';
    html += '<circle cx="42" cy="42" r="36" fill="none" stroke="#222" stroke-width="8"/>';
    html += '<circle cx="42" cy="42" r="36" fill="none" stroke="' + (green ? "#3dff9a" : "#3ee0ff") +
      '" stroke-width="8" stroke-dasharray="' + dash + '" stroke-dashoffset="' + off +
      '" transform="rotate(-90 42 42)" stroke-linecap="round"/>';
    html += '<text x="42" y="47" text-anchor="middle" fill="#f4f5f7" font-size="18" font-weight="800">' +
      esc(String(score)) + "</text></svg>";
    html += '<div><div>' + esc(sc.name || "Waiting") + '</div><div class="muted">' +
      esc((c.mode || "") + (c.scored ? " · " + c.scored + " scored" : "")) + "</div></div></div></div>";
    return html;
  }

  function renderExecutorCard(a) {
    var c = a.card || {};
    var html = '<div class="card agent-card" id="cardExecutor"><div class="agent-head">';
    html += '<div class="agent-name">Executor</div><div class="agent-sub">' + esc((c.trades || 0) + " trades") + "</div></div>";
    if (c.lastSize) html += '<div>' + esc(money(c.lastSize) + " · " + (c.stackPct || 8) + "% of stack") + "</div>";
    else html += '<div class="muted">No ticket yet.</div>';
    var label = c.state === "filled" ? "✓ Filled" : (c.state === "executing" ? "Executing… " + (c.progress || 0) + "%" : "");
    html += '<div class="bar"><i style="width:' + esc(String(c.progress || 0)) + '%"></i></div>';
    html += '<div class="muted" style="margin-top:6px">' + esc(label) + "</div></div>";
    return html;
  }

  function renderRiskCard(a) {
    var c = a.card || {};
    var html = '<div class="card agent-card" id="cardRisk"><div class="agent-head">';
    html += '<div class="agent-name">Risk</div><div class="agent-sub">' +
      esc(c.guarding ? "Guarding" : (c.last || "Idle")) + "</div></div>";
    html += '<div class="pills"><span class="pill stop">stop -' + esc(String(c.stopPct != null ? c.stopPct : 6)) +
      '%</span><span class="pill trail">trail ' + esc(String(c.trailPct != null ? c.trailPct : 12)) +
      "%</span></div></div>";
    return html;
  }

  function renderAgents() {
    if (!S.state) return '<div class="card empty" id="emptyAgents">No agent data yet.</div>';
    var html = "";
    ["scanner", "auditor", "analyst", "executor", "risk"].forEach(function (name) {
      var a = agentCard(name);
      var rows = ((S.agents && S.agents[name] && S.agents[name].rows) || []);
      html += '<div class="card"><div class="agent-head"><div class="agent-name">' +
        name[0].toUpperCase() + name.slice(1) + '</div><div class="agent-sub">' +
        esc(a.status + " · " + (a.subtitle || "")) + "</div></div>";
      if (!rows.length) html += '<div class="muted">No decisions yet.</div>';
      rows.slice(0, 12).forEach(function (r) {
        html += '<button class="feed-row ' + (r.verdict === "kill" || r.verdict === "skipped" ? "kill" : "pass") +
          '" data-mint="' + esc(r.mint || "") + '"><span class="name">' +
          esc(r.name || r.mint || "") + '</span><span class="reason">' +
          esc(r.reason || r.verdict || "") + "</span></button>";
      });
      html += "</div>";
    });
    return html;
  }

  function renderActivity() {
    var rows = (S.activity && S.activity.rows) || [];
    if (!rows.length) return '<div class="card empty" id="emptyActivity">No activity yet.</div>';
    var html = '<div class="card">';
    rows.forEach(function (r) {
      html += '<button class="feed-row" data-mint="' + esc(r.mint || "") + '"><span><strong>' +
        esc(r.title) + '</strong><div class="muted">' + esc(r.subtitle || "") +
        "</div></span><span>" + esc(r.value || "") + " " + esc(r.value2 || "") + "</span></button>";
    });
    return html + "</div>";
  }

  function renderAccount() {
    var a = S.account, st = S.state;
    if (!a && !st) return '<div class="card empty" id="emptyAccount">No account data yet.</div>';
    a = a || {};
    var html = '<div class="card" id="accountCard">';
    html += '<div class="kicker">Account</div>';
    html += '<div class="row"><span class="lab">Mode</span><span>' + esc(a.mode || (st && st.mode)) + "</span></div>";
    html += '<div class="row"><span class="lab">Stack</span><span>' + esc(money(a.stack != null ? a.stack : st && st.balance)) + "</span></div>";
    html += '<div class="row"><span class="lab">Handle</span><span>' + esc(a.fomo_handle || "—") + "</span></div>";
    if (a.profile_url) html += '<div class="row"><span class="lab">Profile</span><span><a href="' +
      esc(a.profile_url) + '" target="_blank" rel="noopener">Open</a></span></div>';
    html += '<div class="row"><span class="lab">Analyst</span><span>' + esc(a.analyst_mode || "") + "</span></div>";
    html += '<div class="row"><span class="lab">Paused</span><span>' + esc(String(a.paused ? "yes" : "no")) + "</span></div>";
    html += "</div>";
    html += '<div class="card" id="pushRow"><div class="kicker">Pushes</div>';
    html += '<div>' + esc(a.pushNote || "No web push on plan B. Telegram is the only notifier.") + "</div></div>";
    if (a.ticket_warning) html += '<div class="card">' + esc(a.ticket_warning) + "</div>";
    html += '<div class="card"><div class="kicker">Settings (read only)</div>';
    html += '<div class="muted">Change these with Telegram commands.</div>';
    var settings = a.settings || {};
    Object.keys(settings).forEach(function (k) {
      var s = settings[k] || {};
      html += '<div class="row"><span class="lab">' + esc(k) + "</span><span>" + esc(String(s.value)) + "</span></div>";
    });
    html += "</div>";
    return html;
  }

  function renderCoin() {
    var c = S.coin, mint = S.coinMint;
    if (!c) return '<button class="back" id="backBtn">Back</button><div class="card" id="coinLoading">Loading coin…</div>';
    var html = '<button class="back" id="backBtn">Back</button>';
    html += '<div class="card" id="coinScreen"><div class="kicker">' + esc(c.symbol || "") + "</div>";
    html += "<h2 style='font-size:22px;text-transform:none;letter-spacing:0;color:var(--txt);margin:0 0 6px'>" +
      esc(c.name || mint) + "</h2>";
    html += '<div class="coin-price">' + esc(money(c.price, 6)) + "</div>";
    if (c.sinceEntryPct != null) html += '<div class="pnl ' + cls(c.sinceEntryPct) + '">' + esc(pct(c.sinceEntryPct)) + " since entry</div>";
    if (c.shield) html += '<div class="muted">Auditor ' + esc(c.shield) + "</div>";
    html += '<canvas class="chart" id="coinChart"></canvas>';
    if (c.exitLine != null) html += '<div class="muted">Orange dashed line: Risk trailing stop</div>';
    if (c.position) {
      var p = c.position;
      html += '<div class="row"><span class="lab">Size</span><span>' + esc(money(p.sizeUsd)) + "</span></div>";
      html += '<div class="row"><span class="lab">Entry</span><span>' + esc(money(p.entryPrice, 6)) + "</span></div>";
      html += '<div class="row"><span class="lab">Stop</span><span>' + esc(money(p.stopPrice, 6)) + "</span></div>";
    }
    if (c.holders != null) html += '<div class="row"><span class="lab">Holders</span><span>' + esc(String(c.holders)) + "</span></div>";
    if (c.coinUrl) html += '<a class="cta" href="' + esc(c.coinUrl) + '" target="_blank" rel="noopener">Open on FOMO</a>';
    html += '<button class="back" id="auditLink" style="display:block;margin-top:10px">Auditor layers</button>';
    html += "</div>";
    return html;
  }

  function renderAuditor() {
    var a = S.audit, mint = S.auditMint;
    var html = '<button class="back" id="backBtn">Back</button>';
    if (!a) return html + '<div class="card" id="auditorEmpty">No audit for this coin.</div>';
    html += '<div class="card" id="auditorScreen"><div class="kicker">Auditor</div>';
    html += "<h2 style='font-size:20px;text-transform:none;letter-spacing:0;color:var(--txt)'>" +
      esc(a.name || mint) + "</h2>";
    html += '<div class="disc">' + (a.passed ? "" : '<div class="beam"></div>') + "</div>";
    html += '<div class="layers">';
    (a.layers || []).forEach(function (l) {
      var clsL = "layer" + (l.pass === true ? " ok" : (l.pass === false ? " red" : ""));
      html += '<div class="' + clsL + '">' + esc(l.n + ". " + l.name) + "<br>" + esc(l.value || "not checked") + "</div>";
    });
    html += "</div>";
    html += '<div class="muted" style="margin-top:10px">' +
      esc(a.passed ? "8/8 → Executor" : (a.reason || "")) + "</div></div>";
    return html;
  }

  function render() {
    var screen = $("screen");
    if (!BASE) {
      screen.innerHTML = '<div class="card error" id="noKey">Open the desk link once (the one with the secret fragment). This browser will remember it.</div>';
      return;
    }
    var html;
    if (S.tab === "coin") html = renderCoin();
    else if (S.tab === "auditor") html = renderAuditor();
    else if (S.tab === "agents") html = renderAgents();
    else if (S.tab === "activity") html = renderActivity();
    else if (S.tab === "account") html = renderAccount();
    else html = renderHome();
    screen.innerHTML = html;
    bind();
    paint();
    var st = S.state || {};
    var badge = $("modeBadge");
    if (badge) {
      badge.textContent = st.mode || "PAPER";
      badge.className = "badge" + (st.mode === "LIVE" ? " live" : "");
    }
    var upd = $("updatedAt");
    if (upd && st.updatedAt) {
      upd.textContent = "Updated " + when(st.updatedAt) + (S.fixture ? " · fixture" : "");
    }
    var dot = $("liveDot");
    if (dot && S.lastOk) {
      dot.className = "dot on";
      dot.textContent = "live";
    }
  }

  function bind() {
    var back = $("backBtn");
    if (back) back.addEventListener("click", function () { S.tab = "home"; render(); });
    var al = $("auditLink");
    if (al) al.addEventListener("click", function () { openAuditor(S.coinMint); });
    document.querySelectorAll("[data-mint]").forEach(function (el) {
      el.addEventListener("click", function () {
        var mint = el.getAttribute("data-mint");
        if (mint) openCoin(mint);
      });
    });
  }

  function paint() {
    if (S.tab === "home" && S.chart && $("equityChart") && window.DeskChart) {
      window.DeskChart.drawLine($("equityChart"), S.chart.points || [], S.chart.marks || []);
    }
    if (S.tab === "coin" && S.coin && $("coinChart") && window.DeskChart) {
      window.DeskChart.drawCandles($("coinChart"), S.coin.candles || [], S.coin.exitLine);
    }
  }

  function openCoin(mint) {
    S.tab = "coin"; S.coinMint = mint; S.coin = null; render();
    if (!BASE) return;
    fetchJson("coin/" + mint + ".json", true).then(function (doc) {
      S.coin = doc || { mint: mint, name: mint };
      if (S.tab === "coin") render();
    });
  }

  function openAuditor(mint) {
    S.tab = "auditor"; S.auditMint = mint; S.audit = null; render();
    if (!BASE) return;
    fetchJson("audit/" + mint + ".json", true).then(function (doc) {
      S.audit = doc;
      if (S.tab === "auditor") render();
    });
  }

  function refreshOpen() {
    if (S.tab === "coin" && S.coinMint) {
      fetchJson("coin/" + S.coinMint + ".json", true).then(function (doc) {
        if (doc) { S.coin = doc; if (S.tab === "coin") render(); }
      });
    }
    if (S.tab === "auditor" && S.auditMint) {
      fetchJson("audit/" + S.auditMint + ".json", true).then(function (doc) {
        if (doc) { S.audit = doc; if (S.tab === "auditor") render(); }
      });
    }
  }

  document.querySelectorAll("#tabs [data-tab]").forEach(function (b) {
    b.addEventListener("click", function () {
      S.tab = b.getAttribute("data-tab");
      document.querySelectorAll("#tabs [data-tab]").forEach(function (x) {
        x.classList.toggle("on", x === b);
      });
      render();
    });
  });

  // Screenshot helpers: ?tab=account&empty=1 or ?error=1
  var params = new URLSearchParams(location.search);
  if (params.get("tab")) S.tab = params.get("tab");
  if (params.get("empty") === "1") {
    BASE = BASE || "d/fixture/";
    S.state = { balance: 200, todayPct: 0, todayUsd: 0, mode: "PAPER", agents: [], positions: [], updatedAt: Date.now() / 1000 };
    S.chart = { points: [], marks: [] };
    S.activity = { rows: [] };
    S.account = { mode: "PAPER", stack: 200, fomo_handle: "WillLarp", settings: {}, pushNote: "No web push on plan B. Telegram is the only notifier." };
    S.lastOk = Date.now();
    S.fixture = true;
  }
  if (params.get("error") === "1") {
    BASE = "d/does-not-exist-path-xxxxxxxxxxxxxxxxxxxx/";
    S.state = null;
    setTimeout(function () { $("offlineBar").hidden = false; }, 50);
  }

  render();
  if (BASE && params.get("empty") !== "1") poll();
  setInterval(function () { if (BASE && params.get("empty") !== "1" && params.get("error") !== "1") poll(); }, 15000);
})();
