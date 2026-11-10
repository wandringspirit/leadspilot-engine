/* Day by day (Autopilot view): emails sent each day - first emails and follow-ups - and what came back.
   Data: /api/master/daily (app/outreach/master.py: daily_table). Plain SVG, no library. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const card = $("daily-card");
  if (!card) return;

  // series: [column in the data, label, colour slot] - colours follow the series, never their rank
  const SENT = [["First emails", "First emails", 1], ["Follow-ups", "Follow-ups", 2]];
  const BACK = [["Replies", "Replies", 3], ["Websites opened", "Websites opened", 4],
                ["Bounces", "Bounces", 5], ["Unsubscribes", "Unsubscribes", 6]];
  const SVG = "http://www.w3.org/2000/svg";
  const PAD = { top: 10, right: 8, bottom: 22, left: 30 };
  const state = { columns: [], rows: [], range: 30, limit: 0, hover: -1, table: false, loaded: 0 };

  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const html = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const parseDay = (iso) => new Date(iso + "T12:00:00");
  const dayLabel = (iso) => parseDay(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const longDay = (iso) => parseDay(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const weekend = (iso) => [0, 6].includes(parseDay(iso).getDay());

  function shown() {
    const rows = state.rows.map((r) => Object.fromEntries(state.columns.map((c, i) => [c, r[i]])));
    return state.range ? rows.slice(-state.range) : rows;
  }

  function niceMax(value) {
    if (value <= 4) return 4;
    const step = value <= 10 ? 2 : value <= 25 ? 5 : 10;
    return Math.ceil(value / step) * step;
  }

  // one stacked bar chart: a column per day, 2px gaps between segments, 4px rounded top on the topmost one
  function chart(box, rows, series, height, { limit = 0 } = {}) {
    box.textContent = "";
    const width = Math.max(box.clientWidth, 280);
    const plotW = width - PAD.left - PAD.right, plotH = height - PAD.top - PAD.bottom;
    const totals = rows.map((r) => series.reduce((sum, [col]) => sum + (r[col] || 0), 0));
    const top = niceMax(Math.max(...totals, limit, 1));
    const y = (v) => PAD.top + plotH - (v / top) * plotH;
    const band = plotW / Math.max(rows.length, 1);
    const barW = Math.max(2, Math.min(18, band * 0.62));
    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
                              "aria-label": `${series.map((s) => s[1]).join(" and ")} per day` });

    rows.forEach((r, i) => {                                   // weekends: no emails go out then
      if (weekend(r.Day)) root.appendChild(svg("rect", { x: PAD.left + i * band, y: PAD.top, width: band,
                                                         height: plotH, class: "viz-weekend" }));
    });
    const step = top <= 4 ? 1 : top / 4;
    for (let v = 0; v <= top + 0.001; v += step) {             // recessive grid and y labels
      root.appendChild(svg("line", { x1: PAD.left, x2: width - PAD.right, y1: y(v), y2: y(v),
                                     class: v === 0 ? "viz-base" : "viz-grid" }));
      const label = svg("text", { x: PAD.left - 6, y: y(v) + 4, class: "viz-axis", "text-anchor": "end" });
      label.textContent = String(Math.round(v));
      root.appendChild(label);
    }
    const every = Math.ceil(rows.length / Math.max(1, Math.floor(plotW / 54)));   // x labels that fit
    rows.forEach((r, i) => {
      if ((rows.length - 1 - i) % every) return;
      const label = svg("text", { x: PAD.left + i * band + band / 2, y: height - 6, class: "viz-axis",
                                  "text-anchor": "middle" });
      label.textContent = dayLabel(r.Day);
      root.appendChild(label);
    });
    const hover = svg("rect", { y: PAD.top, height: plotH, width: band, class: "viz-hover", visibility: "hidden" });
    root.appendChild(hover);

    rows.forEach((r, i) => {
      const x = PAD.left + i * band + (band - barW) / 2;
      const parts = series.map(([col, , slot]) => [r[col] || 0, slot]).filter(([v]) => v > 0);
      let base = 0;
      parts.forEach(([v, slot], k) => {
        const y0 = y(base), y1 = y(base + v);
        const gap = k > 0 ? 2 : 0;                             // 2px surface gap between stacked segments
        const h = Math.max(1, y0 - y1 - gap);
        const yTop = y1, r4 = k === parts.length - 1 ? Math.min(4, h, barW / 2) : 0;
        root.appendChild(svg("path", { class: `viz-s${slot}`, d: r4
          ? `M${x},${yTop + h} V${yTop + r4} Q${x},${yTop} ${x + r4},${yTop} H${x + barW - r4} ` +
            `Q${x + barW},${yTop} ${x + barW},${yTop + r4} V${yTop + h} Z`
          : `M${x},${yTop} h${barW} v${h} h${-barW} Z` }));
        base += v;
      });
    });
    if (limit) {                                               // the daily limit, as a reference line
      root.appendChild(svg("line", { x1: PAD.left, x2: width - PAD.right, y1: y(limit), y2: y(limit), class: "viz-limit" }));
      const label = svg("text", { x: width - PAD.right - 2, y: y(limit) - 4, class: "viz-axis", "text-anchor": "end" });
      label.textContent = `daily limit ${limit}`;
      root.appendChild(label);
    }
    // hit areas: the whole column, wider than the bar
    const hits = svg("rect", { x: PAD.left, y: PAD.top, width: plotW, height: plotH, fill: "transparent" });
    hits.addEventListener("mousemove", (e) => {
      const rect = root.getBoundingClientRect();
      const i = Math.floor(((e.clientX - rect.left) * (width / rect.width) - PAD.left) / band);
      setHover(Math.max(0, Math.min(rows.length - 1, i)), e);
    });
    hits.addEventListener("mouseleave", () => setHover(-1));
    root.appendChild(hits);
    box.appendChild(root);
    return { hover, band };
  }

  let drawn = [];
  function setHover(i, event) {
    state.hover = i;
    const rows = shown(), tip = $("daily-tip");
    for (const d of drawn) {
      d.hover.setAttribute("visibility", i < 0 ? "hidden" : "visible");
      if (i >= 0) d.hover.setAttribute("x", PAD.left + i * d.band);
    }
    if (i < 0 || !rows[i]) { tip.hidden = true; return; }
    const r = rows[i];
    tip.textContent = "";
    tip.appendChild(html("div", "viz-tip-day", longDay(r.Day) + (weekend(r.Day) ? " · weekend" : "")));
    for (const [col, label, slot] of [...SENT, ...BACK]) {
      const line = html("div", "viz-tip-row" + (r[col] ? "" : " zero"));
      line.append(html("i", `viz-key viz-s${slot}`), html("span", "", label), html("b", "", String(r[col] || 0)));
      tip.appendChild(line);
    }
    tip.hidden = false;
    const wrap = $("daily-charts").getBoundingClientRect();
    const x = event ? event.clientX - wrap.left : PAD.left + i * drawn[0].band;
    const left = x + 14 + tip.offsetWidth > wrap.width ? x - tip.offsetWidth - 14 : x + 14;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${event ? Math.max(0, event.clientY - wrap.top - 20) : 10}px`;
  }

  function legend(id, series) {
    const box = $(id);
    box.textContent = "";
    for (const [, label, slot] of series) {
      const item = html("span", "viz-legend-item");
      item.append(html("i", `viz-key viz-s${slot}`), label);
      box.appendChild(item);
    }
  }

  function stats(rows) {
    const sum = (col) => rows.reduce((s, r) => s + (r[col] || 0), 0);
    const box = $("daily-stats");
    box.textContent = "";
    const items = [["Emails sent", sum("First emails") + sum("Follow-ups")], ["First emails", sum("First emails")],
                   ["Follow-ups", sum("Follow-ups")], ["Replies", sum("Replies")],
                   ["Websites opened", sum("Websites opened")], ["Bounces", sum("Bounces")]];
    for (const [label, value] of items) {
      const stat = html("div", "stat");
      stat.append(html("strong", "", String(value)), html("span", "muted", label));
      box.appendChild(stat);
    }
  }

  function table(rows) {
    const t = $("daily-table");
    t.textContent = "";
    const head = html("tr");
    state.columns.forEach((c, i) => head.appendChild(html("th", i ? "num" : "", c)));
    t.appendChild(html("thead")).appendChild(head);
    const body = t.appendChild(html("tbody"));
    for (const r of [...rows].reverse()) {
      const tr = html("tr", weekend(r.Day) ? "weekend" : "");
      state.columns.forEach((c, i) => tr.appendChild(html("td", i ? "num" : "", i ? String(r[c]) : longDay(r.Day))));
      body.appendChild(tr);
    }
  }

  function render() {
    const rows = shown();
    const empty = !rows.length;
    $("daily-charts").hidden = empty || state.table;
    $("daily-table-box").hidden = empty || !state.table;
    if (empty) {
      $("daily-stats").textContent = "No emails sent yet.";
      return;
    }
    stats(rows);
    legend("daily-legend-sent", SENT);
    legend("daily-legend-back", BACK);
    if (state.table) table(rows);
    else drawn = [chart($("daily-sent"), rows, SENT, 170, { limit: state.limit }), chart($("daily-back"), rows, BACK, 120)];
  }

  async function refresh() {
    if (card.hidden || Date.now() - state.loaded < 20000) return render();
    try {
      const [daily, outreach] = await Promise.all([fetch("/api/master/daily").then((r) => r.json()),
                                                   fetch("/api/outreach").then((r) => r.json())]);
      state.columns = daily.columns;
      state.rows = daily.rows;
      state.limit = outreach.daily_limit || 0;
      state.loaded = Date.now();
    } catch (err) {
      return;                                                  // the panel is restarting - try again later
    }
    render();
  }

  for (const input of card.querySelectorAll('input[name="daily-range"]')) {
    input.addEventListener("change", () => { state.range = Number(input.value); setHover(-1); render(); });
  }
  $("daily-table-btn").addEventListener("click", (e) => {
    state.table = !state.table;
    e.currentTarget.setAttribute("aria-pressed", String(state.table));
    e.currentTarget.textContent = state.table ? "Chart" : "Table";
    render();
  });
  let resizeTimer = 0, lastWidth = 0;                          // redraw when the card's width changes (or it shows)
  new ResizeObserver(([entry]) => {
    const w = Math.round(entry.contentRect.width);
    if (!w || w === lastWidth) return;
    lastWidth = w;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(render, 100);
  }).observe(card);
  setInterval(() => { if (!card.hidden && !document.hidden) refresh(); }, 60000);

  window.LeadsDaily = { refresh: () => { state.loaded = 0; return refresh(); } };
  refresh();
})();
