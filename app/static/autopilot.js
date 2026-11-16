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
    type: $("ap-type"), also: $("ap-also"), limit: $("ap-limit"), min: $("ap-min"), perDay: $("ap-per-day"), places: $("ap-places"),
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
    const turns = s.turns || [];
    const names = turns.length ? turns.map((t) => t.name) : [s.business_type_name || "—"];
    const type = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
    const up = s.up_next ? `next ${s.up_next.business_type_name} in ${s.up_next.place}` : "no place left";
    els.summary.textContent = !s.enabled
      ? `Off. Switched on, it searches ${type} businesses place by place${names.length > 1 ? ", taking turns," : ""} ` +
        `and starts each place's campaign whenever fewer than ${s.min_waiting} businesses wait for their first email.`
      : s.searching ? `Searching ${s.searching} for ${s.searching_for || type} businesses – its campaign starts when ` +
        `the search is done.${names.length > 1 ? ` Then ${up}.` : ""}`
      : `${names.length > 1 ? `${type} take turns: ` : `${type}: `}${up}. Not searching now: ${s.holding || "…"}.`;
    els.stopped.hidden = !s.stopped;
    els.stoppedText.textContent = s.stopped ? s.stopped + " – switching it on again clears this." : "";
    const stats = [["waiting for a first email", s.waiting], ["searches today", `${s.searches_today} of ${s.searches_per_day}`]];
    for (const t of turns.length ? turns : [{ name: "", next_place: s.next_place }]) {
      stats.push([turns.length > 1 ? `places searched for ${t.name}` : "places searched",
        `${Math.min(t.next_place, s.places.length)} of ${s.places.length}`]);
    }
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
      const who = turns.length > 1 && run.business_type_name ? `${run.business_type_name} · ` : "";
      li.append(el("span", "activity-when", when(run.started_at)), el("strong", "", who + run.place));
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
    fillAlso(s.business_type_id, (s.also || []).map((t) => t.business_type_id));
    els.limit.value = s.limit;
    els.min.value = s.min_waiting;
    els.perDay.value = s.searches_per_day;
    els.places.value = s.places.join("\n");
    fillNext(s.places, s.next_place);
  }

  // the business types taking turns with the one it looks for: every other type, ticked if it takes turns
  function fillAlso(mainId, ticked) {
    for (const old of els.also.querySelectorAll("label.check")) old.remove();
    const list = types.length ? types : (status.turns || []).map((t) => ({ id: t.business_type_id, name: t.name }));
    for (const t of list) {
      if (t.id === mainId) continue;
      const label = el("label", "check");
      const box = el("input");
      box.type = "checkbox"; box.value = t.id; box.checked = ticked.includes(t.id);
      label.append(box, document.createTextNode(" " + t.name));
      els.also.appendChild(label);
    }
  }
  const alsoTicked = () => [...els.also.querySelectorAll("input:checked")].map((b) => b.value);
  els.type.addEventListener("change", () => {
    fillAlso(els.type.value, alsoTicked());
    const own = (status.turns || []).find((t) => t.business_type_id === els.type.value);  // its own next place
    if (own) fillNext(els.places.value.split("\n").map((p) => p.trim()).filter(Boolean), own.next_place);
  });

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
        business_type_id: els.type.value, also_type_ids: alsoTicked(), places, next_place: Number(els.next.value),
        limit: Number(els.limit.value), min_waiting: Number(els.min.value), searches_per_day: Number(els.perDay.value),
      });
      setEditing(false);
      render();
      toast("Autopilot saved");
    } catch (err) { toast(err.message, true); }
  });

  els.toggle.addEventListener("click", async () => {
    const on = status.enabled && !status.stopped;
    const up = status.up_next ? `${status.up_next.business_type_name} in ${status.up_next.place}` : "nothing left";
    const names = (status.turns || []).map((t) => t.name).join(" and ") || status.business_type_name;
    if (!on && !confirm(`Switch the autopilot on? It searches ${names} businesses ` +
      `(next: ${up}) and starts their campaigns by itself – emails go out in the ` +
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
    for (const [part, label] of [["activity", "Activity sheet"], ["campaigns", "Campaigns sheet"],
      ["sheet", "Master sheet"]]) {
      if (!files[part] || !files[part].link) continue;
      const a = el("a", `btn small drive-link${part === "sheet" ? " primary" : ""}`, `↗ ${label} in Drive`);
      a.href = files[part].link; a.target = "_blank"; a.rel = "noopener";
      els.masterLinks.prepend(a);
    }
    const saved = files.sheet || files.campaigns;
    els.masterNote.textContent = !drive || !drive.connected
      ? "Connect Google Drive and the master sheet – every campaign on one page, and this log – is kept there."
      : !drive.auto_upload ? "Uploading to Drive is off (Settings › Google Drive) – download the sheets here."
      : (saved ? `The master sheet in Google Drive (${drive.folder}) is updated within 5 minutes of a change.`
        : `The master sheet goes to Google Drive (${drive.folder}) within a few minutes.`) +
        (drive.sheets_off ? ` For now as CSV files: ${drive.sheets_off}.` : "");
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
