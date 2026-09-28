/* Google Maps Lead Crawler - panel logic (vanilla JS, no build step).
 * Talks to the JSON API documented in docs/API.md. */
(() => {
  "use strict";

  const ACTIVE = new Set(["queued", "running", "enriching"]);
  const POLL_MS = 1500;
  const $ = (id) => document.getElementById(id);

  const els = {
    form: $("scrape-form"), business: $("business"), place: $("place"), queryPreview: $("query-preview"),
    wholeArea: $("whole-area"), progressArea: $("progress-area"), progressWeb: $("progress-web"),
    limit: $("limit"), limitHint: $("limit-hint"), contact: $("contact"), profiles: $("profiles"),
    profilesRow: $("profiles-row"), searchWeb: $("search-web"), searchWebRow: $("search-web-row"),
    filterHint: $("filter-hint"),
    enrich: $("enrich"), headed: $("headed"), startBtn: $("start-btn"), formError: $("form-error"),
    jobList: $("job-list"), refreshJobs: $("refresh-jobs"),
    progressCard: $("progress-card"), progressTitle: $("progress-title"), progressFilter: $("progress-filter"),
    statusPill: $("status-pill"),
    progressMessage: $("progress-message"), barFill: $("bar-fill"), progressCounts: $("progress-counts"),
    cancelBtn: $("cancel-btn"), jobError: $("job-error"), notice: $("notice"), noticeText: $("notice-text"),
    rowCount: $("row-count"), resultsSubtitle: $("results-subtitle"), filter: $("filter"),
    enrichAllBtn: $("enrich-all-btn"), copyBtn: $("copy-btn"), downloadBtn: $("download-btn"),
    downloadExtBtn: $("download-ext-btn"), body: $("results-body"), toast: $("toast"),
    health: $("health"), healthText: $("health-text"),
  };
  const websiteRadios = Array.from(document.querySelectorAll('input[name="website"]'));

  const state = { config: null, jobs: [], current: null, pollTimer: null, filter: "" };

  // ------------------------------------------------------------------
  // helpers
  // ------------------------------------------------------------------
  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (res.status === 204) return null;
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || res.statusText));
    }
    return data;
  }

  let toastTimer = null;
  function toast(message, isError = false) {
    els.toast.textContent = message;
    els.toast.className = "toast" + (isError ? " err" : "");
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { els.toast.hidden = true; }, isError ? 6000 : 3000);
  }

  const fmtDate = (iso) => iso ? new Date(iso).toLocaleString() : "";

  // "plumbers · Austin, TX" (free-text queries and jobs from before 1.2 have no business / place)
  const jobTitle = (job) => job.business ? (job.place ? `${job.business} · ${job.place}` : job.business) : job.query;
  const isFiltered = (job) => !!(job.filters && (job.filters.website !== "any" || job.filters.contact !== "any"));
  const searchesWeb = (job) => !!(job.filters && job.filters.website === "without" && job.filters.search_web);

  function cell(text, className, title) {
    const td = document.createElement("td");
    if (className) td.className = className;
    if (text === null || text === undefined || text === "") {
      const span = document.createElement("span");
      span.className = "missing";
      span.textContent = "—";
      td.appendChild(span);
    } else {
      td.textContent = String(text);
      td.title = title || String(text);
    }
    return td;
  }

  function linkCell(url, label) {
    const td = document.createElement("td");
    if (!url) return cell("");
    const a = document.createElement("a");
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    a.textContent = label || url.replace(/^https?:\/\/(www\.)?/, "");
    a.title = url;
    td.appendChild(a);
    return td;
  }

  // ------------------------------------------------------------------
  // boot
  // ------------------------------------------------------------------
  async function init() {
    try {
      state.config = await api("/api/config");
      els.limit.max = state.config.max_results;
      els.headed.checked = !state.config.headless;
      els.health.classList.add("ok");
      els.healthText.textContent = "API online";
    } catch (err) {
      els.health.classList.add("bad");
      els.healthText.textContent = "API offline";
      toast("Cannot reach the API: " + err.message, true);
    }
    await refreshJobs();
    const active = state.jobs.find((j) => ACTIVE.has(j.status)) || state.jobs[0];
    if (active) selectJob(active.id);
  }

  // ------------------------------------------------------------------
  // jobs list
  // ------------------------------------------------------------------
  async function refreshJobs() {
    try {
      state.jobs = await api("/api/jobs");
    } catch (err) {
      toast("Could not load jobs: " + err.message, true);
      return;
    }
    renderJobList();
  }

  function renderJobList() {
    els.jobList.innerHTML = "";
    if (!state.jobs.length) {
      const li = document.createElement("li");
      li.className = "muted"; li.textContent = "No jobs yet."; li.style.cursor = "default";
      els.jobList.appendChild(li);
      return;
    }
    for (const job of state.jobs) {
      const li = document.createElement("li");
      if (state.current && job.id === state.current.id) li.classList.add("active");
      const main = document.createElement("div");
      main.className = "job-main";
      const q = document.createElement("div"); q.className = "job-query"; q.textContent = jobTitle(job); q.title = job.query;
      const meta = document.createElement("div"); meta.className = "job-meta";
      const short = job.found !== null && job.found !== undefined && job.found < job.limit;
      const count = short ? `${job.found} of ${job.limit} found` : `${job.row_count} rows`;
      meta.textContent = `${job.status} · ${count} · ${fmtDate(job.created_at)}`;
      main.append(q);
      const chips = [isFiltered(job) ? job.filters.label : "", job.whole_area ? "whole area" : "",
        searchesWeb(job) ? "web search" : ""].filter(Boolean);
      for (const text of chips) {
        const chip = document.createElement("span");
        chip.className = "chip small" + (text === "whole area" || text === "web search" ? " area" : "");
        chip.textContent = text;
        main.append(chip, " ");
      }
      main.append(meta);
      const del = document.createElement("button");
      del.className = "del"; del.title = "Delete job"; del.textContent = "✕";
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete job "${job.query}"?`)) return;
        try {
          await api(`/api/jobs/${job.id}`, { method: "DELETE" });
          if (state.current && state.current.id === job.id) { state.current = null; stopPolling(); renderJob(null); }
          await refreshJobs();
        } catch (err) { toast(err.message, true); }
      });
      li.append(main, del);
      li.addEventListener("click", () => selectJob(job.id));
      els.jobList.appendChild(li);
    }
  }

  // ------------------------------------------------------------------
  // current job
  // ------------------------------------------------------------------
  async function selectJob(id) {
    stopPolling();
    try {
      state.current = await api(`/api/jobs/${id}`);
    } catch (err) {
      toast(err.message, true);
      return;
    }
    renderJob(state.current);
    renderJobList();
    if (ACTIVE.has(state.current.status)) startPolling();
    onJobSelected();
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(async () => {
      if (!state.current) return stopPolling();
      try {
        const job = await api(`/api/jobs/${state.current.id}`);
        const wasActive = ACTIVE.has(state.current.status);
        state.current = job;
        renderJob(job);
        if (!ACTIVE.has(job.status)) {
          stopPolling();
          if (wasActive) {
            const done = job.notice ? `Finished: ${job.stats.matched} of ${job.limit} found`
              : `Finished: ${job.rows.length} rows`;
            toast(job.status === "completed" ? done : `Job ${job.status}`, job.status === "failed");
            refreshJobs();
          }
        }
      } catch (err) {
        toast(err.message, true);
        stopPolling();
      }
    }, POLL_MS);
  }

  function stopPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  function renderJob(job) {
    if (!job) {
      els.progressCard.hidden = true;
      els.notice.hidden = true;
      els.rowCount.textContent = "0";
      els.resultsSubtitle.textContent = "Start a search or pick a job from the history.";
      els.body.innerHTML = '<tr class="empty"><td colspan="13">No rows yet.</td></tr>';
      setToolbar(false, false);
      return;
    }
    const active = ACTIVE.has(job.status);
    const filtered = isFiltered(job);
    els.progressCard.hidden = false;
    els.progressTitle.textContent = `“${jobTitle(job)}” · ${job.limit} wanted`;
    els.progressFilter.hidden = !filtered;
    els.progressFilter.textContent = filtered ? job.filters.label : "";
    els.progressArea.hidden = !job.whole_area;
    els.progressWeb.hidden = !searchesWeb(job);
    els.statusPill.textContent = job.status;
    els.statusPill.className = "pill " + job.status;
    els.progressMessage.textContent = job.progress.message || job.status;
    const pct = active ? job.progress.percent : (job.status === "completed" ? 100 : job.progress.percent);
    els.barFill.style.width = `${pct || 0}%`;
    const scanning = filtered && (job.progress.phase === "collecting" || job.progress.phase === "details");
    els.progressCounts.textContent = !job.progress.total ? job.progress.phase
      : scanning ? `${job.progress.current} of ${job.progress.total} matches found`
        : `${job.progress.phase}: ${job.progress.current} / ${job.progress.total}`;
    els.cancelBtn.hidden = !active;
    els.jobError.hidden = !job.error;
    els.jobError.textContent = job.error || "";
    // why fewer results than asked for ("Only 35 businesses found for ...")
    els.notice.hidden = !job.notice;
    els.noticeText.textContent = job.notice || "";

    els.rowCount.textContent = job.rows.length;
    const places = new Set(job.rows.map((r) => r.rank)).size;
    const withEmail = job.rows.filter((r) => r.email).length;
    const parts = [`${places} places`, `${job.rows.length} rows`, `${withEmail} with email`];
    const stats = job.stats || {};
    if ((filtered || job.whole_area) && stats.links_found) {
      const skipped = Object.entries(stats.skipped || {}).sort((a, b) => b[1] - a[1])
        .map(([reason, n]) => `${n} ${reason}`).join(", ");
      const sections = stats.sections ? ` in ${stats.sections} map sections` : "";
      parts.push(`${stats.links_found} listings scanned${sections}${skipped ? ` (skipped: ${skipped})` : ""}`);
    }
    if (stats.web_searches) parts.push(`${stats.web_searches} web searches`);
    parts.push(`created ${fmtDate(job.created_at)}`);
    els.resultsSubtitle.textContent = parts.join(" · ");
    renderRows(job);
    setToolbar(job.rows.length > 0, active);
    els.startBtn.disabled = false;
  }

  function setToolbar(hasRows, active) {
    els.filter.disabled = !hasRows;
    els.copyBtn.disabled = !hasRows;
    els.downloadBtn.setAttribute("aria-disabled", hasRows ? "false" : "true");
    els.downloadExtBtn.setAttribute("aria-disabled", hasRows ? "false" : "true");
    if (state.current) {
      els.downloadBtn.href = `/api/jobs/${state.current.id}/export.csv`;
      els.downloadExtBtn.href = `/api/jobs/${state.current.id}/export.csv?extended=true`;
    }
    const missing = state.current ? state.current.rows.some((r) => r.website && (!r.email || !r.phone)) : false;
    els.enrichAllBtn.disabled = !hasRows || active || !missing;
    els.enrichAllBtn.title = active ? "Wait for the job to finish"
      : (!missing ? "Every row with a website already has an email and phone" : "Look up emails/phones on the website of every row that is missing one");
  }

  function renderRows(job) {
    els.body.innerHTML = "";
    const filter = state.filter.trim().toLowerCase();
    const rows = job.rows.filter((r) => !filter || Object.values(r).some((v) => v !== null && String(v).toLowerCase().includes(filter)));
    if (!rows.length) {
      const why = job.rows.length ? "No rows match the filter."
        : ACTIVE.has(job.status) ? "Collecting results…" : isFiltered(job) ? "No matching businesses." : "No rows.";
      els.body.innerHTML = `<tr class="empty"><td colspan="13">${why}</td></tr>`;
      return;
    }
    const active = ACTIVE.has(job.status);
    let previousRank = null;
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.dataset.rowId = row.row_id;
      if (row.rank === previousRank) tr.classList.add("dup");
      previousRank = row.rank;

      tr.appendChild(cell(row.rank, "num"));
      const nameTd = cell(row.name, "name");
      if (row.enrichment_status) {
        const tag = document.createElement("span");
        tag.className = "tag " + row.enrichment_status;
        tag.textContent = row.enrichment_status.replace("_", " ");
        tag.title = row.enrichment_note || "";
        nameTd.appendChild(tag);
      }
      tr.appendChild(nameTd);
      tr.appendChild(cell(row.city));
      tr.appendChild(cell(row.state));
      tr.appendChild(cell(row.country));
      const emailTd = row.email ? linkCell("mailto:" + row.email, row.email) : cell("");
      if (row.email_source) emailTd.title = "found on " + row.email_source;
      tr.appendChild(emailTd);
      const siteTd = linkCell(row.website);
      siteTd.classList.add("site");
      if (row.website_type === "profile") {
        const tag = document.createElement("span"); tag.className = "tag profile"; tag.textContent = "profile";
        tag.title = "A social / directory / booking page, not the business's own website";
        siteTd.appendChild(tag);
      }
      tr.appendChild(siteTd);
      const phoneTd = cell(row.phone);
      if (row.phone_source === "website") {
        const tag = document.createElement("span"); tag.className = "tag website"; tag.textContent = "site"; tag.title = "phone found on the website";
        phoneTd.appendChild(tag);
      }
      tr.appendChild(phoneTd);
      tr.appendChild(cell(row.phone_unformatted, "mono"));
      tr.appendChild(linkCell(row.url, "maps link"));
      tr.appendChild(cell(row.review_count, "num"));
      const descTd = cell(row.description, "wrap");
      if (row.description) {
        descTd.textContent = "";
        const clamp = document.createElement("div");
        clamp.className = "clamp";
        clamp.textContent = row.description;
        descTd.appendChild(clamp);
        descTd.title = row.description;
      }
      tr.appendChild(descTd);

      const actions = document.createElement("td");
      const needs = !row.email || !row.phone;
      if (needs && row.website) {
        const btn = document.createElement("button");
        btn.className = "btn tiny";
        btn.textContent = row.enrichment_status === "pending" ? "Searching…" : "✉ Find";
        btn.title = "Search this website for an email address / phone number";
        btn.disabled = active || row.enrichment_status === "pending";
        btn.addEventListener("click", () => enrichRows([row.row_id], row.enrichment_status === "not_found"));
        actions.appendChild(btn);
      } else if (needs) {
        actions.appendChild(cell("").firstChild);
        actions.title = "No website to search";
        actions.firstChild.textContent = "no website";
      } else {
        actions.textContent = "✓";
        actions.title = "Email and phone present";
      }
      tr.appendChild(actions);
      els.body.appendChild(tr);
    }
  }

  // ------------------------------------------------------------------
  // search form
  // ------------------------------------------------------------------
  const FILTERS_KEY = "searchFilters";
  const SLOW_EMAIL = "Google Maps doesn't show emails, so each candidate's website or social page is checked " +
    "while scanning (slower).";
  const CONTACT_HINTS = {
    phone: "The phone number shown on Google Maps.",
    email: SLOW_EMAIL,
    phone_or_email: "Most listings show a phone. For those that don't, their website or social page is " +
      "checked for an email.",
    phone_and_email: SLOW_EMAIL,
  };

  function currentFilters() {
    const website = (websiteRadios.find((r) => r.checked) || { value: "any" }).value;
    // search_web only applies to "No website" (the server ignores it otherwise)
    return { website, contact: els.contact.value, profiles_as_no_website: els.profiles.checked,
      search_web: els.searchWeb.checked };
  }

  function updateFilterUi() {
    const f = currentFilters();
    els.profilesRow.hidden = f.website === "any";
    els.searchWebRow.hidden = f.website !== "without";
    let hint = CONTACT_HINTS[f.contact] || "";
    if (f.website === "without" && (f.contact === "email" || f.contact === "phone_and_email")) {
      hint += " Businesses without a website rarely publish an email, so expect few results.";
    }
    if (f.website === "without") {
      hint += " A business is left out when its email address or social page leads to a website of its own" +
        (f.search_web ? ", or a web search for its name or phone number finds one (a few seconds per business)."
          : ".");
    }
    hint = hint.trim();
    els.filterHint.textContent = hint;
    els.filterHint.hidden = !hint;
    try { localStorage.setItem(FILTERS_KEY, JSON.stringify(f)); } catch { /* private mode etc. */ }
  }

  function restoreFilters() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(FILTERS_KEY) || "null"); } catch { saved = null; }
    if (!saved || typeof saved !== "object") return;
    const radio = websiteRadios.find((r) => r.value === saved.website);
    if (radio) radio.checked = true;
    if (Array.from(els.contact.options).some((o) => o.value === saved.contact)) els.contact.value = saved.contact;
    if (typeof saved.profiles_as_no_website === "boolean") els.profiles.checked = saved.profiles_as_no_website;
    if (typeof saved.search_web === "boolean") els.searchWeb.checked = saved.search_web;
  }

  // "Katy, TX; Pasadena, TX" -> ["Katy, TX", "Pasadena, TX"] (same rule as the server)
  const splitPlaces = (text) => Array.from(new Set(
    text.split(/[;\n]/).map((p) => p.trim().replace(/\s+/g, " ")).filter(Boolean)));

  function updateQueryPreview() {
    const business = els.business.value.trim();
    const places = splitPlaces(els.place.value);
    let text = "";
    if (business && places.length && els.wholeArea.checked) {
      text = `Searches “${business}” across all of ${places.join(" · ")}, map section by section`;
    } else if (business && places.length) {
      text = "Searches Google Maps for " + places.map((p) => `“${business} in ${p}”`).join(", ");
    } else if (business) {
      text = `Searches Google Maps for “${business}”`;
    }
    els.queryPreview.hidden = !text;
    els.queryPreview.textContent = text;
    els.limitHint.textContent = `1 – ${state.config ? state.config.max_results : 500} businesses. ` +
      (els.wholeArea.checked ? "The search goes on until it has found that many or covered the whole area."
        : "Google Maps lists at most about 120 per search.");
  }

  function restoreWholeArea() {
    try {
      const saved = localStorage.getItem("wholeArea");
      if (saved !== null) els.wholeArea.checked = saved === "1";
    } catch { /* private mode etc. */ }
  }

  els.business.addEventListener("input", updateQueryPreview);
  els.place.addEventListener("input", updateQueryPreview);
  els.wholeArea.addEventListener("change", () => {
    try { localStorage.setItem("wholeArea", els.wholeArea.checked ? "1" : "0"); } catch { /* ignore */ }
    updateQueryPreview();
  });
  websiteRadios.forEach((r) => r.addEventListener("change", updateFilterUi));
  els.contact.addEventListener("change", updateFilterUi);
  els.profiles.addEventListener("change", updateFilterUi);
  els.searchWeb.addEventListener("change", updateFilterUi);

  // ------------------------------------------------------------------
  // actions
  // ------------------------------------------------------------------
  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    els.formError.hidden = true;
    const payload = {
      business: els.business.value.trim(),
      place: els.place.value.trim(),
      whole_area: els.wholeArea.checked,
      limit: Number(els.limit.value),
      filters: currentFilters(),
      enrich: els.enrich.checked,
      headless: !els.headed.checked,
    };
    els.startBtn.disabled = true;
    els.startBtn.textContent = "Starting…";
    try {
      const summary = await api("/api/scrape", { method: "POST", body: JSON.stringify(payload) });
      toast(`Started: “${summary.query}”`);
      await refreshJobs();
      selectJob(summary.id);
    } catch (err) {
      els.formError.textContent = err.message;
      els.formError.hidden = false;
    } finally {
      els.startBtn.disabled = false;
      els.startBtn.textContent = "Search";
    }
  });

  els.cancelBtn.addEventListener("click", async () => {
    if (!state.current) return;
    try {
      await api(`/api/jobs/${state.current.id}/cancel`, { method: "POST" });
      toast("Cancelling…");
    } catch (err) { toast(err.message, true); }
  });

  async function enrichRows(rowIds, force) {
    if (!state.current) return;
    try {
      await api(`/api/jobs/${state.current.id}/enrich`, {
        method: "POST",
        body: JSON.stringify({ row_ids: rowIds, force: !!force }),
      });
      toast(rowIds ? "Searching the website…" : "Searching websites for missing emails / phones…");
      await selectJob(state.current.id);
    } catch (err) { toast(err.message, true); }
  }

  els.enrichAllBtn.addEventListener("click", () => enrichRows(null, false));

  els.copyBtn.addEventListener("click", async () => {
    if (!state.current) return;
    try {
      const res = await fetch(`/api/jobs/${state.current.id}/export.csv`);
      const text = (await res.text()).replace(/^﻿/, "");
      await navigator.clipboard.writeText(text);
      toast(`Copied ${state.current.rows.length} rows as CSV`);
    } catch (err) { toast("Copy failed: " + err.message, true); }
  });

  els.filter.addEventListener("input", () => {
    state.filter = els.filter.value;
    if (state.current) renderRows(state.current);
  });

  els.refreshJobs.addEventListener("click", refreshJobs);

  // ------------------------------------------------------------------
  // live logs (polls /api/logs with a sequence cursor)
  // ------------------------------------------------------------------
  const LOG_POLL_MS = 1200;
  const LOG_MAX_LINES = 1500;
  const lels = {
    card: $("logs-card"), toggle: $("logs-toggle"), count: $("log-count"), view: $("log-view"),
    empty: $("log-empty"), level: $("log-level"), jobOnly: $("log-job-only"), autoscroll: $("log-autoscroll"),
    pause: $("log-pause"), clear: $("log-clear"), download: $("log-download"),
  };
  const logs = { cursor: null, paused: false, collapsed: false, timer: null, inFlight: false, lines: 0,
                 lastJobId: null, generation: 0 };

  const fmtTime = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleTimeString([], { hour12: false });
  };

  function logQuery() {
    const params = new URLSearchParams();
    if (logs.cursor !== null) params.set("since", String(logs.cursor));
    params.set("limit", logs.cursor === null ? "300" : "500");
    if (lels.level.value) params.set("level", lels.level.value);
    if (lels.jobOnly.checked && state.current) params.set("job_id", state.current.id);
    return `/api/logs?${params.toString()}`;
  }

  function isAtBottom() {
    return lels.view.scrollHeight - lels.view.scrollTop - lels.view.clientHeight < 8;
  }

  function appendLogEntry(entry) {
    const line = document.createElement("div");
    line.className = `log-line level-${entry.level}`;
    if (state.current && entry.job_id && entry.job_id !== state.current.id) line.classList.add("other-job");
    line.dataset.seq = entry.seq;
    const ts = document.createElement("span"); ts.className = "log-ts"; ts.textContent = fmtTime(entry.ts);
    const level = document.createElement("span"); level.className = "log-level"; level.textContent = entry.level;
    const logger = document.createElement("span"); logger.className = "log-logger";
    logger.textContent = entry.logger.replace(/^app\./, "");
    logger.title = entry.job_id ? `${entry.logger} · job ${entry.job_id}` : entry.logger;
    const msg = document.createElement("span"); msg.className = "log-msg"; msg.textContent = entry.message;
    line.append(ts, level, logger, msg);
    lels.view.appendChild(line);
    logs.lines += 1;
  }

  function appendGapNotice(text) {
    const gap = document.createElement("div");
    gap.className = "log-gap"; gap.textContent = text;
    lels.view.appendChild(gap);
  }

  function trimLogView() {
    while (logs.lines > LOG_MAX_LINES && lels.view.firstElementChild) {
      const first = lels.view.firstElementChild;
      if (first.classList.contains("log-line")) logs.lines -= 1;
      first.remove();
    }
  }

  function resetLogView() {
    lels.view.innerHTML = "";
    logs.lines = 0;
    logs.cursor = null;
    logs.generation += 1;
    lels.count.textContent = "0";
  }

  async function pollLogs() {
    if (logs.paused || logs.collapsed || logs.inFlight) return;
    logs.inFlight = true;
    const generation = logs.generation;
    try {
      const wasInitial = logs.cursor === null;
      const data = await api(logQuery());
      if (generation !== logs.generation) {
        // filters changed while this request was in flight - discard and fetch again
        logs.inFlight = false;
        return pollLogs();
      }
      const stick = lels.autoscroll.checked || wasInitial;
      if (wasInitial) { lels.view.innerHTML = ""; logs.lines = 0; }
      if (!wasInitial && data.first_seq > logs.cursor + 1 && data.entries.length) {
        appendGapNotice("… older lines dropped (buffer full) …");
      }
      for (const entry of data.entries) appendLogEntry(entry);
      if (!lels.view.children.length) {
        lels.view.appendChild(lels.empty);
        lels.empty.textContent = lels.level.value || lels.jobOnly.checked ? "No log lines match the current filter." : "Waiting for log lines…";
      } else if (lels.empty.parentNode === lels.view) {
        lels.empty.remove();
      }
      trimLogView();
      logs.cursor = data.next;
      lels.count.textContent = String(logs.lines);
      if (stick) lels.view.scrollTop = lels.view.scrollHeight;
      if (data.truncated) { logs.inFlight = false; return pollLogs(); }
    } catch (err) {
      // the server may be restarting - keep polling quietly
    } finally {
      logs.inFlight = false;
    }
  }

  function startLogPolling() {
    if (logs.timer) clearInterval(logs.timer);
    logs.timer = setInterval(pollLogs, LOG_POLL_MS);
    pollLogs();
  }

  function setLogsCollapsed(collapsed) {
    logs.collapsed = collapsed;
    lels.card.classList.toggle("collapsed", collapsed);
    lels.toggle.textContent = collapsed ? "▸" : "▾";
    lels.toggle.setAttribute("aria-expanded", String(!collapsed));
    try { localStorage.setItem("logsCollapsed", collapsed ? "1" : "0"); } catch { /* private mode etc. */ }
    if (!collapsed) pollLogs();
  }

  function setLogsPaused(paused) {
    logs.paused = paused;
    lels.pause.classList.toggle("paused", paused);
    lels.pause.innerHTML = paused ? "&#9654; Resume" : "&#10074;&#10074; Pause";
    lels.pause.title = paused ? "Continue fetching new lines" : "Stop fetching new lines";
    if (!paused) pollLogs();
  }

  function refilterLogs() {
    resetLogView();
    pollLogs();
  }

  lels.toggle.addEventListener("click", () => setLogsCollapsed(!logs.collapsed));
  lels.pause.addEventListener("click", () => setLogsPaused(!logs.paused));
  lels.clear.addEventListener("click", () => {
    lels.view.innerHTML = "";
    logs.lines = 0;
    lels.count.textContent = "0";
  });
  lels.level.addEventListener("change", refilterLogs);
  lels.jobOnly.addEventListener("change", refilterLogs);
  lels.view.addEventListener("scroll", () => {
    // scrolling up pauses auto-scroll; scrolling back to the bottom re-enables it
    lels.autoscroll.checked = isAtBottom();
  });
  lels.download.addEventListener("click", () => {
    const text = Array.from(lels.view.querySelectorAll(".log-line"))
      .map((l) => Array.from(l.children).map((c) => c.textContent).join("  "))
      .join("\n");
    const blob = new Blob([text + "\n"], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `crawler-logs-${new Date().toISOString().replace(/[:.]/g, "-")}.txt`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  // called by selectJob(): when the filter is on, reload the view for the newly selected job
  function onJobSelected() {
    const id = state.current ? state.current.id : null;
    if (lels.jobOnly.checked && id !== logs.lastJobId) refilterLogs();
    logs.lastJobId = id;
  }

  function initLogs() {
    let collapsed = false;
    try { collapsed = localStorage.getItem("logsCollapsed") === "1"; } catch { /* ignore */ }
    setLogsCollapsed(collapsed);
    startLogPolling();
  }

  restoreFilters();
  restoreWholeArea();
  updateFilterUi();
  updateQueryPreview();
  init().then(() => { updateQueryPreview(); initLogs(); });
})();
