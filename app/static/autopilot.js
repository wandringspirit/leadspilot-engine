/* Leads Pilot Engine - Outreach tab: the autopilot (it searches the next place and starts its campaign by
 * itself) and the activity log with the master sheet (vanilla JS, no build step). Talks to /api/autopilot,
 * /api/activity and /api/drive (docs/API.md, docs/AUTOPILOT.md). outreach.js refreshes it while the tab is open. */
(() => {
  "use strict";

  const { api, toast } = window.panel;
  const $ = (id) => document.getElementById(id);
  const ACTIVITY_SHOWN = 40;

  const els = {
    state: $("ap-state"), toggle: $("ap-toggle"), editBtn: $("ap-edit-btn"), summary: $("ap-summary"),
    stopped: $("ap-stopped"), stoppedText: $("ap-stopped-text"), stats: $("ap-stats"), form: $("ap-form"),
    type: $("ap-type"), limit: $("ap-limit"), min: $("ap-min"), perDay: $("ap-per-day"), places: $("ap-places"),
    next: $("ap-next"), cancel: $("ap-cancel"), runs: $("ap-runs"), activity: $("activity-list"),
    masterNote: $("master-note"), masterLinks: $("master-links"),
  };
  let status = null;
  let editing = false;
  let types = [];             // business types, for the form

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  const when = (iso) => iso ? new Date(iso).toLocaleString([], {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const patch = (body) => api("/api/autopilot", { method: "PATCH", body: JSON.stringify(body) });

  // ------------------------------------------------------------------
  // the autopilot
  // ------------------------------------------------------------------
  function render() {
    const s = status;
    const on = s.enabled && !s.stopped;
    els.state.textContent = s.stopped ? "stopped" : s.enabled ? (s.searching ? "searching" : "on") : "off";
    els.state.className = "pill " + (s.stopped ? "paused" : on ? "running" : "draft");
    els.toggle.textContent = on ? "Switch off" : "Switch on";
    els.toggle.classList.toggle("primary", !on);
    const type = s.business_type_name || "—";
    els.summary.textContent = !s.enabled
      ? `Off. Switched on, it searches ${type} businesses place by place and starts each place's campaign ` +
        `whenever fewer than ${s.min_waiting} businesses wait for their first email.`
      : s.searching ? `Searching ${s.searching} for ${type} businesses – its campaign starts when the search is done.`
      : `${type}: next place ${s.next || "– none left"}. Not searching now: ${s.holding || "…"}.`;
    els.stopped.hidden = !s.stopped;
    els.stoppedText.textContent = s.stopped ? s.stopped + " – switching it on again clears this." : "";
    const searched = Math.min(s.next_place, s.places.length);
    const stats = [["waiting for a first email", s.waiting], ["searches today", `${s.searches_today} of ${s.searches_per_day}`],
      ["places searched", `${searched} of ${s.places.length}`]];
    els.stats.innerHTML = "";
    for (const [label, value] of stats) {
      const stat = el("div", "stat");
      stat.append(el("strong", "", String(value)), el("span", "muted", label));
      els.stats.appendChild(stat);
    }
    els.runs.innerHTML = "";
    if (!s.runs.length) els.runs.appendChild(el("li", "muted", "No searches yet."));
    for (const run of s.runs) {
      const li = el("li");
      li.append(el("span", "activity-when", when(run.started_at)), el("strong", "", run.place));
      const parts = [];
      if (run.finished_at) parts.push(`${run.found} found`);
      if (run.emailing) parts.push(`${run.emailing} to email`);
      parts.push(run.finished_at ? run.note : "searching…");
      li.appendChild(el("span", "muted", " – " + parts.filter(Boolean).join(" · ")));
      els.runs.appendChild(li);
    }
    if (!editing) fillForm();
  }

  function fillForm() {
    const s = status;
    els.type.innerHTML = "";
    const list = types.length ? types : [{ id: s.business_type_id, name: s.business_type_name || "—" }];
    for (const t of list) els.type.appendChild(new Option(t.name, t.id));
    els.type.value = s.business_type_id;
    els.limit.value = s.limit;
    els.min.value = s.min_waiting;
    els.perDay.value = s.searches_per_day;
    els.places.value = s.places.join("\n");
    fillNext(s.places, s.next_place);
  }

  function fillNext(places, index) {
    els.next.innerHTML = "";
    places.forEach((place, i) => els.next.appendChild(new Option(place, String(i))));
    els.next.appendChild(new Option("– none (all searched)", String(places.length)));
    els.next.value = String(Math.min(index, places.length));
  }

  els.places.addEventListener("input", () => {
    const places = els.places.value.split("\n").map((p) => p.trim()).filter(Boolean);
    const current = els.next.selectedOptions[0] ? els.next.selectedOptions[0].textContent : "";
    const kept = places.indexOf(current);
    fillNext(places, kept >= 0 ? kept : Math.min(Number(els.next.value), places.length));
  });

  function setEditing(on) {
    editing = on;
    els.form.hidden = !on;
    els.editBtn.setAttribute("aria-expanded", String(on));
    if (on) fillForm();
  }

  els.editBtn.addEventListener("click", async () => {
    if (!editing) {
      try { types = await api("/api/business-types"); } catch { /* the form shows the current one */ }
    }
    setEditing(!editing);
  });
  els.cancel.addEventListener("click", () => setEditing(false));

  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const places = els.places.value.split("\n").map((p) => p.trim()).filter(Boolean);
    try {
      status = await patch({
        business_type_id: els.type.value, places, next_place: Number(els.next.value),
        limit: Number(els.limit.value), min_waiting: Number(els.min.value), searches_per_day: Number(els.perDay.value),
      });
      setEditing(false);
      render();
      toast("Autopilot saved");
    } catch (err) { toast(err.message, true); }
  });

  els.toggle.addEventListener("click", async () => {
    const on = status.enabled && !status.stopped;
    if (!on && !confirm(`Switch the autopilot on? It searches ${status.business_type_name} businesses ` +
      `(next: ${status.next || "nothing left"}) and starts their campaigns by itself – emails go out in the ` +
      "send window, under the daily limit.")) return;
    els.toggle.disabled = true;
    try {
      status = await patch({ enabled: !on });
      render();
      toast(on ? "Autopilot off" : "Autopilot on");
      refresh();
    } catch (err) { toast(err.message, true); } finally { els.toggle.disabled = false; }
  });

  // ------------------------------------------------------------------
  // activity + the master sheet
  // ------------------------------------------------------------------
  function renderActivity(entries) {
    els.activity.innerHTML = "";
    if (!entries.length) els.activity.appendChild(el("li", "muted", "Nothing yet."));
    for (const entry of entries) {
      const li = el("li", "what-" + entry.what.toLowerCase().replace(/[^a-z]+/g, "-"));
      li.append(el("span", "activity-when", when(entry.at)), el("strong", "", entry.what));
      const who = [entry.business, entry.campaign && !entry.business ? entry.campaign : ""].filter(Boolean).join("");
      if (who) li.appendChild(el("span", "", " · " + who));
      if (entry.detail) li.appendChild(el("span", "muted", " – " + entry.detail));
      els.activity.appendChild(li);
    }
  }

  function renderMaster(drive) {
    for (const old of els.masterLinks.querySelectorAll(".drive-link")) old.remove();
    const files = (drive && drive.master) || {};
    for (const [part, label] of [["campaigns", "Campaigns sheet"], ["activity", "Activity sheet"]]) {
      if (!files[part] || !files[part].link) continue;
      const a = el("a", "btn small drive-link", `↗ ${label} in Drive`);
      a.href = files[part].link; a.target = "_blank"; a.rel = "noopener";
      els.masterLinks.prepend(a);
    }
    els.masterNote.textContent = !drive || !drive.connected
      ? "Connect Google Drive and the master sheet – every campaign on one page, and this log – is kept there."
      : !drive.auto_upload ? "Uploading to Drive is off (Settings › Google Drive) – download the sheets here."
      : files.campaigns ? `The master sheet in Google Drive (${drive.folder}) is updated within 5 minutes of a change.`
      : `The master sheet goes to Google Drive (${drive.folder}) within a few minutes.`;
  }

  async function refresh() {
    try {
      const [s, entries, drive] = await Promise.all([api("/api/autopilot"), api(`/api/activity?limit=${ACTIVITY_SHOWN}`),
        api("/api/drive")]);
      status = s;
      render();
      renderActivity(entries);
      renderMaster(drive);
    } catch { /* the next poll tries again */ }
  }

  window.autopilotPanel = { refresh };
  refresh();
})();
