/* Leads Pilot Engine - Outreach tab: the setup checklist, the mailbox, campaigns, business types and
 * their emails (vanilla JS, no build step). Talks to /api/outreach, /api/campaigns, /api/business-types and
 * /api/drive (docs/API.md); shares its helpers with app.js through window.panel. */
(() => {
  "use strict";

  const { api, toast, fmtDate, jobTitle } = window.panel;
  const $ = (id) => document.getElementById(id);
  const POLL_MS = 5000;
  const BACKGROUND_POLL_MS = 30000;
  const ACTIVE_JOB = new Set(["queued", "running", "enriching"]);
  const STATUS_TEXT = {
    queued: "waiting", sent: "in sequence", done: "no reply", replied: "replied", unsubscribed: "unsubscribed",
    bounced: "bounced", failed: "refused", skipped: "left out",
  };

  const els = {
    setupList: $("setup-list"), mailboxFacts: $("mailbox-facts"), checkBtn: $("check-btn"),
    newCampaignBtn: $("new-campaign-btn"), campaignList: $("campaign-list"), newTypeBtn: $("new-type-btn"),
    typeList: $("type-list"), intro: $("outreach-intro"), tabCount: $("tab-outreach-count"),
    newCard: $("new-campaign-card"), form: $("campaign-form"), campType: $("camp-type"), campJobs: $("camp-jobs"),
    campFiles: $("camp-files"), campName: $("camp-name"), campCreate: $("camp-create"), campCancel: $("camp-cancel"),
    campError: $("camp-error"),
    campCard: $("campaign-card"), campTitle: $("camp-title"), campStatus: $("camp-status"),
    campTypeChip: $("camp-type-chip"), campStart: $("camp-start"), campPause: $("camp-pause"),
    campDrive: $("camp-drive"), campDelete: $("camp-delete"), campSummary: $("camp-summary"),
    campAlert: $("camp-alert"), campAlertTitle: $("camp-alert-title"), campAlertList: $("camp-alert-list"),
    campStats: $("camp-stats"),
    editorCard: $("editor-card"), editorTitle: $("editor-title"), editorSubtitle: $("editor-subtitle"),
    editorForm: $("editor-form"), edLoadRow: $("ed-load-row"), edLoad: $("ed-load"), edSaveAs: $("ed-save-as"),
    edDeleteType: $("ed-delete-type"), edSave: $("ed-save"), edTypeFields: $("ed-type-fields"), edName: $("ed-name"),
    edSearch: $("ed-search"), edTrade: $("ed-trade"), edChips: $("ed-chips"), edSteps: $("ed-steps"),
    edAddStep: $("ed-add-step"), edExpand: $("ed-expand"), edFooter: $("ed-footer"), edFooterBox: $("ed-footer-box"),
    edFooterMark: $("ed-footer-mark"), edFooterPreview: $("ed-footer-preview"), edTradeMark: $("ed-trade-mark"),
    edHelp: $("ed-help"), edError: $("ed-error"), previewPick: $("preview-pick"), previewNote: $("preview-note"),
    leadsCard: $("leads-card"), leadCount: $("lead-count"), leadsSubtitle: $("leads-subtitle"),
    leadFilter: $("lead-filter"), leadBody: $("lead-body"), leftOut: $("left-out"),
    leftOutSummary: $("left-out-summary"), leftOutList: $("left-out-list"),
  };

  const state = {
    visible: false, timer: null, status: null, drive: null, campaigns: [], types: [],
    view: { kind: "intro" },   // intro | new | campaign | type
    campaign: null,            // the campaign shown (full)
    type: null,                // the business type shown
    dirty: false,              // the email editor has unsaved changes
    lastField: null,           // where a placeholder chip goes
    openSteps: new Set([0]),   // which emails of the editor are opened up
    previewPick: "",           // campaign: a lead id - business type: a sample's index
    preview: null,             // the server's preview: the lead's values the editor fills in live
    leadFilter: "",
    typeTouched: false,        // the new campaign's business type was picked by hand
    alert: null,               // {title, items} from a failed start
  };

  // ------------------------------------------------------------------
  // helpers
  // ------------------------------------------------------------------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
  const when = (iso) => iso ? new Date(iso).toLocaleString([], {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const patch = (path, body) => api(path, { method: "PATCH", body: JSON.stringify(body) });
  const link = (href, text) => {
    const a = el("a", "", text);
    a.href = href; a.target = "_blank"; a.rel = "noopener";
    return a;
  };
  function jobName(id) {
    const job = window.panel.jobs().find((j) => j.id === id);
    return job ? `“${jobTitle(job)}”` : "a deleted search";
  }
  function countsOf(campaign) {
    const counts = Object.fromEntries(Object.keys(STATUS_TEXT).map((status) => [status, 0]));
    for (const lead of campaign.leads) counts[lead.status] = (counts[lead.status] || 0) + 1;
    return counts;
  }

  // ------------------------------------------------------------------
  // loading
  // ------------------------------------------------------------------
  async function loadStatus() {
    try {
      [state.status, state.drive] = await Promise.all([api("/api/outreach"), api("/api/drive")]);
    } catch (err) {
      return;
    }
    renderSetup();
    renderMailbox();
    renderPlaceholderHelp();
  }

  async function loadCampaigns() {
    try { state.campaigns = await api("/api/campaigns"); } catch { return; }
    renderCampaignList();
    renderSetup();
    const sending = state.campaigns.filter((c) => c.status === "running").length;
    els.tabCount.hidden = !sending;
    els.tabCount.textContent = String(sending);
  }

  async function loadTypes() {
    try { state.types = await api("/api/business-types"); } catch { return; }
    renderTypeList();
    renderSetup();
  }

  const refreshAll = () => Promise.all([loadStatus(), loadCampaigns(), loadTypes(), window.panel.refreshJobs()]);

  // ------------------------------------------------------------------
  // setup checklist + mailbox
  // ------------------------------------------------------------------
  function renderSetup() {
    const s = state.status;
    const d = state.drive;
    if (!s || !d) return;
    const edited = state.types.some((t) => t.updated_at !== t.created_at);
    const searched = window.panel.jobs().some((j) => j.email_count > 0);
    const started = state.campaigns.some((c) => c.started_at);
    const items = [
      { done: s.ready, title: "Mailbox", build: buildMailboxStep },
      { done: d.connected, title: "Google Drive", build: buildDriveStep },
      { done: edited, title: "The emails of your business types",
        text: "Open a business type below, read its emails, change what you like, press Save." },
      { done: searched, title: "A search with emails",
        text: "Search tab: a business type and a city, No website, Must have: Email." },
      { done: started, title: "Your first campaign",
        text: "+ New campaign, check the preview, send yourself a test, then Start." },
    ];
    if (s.sites) items.splice(3, 0, { done: !s.sites.last_upload.error, title: "Preview websites", build: buildSitesStep });
    els.setupList.innerHTML = "";
    for (const item of items) {
      const li = el("li", item.done ? "done" : "");
      const head = el("div", "check-head");
      head.append(el("span", "check-mark", item.done ? "✓" : ""), el("strong", "", item.title));
      li.appendChild(head);
      if (item.build) item.build(li, item.done);
      else if (!item.done) li.appendChild(el("small", "muted block", item.text));
      els.setupList.appendChild(li);
    }
  }

  // a button to the Settings tab's group where the missing settings are filled in
  function settingsButton(text, group) {
    const button = el("button", "btn primary small", text);
    button.type = "button";
    button.addEventListener("click", () => window.settingsPanel.open(group));
    return button;
  }

  function buildMailboxStep(li, done) {
    const s = state.status;
    if (done) {
      li.appendChild(el("small", "muted block", `Sending as ${s.sender}`));
      return;
    }
    const list = el("ul", "problems");
    for (const problem of s.problems) list.appendChild(el("li", "", problem));
    li.appendChild(list);
    const line = el("div", "row-between");
    line.appendChild(el("small", "muted", "Gmail: use an app password – Settings shows how."));
    line.appendChild(settingsButton("Fill in ›", "mailbox"));
    li.appendChild(line);
  }

  function buildSitesStep(li, done) {
    const w = state.status.sites;
    if (!done) {
      const list = el("ul", "problems");
      list.appendChild(el("li", "", `The websites couldn't be uploaded: ${w.last_upload.error}`));
      li.appendChild(list);
      li.appendChild(el("small", "muted block", "First emails wait until their business's website is online. "
        + "It tries again every 10 minutes."));
      return;
    }
    const parts = [`${w.online.length} online at ${w.url.replace(/^https?:\/\//, "")}`];
    if (w.uploading) parts.push("uploading…");
    else if (w.waiting) parts.push(`${w.waiting} waiting to go online`);
    li.appendChild(el("small", "muted block", parts.join(" · ")));
  }

  // the business's website (SITES_URL/<slug>/), or "" when it has none
  function siteUrl(lead) {
    const w = state.status && state.status.sites;
    return lead.site && w ? `${w.url}/${lead.site}/` : "";
  }

  function siteOnline(lead) {
    const w = state.status && state.status.sites;
    return !w || !lead.site || w.online.includes(lead.site);
  }

  function buildDriveStep(li, done) {
    const d = state.drive;
    if (!d.configured) {
      const list = el("ul", "problems");
      for (const problem of d.problems) list.appendChild(el("li", "", problem));
      li.appendChild(list);
      const line = el("div", "row-between");
      line.appendChild(el("small", "muted", "docs/PLAYBOOK.md step 2 walks through making the OAuth client."));
      line.appendChild(settingsButton("Fill in ›", "drive"));
      li.appendChild(line);
      return;
    }
    const line = el("div", "row-between");
    if (done) {
      line.appendChild(el("small", "muted", `${d.email || "Connected"} · folder: ${d.folder}` +
        (d.auto_upload ? " · searches upload when they finish" : "")));
      const off = el("button", "btn ghost small", "Disconnect");
      off.addEventListener("click", disconnectDrive);
      line.appendChild(off);
    } else {
      line.appendChild(el("small", "muted", "Sign in once: Google asks you to allow access to your Drive."));
      const on = el("button", "btn primary small", "Connect Google Drive");
      on.addEventListener("click", connectDrive);
      line.appendChild(on);
    }
    li.appendChild(line);
    if (d.last_error) li.appendChild(el("small", "error block", d.last_error));
  }

  async function connectDrive() {
    try {
      const { auth_url: url } = await post("/api/drive/connect");
      location.href = url;               // Google sends the browser back to this panel
    } catch (err) { toast(err.message, true); }
  }

  async function disconnectDrive() {
    if (!confirm("Disconnect Google Drive? Nothing is uploaded until you connect it again.")) return;
    try {
      await post("/api/drive/disconnect");
      toast("Google Drive disconnected");
      await loadStatus();
      window.panel.refreshDrive();
    } catch (err) { toast(err.message, true); }
  }

  function renderMailbox() {
    const s = state.status;
    const facts = [];
    if (!s.autosend) facts.push(["Sending", "off – Settings › When emails go out"]);
    facts.push(["From", s.sender || "not set"]);
    facts.push(["Send window", s.window ? s.window + (s.window_open ? " · open now" : "") : "—"]);
    facts.push(["Today", `${s.sent_today} of ${s.daily_limit} emails sent`]);
    facts.push(["Next email", !s.autosend ? "— (sending is off)" : s.next_send_at ? when(s.next_send_at)
      : s.running ? "nothing due" : "no campaign sending"]);
    const check = s.last_check;
    facts.push(["Replies checked", check.at ? when(check.at) + (check.error ? " – failed" : "") : "not yet"]);
    facts.push(["Emailed so far", plural(s.contacted, "business", "businesses")]);
    els.mailboxFacts.innerHTML = "";
    for (const [term, value] of facts) {
      els.mailboxFacts.append(el("dt", "", term), el("dd", "", value));
    }
    if (check.error) els.mailboxFacts.append(el("dd", "error wide", check.error));
    els.checkBtn.disabled = !s.ready;
  }

  els.checkBtn.addEventListener("click", async () => {
    els.checkBtn.disabled = true;
    try {
      const result = await post("/api/outreach/check");
      toast(`Mailbox checked: ${plural(result.replies, "new reply", "new replies")}, ` +
        `${plural(result.bounces, "bounce")}`);
      await poll();
    } catch (err) { toast(err.message, true); } finally { els.checkBtn.disabled = false; }
  });

  // ------------------------------------------------------------------
  // lists: campaigns, business types
  // ------------------------------------------------------------------
  function renderCampaignList() {
    els.campaignList.innerHTML = "";
    if (!state.campaigns.length) {
      els.campaignList.appendChild(el("li", "muted", "No campaigns yet."));
      return;
    }
    for (const c of state.campaigns) {
      const li = el("li");
      if (state.view.kind === "campaign" && state.view.id === c.id) li.classList.add("active");
      const main = el("div", "job-main");
      main.appendChild(el("div", "job-query", c.name));
      if (c.business_type_name) main.append(el("span", "chip small type", c.business_type_name), " ");
      main.appendChild(el("span", `pill tiny ${c.status}`, c.status));
      main.appendChild(el("div", "job-meta", `${c.emails_sent} sent · ${c.counts.replied} replied · ` +
        plural(c.leads, "business", "businesses")));
      li.appendChild(main);
      li.addEventListener("click", () => openCampaign(c.id));
      els.campaignList.appendChild(li);
    }
  }

  function renderTypeList() {
    els.typeList.innerHTML = "";
    for (const t of state.types) {
      const li = el("li");
      if (state.view.kind === "type" && state.view.id === t.id) li.classList.add("active");
      const main = el("div", "job-main");
      main.appendChild(el("div", "job-query", t.name));
      main.appendChild(el("div", "job-meta", `searches “${t.search || t.name}” · ${plural(t.steps.length, "email")}`));
      li.appendChild(main);
      li.addEventListener("click", () => openType(t.id));
      els.typeList.appendChild(li);
    }
  }

  // ------------------------------------------------------------------
  // what the content area shows
  // ------------------------------------------------------------------
  function leaveEditor() {
    if (!state.dirty) return true;
    if (!confirm("Discard your unsaved changes to the emails?")) return false;
    state.dirty = false;
    return true;
  }

  function show(kind) {
    els.intro.hidden = kind !== "intro";
    for (const id of ["autopilot-card", "activity-card"]) $(id).hidden = kind !== "intro";
    els.newCard.hidden = kind !== "new";
    els.campCard.hidden = kind !== "campaign";
    els.leadsCard.hidden = kind !== "campaign";
    els.editorCard.hidden = kind !== "campaign" && kind !== "type";
  }

  async function openCampaign(id, { keepEditor = false } = {}) {
    if (!keepEditor && !leaveEditor()) return;
    let campaign;
    try { campaign = await api(`/api/campaigns/${id}`); } catch (err) { toast(err.message, true); return; }
    const switched = !state.campaign || state.campaign.id !== id;
    state.view = { kind: "campaign", id };
    state.campaign = campaign;
    state.type = null;
    if (switched) { state.alert = null; state.previewPick = ""; state.leadFilter = ""; els.leadFilter.value = ""; }
    show("campaign");
    renderCampaign();
    renderLeads();
    if (!keepEditor) renderEditor(campaign, "campaign");
    renderCampaignList();
    renderTypeList();
    if (!keepEditor) loadPreview();
  }

  async function openType(id) {
    if (!leaveEditor()) return;
    let type;
    try { type = await api(`/api/business-types/${id}`); } catch (err) { toast(err.message, true); return; }
    state.view = { kind: "type", id };
    state.type = type;
    state.campaign = null;
    state.previewPick = "0";
    show("type");
    renderEditor(type, "type");
    renderCampaignList();
    renderTypeList();
    loadPreview();
  }

  // ------------------------------------------------------------------
  // new campaign
  // ------------------------------------------------------------------
  async function openNewCampaign(jobIds = [], typeId = null) {
    if (!leaveEditor()) return;
    state.view = { kind: "new" };
    state.campaign = null;
    state.type = null;
    state.typeTouched = false;
    els.campError.hidden = true;
    els.campName.value = "";
    show("new");
    renderCampaignList();
    renderTypeList();
    if (!state.types.length) await loadTypes();
    const jobs = await window.panel.refreshJobs();
    els.campType.innerHTML = "";
    els.campType.appendChild(new Option("Default emails", ""));
    for (const t of state.types) els.campType.appendChild(new Option(t.name, t.id));
    const usable = jobs.filter((j) => !ACTIVE_JOB.has(j.status) && j.email_count > 0);
    els.campJobs.innerHTML = "";
    if (!usable.length) {
      els.campJobs.appendChild(el("small", "muted", "No finished search with emails yet – run one in the Search tab."));
    }
    for (const job of usable) {
      const label = el("label", "pick");
      const box = el("input");
      box.type = "checkbox"; box.value = job.id; box.checked = jobIds.includes(job.id);
      box.dataset.type = job.business_type_id || "";
      box.addEventListener("change", () => { if (!state.typeTouched) pickTypeFromJobs(); });
      const text = el("span");
      text.appendChild(el("span", "", jobTitle(job)));
      const type = state.types.find((t) => t.id === job.business_type_id);
      if (type) text.append(" ", el("span", "chip small type", type.name));
      text.appendChild(el("small", "muted block", `${plural(job.email_count, "row")} with an email · ` +
        `${job.filters ? job.filters.label : ""} · ${fmtDate(job.created_at)}`));
      label.append(box, text);
      els.campJobs.appendChild(label);
    }
    if (typeId !== null && typeId !== undefined && (typeId === "" || state.types.some((t) => t.id === typeId))) {
      els.campType.value = typeId;
      loadFiles();
    } else {
      pickTypeFromJobs();
    }
    els.newCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function checkedValues(container) {
    return Array.from(container.querySelectorAll("input[type=checkbox]:checked")).map((b) => b.value);
  }

  function pickTypeFromJobs() {
    const types = new Set(Array.from(els.campJobs.querySelectorAll("input:checked")).map((b) => b.dataset.type));
    if (types.size === 1) els.campType.value = [...types][0];
    loadFiles();
  }

  async function loadFiles() {
    const typeId = els.campType.value;
    els.campFiles.innerHTML = "";
    if (!state.drive || !state.drive.connected) {
      els.campFiles.appendChild(el("small", "muted", "Connect Google Drive (Setup, on the left) to use lead files."));
      return;
    }
    if (!typeId) {
      els.campFiles.appendChild(el("small", "muted", "Pick a business type to see the files in its Drive folder."));
      return;
    }
    els.campFiles.appendChild(el("small", "muted", "Looking in Google Drive…"));
    let data;
    try {
      data = await api(`/api/drive/files?business_type_id=${encodeURIComponent(typeId)}`);
    } catch (err) {
      els.campFiles.innerHTML = "";
      els.campFiles.appendChild(el("small", "error", err.message));
      return;
    }
    if (els.campType.value !== typeId) return;      // picked another type meanwhile
    els.campFiles.innerHTML = "";
    if (!data.files.length) {
      const note = el("small", "muted", `No lead files in the Drive folder ${data.folder} yet. `);
      if (data.folder_link) note.appendChild(link(data.folder_link, "Open the folder ↗"));
      els.campFiles.appendChild(note);
      return;
    }
    for (const file of data.files) {
      const label = el("label", "pick");
      const box = el("input");
      box.type = "checkbox"; box.value = file.id;
      const text = el("span");
      text.append(el("span", "", file.name), " ");
      if (file.link) text.appendChild(link(file.link, "open ↗"));
      text.appendChild(el("small", "muted block", (file.sheet ? "Google Sheet · " : "CSV · ") +
        `changed ${fmtDate(file.modified)}`));
      label.append(box, text);
      els.campFiles.appendChild(label);
    }
  }

  els.campType.addEventListener("change", () => { state.typeTouched = true; loadFiles(); });
  els.newCampaignBtn.addEventListener("click", () => openNewCampaign());
  els.campCancel.addEventListener("click", () => { state.view = { kind: "intro" }; show("intro"); renderCampaignList(); });

  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    els.campError.hidden = true;
    const body = {
      job_ids: checkedValues(els.campJobs), drive_file_ids: checkedValues(els.campFiles),
      business_type_id: els.campType.value, name: els.campName.value.trim(),
    };
    if (!body.job_ids.length && !body.drive_file_ids.length) {
      els.campError.textContent = "Tick at least one search or lead file.";
      els.campError.hidden = false;
      return;
    }
    els.campCreate.disabled = true;
    els.campCreate.textContent = "Creating…";
    try {
      const campaign = await post("/api/campaigns", body);
      toast(`Campaign created: ${plural(campaign.leads.length, "business", "businesses")} to email, ` +
        `${campaign.left_out.length} left out`);
      await loadCampaigns();
      await openCampaign(campaign.id);
    } catch (err) {
      els.campError.textContent = err.message;
      els.campError.hidden = false;
    } finally {
      els.campCreate.disabled = false;
      els.campCreate.textContent = "Create campaign";
    }
  });

  // ------------------------------------------------------------------
  // a campaign
  // ------------------------------------------------------------------
  function renderCampaign() {
    const c = state.campaign;
    els.campTitle.textContent = c.name;
    els.campStatus.textContent = c.status;
    els.campStatus.className = "pill " + c.status;
    els.campTypeChip.hidden = !c.business_type_name;
    els.campTypeChip.textContent = c.business_type_name ? `emails from ${c.business_type_name}` : "";
    const canStart = c.status === "draft" || c.status === "paused";
    els.campStart.hidden = !canStart;
    els.campStart.textContent = c.status === "paused" ? "▶ Resume sending" : "▶ Start sending";
    els.campPause.hidden = c.status !== "running";
    els.campDrive.hidden = !(state.drive && state.drive.connected) || c.status === "draft";
    els.campDelete.disabled = c.status === "running";
    els.campDelete.title = c.status === "running" ? "Pause the campaign first" : "Delete this campaign";

    const sources = [...c.job_ids.map(jobName), ...c.drive_files.map((f) => `the file ${f.name}`)];
    const parts = [`From ${sources.join(", ") || "—"}`, `${plural(c.steps.length, "email")} per business`,
      `created ${fmtDate(c.created_at)}`];
    if (c.started_at) parts.push(`started ${fmtDate(c.started_at)}`);
    if (c.finished_at) parts.push(`finished ${fmtDate(c.finished_at)}`);
    els.campSummary.textContent = parts.join(" · ");
    if (c.drive_path) {
      els.campSummary.append(" · results in Drive: ", link(c.drive_link || "#", c.drive_path));
    }

    const alert = state.alert || (c.error ? { title: "Paused automatically", items: [c.error] } : null);
    els.campAlert.hidden = !alert;
    if (alert) {
      els.campAlertTitle.textContent = alert.title;
      els.campAlertList.innerHTML = "";
      for (const item of alert.items) els.campAlertList.appendChild(el("li", "", item));
    }

    const counts = countsOf(c);
    const stats = [
      ["waiting", counts.queued], ["in sequence", counts.sent], ["replied", counts.replied],
      ["no reply", counts.done], ["bounced / refused", counts.bounced + counts.failed],
      ["unsubscribed", counts.unsubscribed], ["left out", counts.skipped],
    ];
    els.campStats.innerHTML = "";
    for (const [label, n] of stats) {
      if (!n && label !== "waiting" && label !== "replied") continue;
      const stat = el("div", "stat");
      stat.append(el("strong", "", String(n)), el("span", "muted", label));
      els.campStats.appendChild(stat);
    }
  }

  els.campStart.addEventListener("click", async () => {
    const c = state.campaign;
    if (!c) return;
    if (c.status === "draft" && !confirm(`Start sending “${c.name}”? ` +
      `${plural(countsOf(c).queued, "business", "businesses")} will get the first email in the send window, ` +
      "a few minutes apart, then the follow-ups.")) return;
    try {
      state.campaign = await post(`/api/campaigns/${c.id}/start`);
      state.alert = null;
      toast(state.status && !state.status.autosend ? "Started – but sending is off (OUTREACH_AUTOSEND=false)"
        : "Sending started – emails go out in the send window");
      renderCampaign();
      renderLeads();
      await Promise.all([loadCampaigns(), loadStatus()]);
    } catch (err) {
      const items = err.message.replace(/^Can't start yet:\s*/, "").split(" · ");
      state.alert = { title: "Can't start yet", items };
      renderCampaign();
    }
  });

  els.campPause.addEventListener("click", async () => {
    try {
      state.campaign = await post(`/api/campaigns/${state.campaign.id}/pause`);
      toast("Paused – nothing more is sent until you resume");
      renderCampaign();
      renderLeads();
      loadCampaigns();
    } catch (err) { toast(err.message, true); }
  });

  els.campDelete.addEventListener("click", async () => {
    const c = state.campaign;
    if (!confirm(`Delete the campaign “${c.name}”? The businesses it emailed stay on the “emailed” list, ` +
      "so no other campaign emails them again.")) return;
    try {
      await api(`/api/campaigns/${c.id}`, { method: "DELETE" });
      state.dirty = false;
      state.view = { kind: "intro" };
      state.campaign = null;
      show("intro");
      toast("Campaign deleted");
      loadCampaigns();
    } catch (err) { toast(err.message, true); }
  });

  els.campDrive.addEventListener("click", async () => {
    els.campDrive.disabled = true;
    try {
      const saved = await post(`/api/campaigns/${state.campaign.id}/drive`);
      toast(`Saved to Google Drive: ${saved.path}`);
      await openCampaign(state.campaign.id, { keepEditor: true });
    } catch (err) { toast(err.message, true); } finally { els.campDrive.disabled = false; }
  });

  // ------------------------------------------------------------------
  // the businesses of a campaign
  // ------------------------------------------------------------------
  function renderLeads() {
    const c = state.campaign;
    els.leadCount.textContent = String(c.leads.length);
    els.leadsSubtitle.textContent = c.status === "draft"
      ? "Check the list and leave out anyone you don't want to email – nothing is sent before you press Start."
      : "Updates every few seconds. A reply, bounce or unsubscribe stops the follow-ups for that business.";
    const filter = state.leadFilter.trim().toLowerCase();
    const leads = c.leads.filter((l) => !filter || [l.business, l.name, l.email, l.city, STATUS_TEXT[l.status]]
      .some((v) => (v || "").toLowerCase().includes(filter)));
    els.leadBody.innerHTML = "";
    if (!leads.length) {
      const tr = el("tr", "empty");
      const td = el("td", "", c.leads.length ? "No business matches the filter." : "No business to email.");
      td.colSpan = 8;
      tr.appendChild(td);
      els.leadBody.appendChild(tr);
    }
    for (const lead of leads) els.leadBody.appendChild(leadRow(c, lead));
    renderLeftOut(c.left_out);
  }

  function leadRow(c, lead) {
    const tr = el("tr");
    if (lead.status === "skipped") tr.classList.add("skipped");
    const name = el("td", "name");
    name.appendChild(el("span", "", lead.business));
    name.title = lead.name !== lead.business ? `On Google Maps: ${lead.name}` : lead.name;
    if (lead.category) name.appendChild(el("small", "muted block", lead.category));
    if (siteUrl(lead)) {
      const link = el("a", "site-link", siteOnline(lead) ? "Website ↗" : "Website (not online yet)");
      link.href = siteUrl(lead);
      link.target = "_blank";
      link.rel = "noopener";
      name.appendChild(link);
    }
    tr.appendChild(name);
    const email = el("td", "", lead.email);
    email.title = lead.other_emails.length ? `Also: ${lead.other_emails.join(", ")}` : lead.email;
    tr.appendChild(email);
    tr.appendChild(el("td", "", lead.city || "—"));
    const rating = el("td", "num", lead.rating !== null && lead.rating !== undefined
      ? `${Number(lead.rating).toFixed(1)} ★ · ${lead.reviews ?? 0}` : "—");
    rating.title = "Google rating · number of reviews";
    tr.appendChild(rating);
    const status = el("td");
    const tag = el("span", `tag lead-${lead.status}`, STATUS_TEXT[lead.status] || lead.status);
    const notes = [lead.note, ...lead.inbound.map((i) => `${i.kind.replace("_", " ")} ${when(i.received_at)}: ${i.subject}`)];
    tag.title = notes.filter(Boolean).join("\n");
    status.appendChild(tag);
    if (lead.site_opened_at) {           // who opened their website: app/outreach/visits.py
      const taps = lead.site_taps.length ? ` · tapped ${lead.site_taps.join(", ")}` : "";
      const opened = el("small", "block site-opened",
        `Opened website${lead.site_visits > 1 ? ` ×${lead.site_visits}` : ""}${taps}`);
      opened.title = [`First: ${when(lead.site_opened_at)}`, `Last: ${when(lead.site_last_visit_at)}`,
        lead.site_from ? `From: ${lead.site_from}` : ""].filter(Boolean).join("\n");
      status.appendChild(opened);
    }
    tr.appendChild(status);
    const sent = el("td", "", `${lead.sent.length} of ${c.steps.length}`);
    sent.title = lead.sent.map((s) => `Email ${s.step}: ${when(s.sent_at)}`).join("\n") || "Nothing sent yet";
    tr.appendChild(sent);
    let next = "—";
    if (lead.status === "sent" && lead.next_due) next = when(lead.next_due);
    else if (lead.status === "queued" && c.sites && !siteOnline(lead)) next = "once its website is online";
    else if (lead.status === "queued") next = c.status === "running" ? "in the send window" : "after Start";
    tr.appendChild(el("td", "", next));

    const actions = el("td", "actions");
    const preview = el("button", "btn tiny", "Preview");
    preview.title = "Show the emails as this business gets them";
    preview.addEventListener("click", () => {
      state.previewPick = lead.id;
      loadPreview();
      const first = stepBoxes()[0];
      if (first) first.open = true;
      els.editorCard.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    actions.appendChild(preview);
    if (lead.status === "queued" || lead.status === "sent") {
      const skip = el("button", "btn tiny", "Leave out");
      skip.title = "Send this business nothing (more)";
      skip.addEventListener("click", () => updateLead(lead, { skipped: true }));
      actions.appendChild(skip);
    } else if (lead.status === "skipped") {
      const include = el("button", "btn tiny", "Include");
      include.addEventListener("click", () => updateLead(lead, { skipped: false }));
      actions.appendChild(include);
    }
    const rename = el("button", "btn tiny ghost", "✎");
    rename.title = "Change the name the emails use ({Business})";
    rename.addEventListener("click", () => {
      const name = prompt("The business name as the emails write it:", lead.business);
      if (name && name.trim() && name.trim() !== lead.business) updateLead(lead, { business: name.trim() });
    });
    actions.appendChild(rename);
    tr.appendChild(actions);
    return tr;
  }

  async function updateLead(lead, body) {
    try {
      await patch(`/api/campaigns/${state.campaign.id}/leads/${lead.id}`, body);
      await openCampaign(state.campaign.id, { keepEditor: true });
      if (body.business) loadPreview();
    } catch (err) { toast(err.message, true); }
  }

  function renderLeftOut(items) {
    els.leftOut.hidden = !items.length;
    if (!items.length) return;
    els.leftOutSummary.textContent = `${plural(items.length, "business", "businesses")} left out – why`;
    const groups = new Map();
    for (const item of items) {
      const cut = item.reason.search(/ \(|: /);
      const key = cut > 0 ? item.reason.slice(0, cut) : item.reason;
      const detail = cut > 0 ? item.reason.slice(cut).replace(/^: /, "").replace(/^ \((.*)\)$/, "$1") : "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(detail ? `${item.name} (${detail})` : item.name);
    }
    els.leftOutList.innerHTML = "";
    for (const [reason, names] of groups) {
      const li = el("li");
      li.append(el("strong", "", `${reason} (${names.length}): `), names.join(" · "));
      els.leftOutList.appendChild(li);
    }
  }

  els.leadFilter.addEventListener("input", () => {
    state.leadFilter = els.leadFilter.value;
    if (state.campaign) renderLeads();
  });

  // ------------------------------------------------------------------
  // the email editor (a campaign's emails or a business type): every email is a block you can open
  // and close - its text on the left, and on the right the email as it goes out, filled in while you
  // type; the check mark in the block's header says whether it is filled in (! = something is wrong)
  // ------------------------------------------------------------------
  const FIELD = /\{([A-Za-z]+)\}/g;
  const TOKEN = /\[\[([\s\S]*?)\]\]|\{([A-Za-z]+)\}/g;
  const knownFields = () => new Set(Object.keys((state.status && state.status.placeholders) || {}));
  const oneLine = (text) => text.split(/\s+/).filter(Boolean).join(" ");

  // the same rules as render() / compose() in app/outreach/templates.py
  function fillTemplate(text, values) {
    const names = knownFields();
    const value = (name) => (names.has(name) ? String(values[name] || "") : null);
    const fill = (part) => part.replace(FIELD, (m, name) => { const v = value(name); return v === null ? m : v; });
    const out = String(text || "").replace(TOKEN, (m, block, field) => {
      if (field !== undefined) { const v = value(field); return v === null ? m : v; }
      const bar = block.indexOf("||");
      const first = bar >= 0 ? block.slice(0, bar) : block;
      const fallback = bar >= 0 ? block.slice(bar + 2) : "";
      const wanted = Array.from(first.matchAll(FIELD), (x) => x[1]).filter((n) => names.has(n));
      return wanted.every((n) => values[n]) ? fill(first) : fill(fallback);
    });
    return out.replace(/\r\n/g, "\n").split("\n").map((line) => line.replace(/\s+$/, "")).join("\n")
      .replace(/\n{3,}/g, "\n\n").replace(/^\n+|\n+$/g, "");
  }

  function composeEmail(steps, footer, index, values) {
    const first = oneLine(fillTemplate(steps[0].subject, values));
    const own = steps[index].subject.trim() ? oneLine(fillTemplate(steps[index].subject, values)) : "";
    const subject = own || (index === 0 ? first : `Re: ${first}`);
    const body = fillTemplate(steps[index].body, values);
    const foot = fillTemplate(footer, values);
    return { subject, body: foot ? `${body}\n\n${foot}` : body };
  }

  // what is wrong with a template ([] when it is fine) - the server checks again on Save
  function templateProblems(text) {
    const names = knownFields();
    const problems = [];
    if (names.size) {
      const unknown = new Set(Array.from(String(text || "").matchAll(FIELD), (m) => m[1]).filter((n) => !names.has(n)));
      for (const name of unknown) problems.push(`unknown placeholder {${name}}`);
    }
    if (/\[\[|\]\]/.test(String(text || "").replace(/\[\[[\s\S]*?\]\]/g, ""))) {
      problems.push("an optional part [[ … ]] is not closed");
    }
    return problems;
  }

  function setMark(mark, status, title) {           // status: done | todo | error
    mark.className = `check-mark ${status}`;
    mark.textContent = status === "done" ? "✓" : status === "error" ? "!" : "";
    mark.title = title || (status === "done" ? "Filled in" : "Not filled in yet");
  }

  function renderEditor(source, kind, { keepOpen = false } = {}) {
    const isType = kind === "type";
    state.dirty = false;
    if (!keepOpen) {
      state.openSteps = new Set([0]);
      els.edFooterBox.open = false;
    }
    els.edSave.disabled = true;
    els.edError.hidden = true;
    els.editorTitle.textContent = isType ? `Business type: ${source.name}` : "Emails of this campaign";
    els.editorSubtitle.textContent = isType
      ? "A campaign for these businesses starts with these emails and keeps its own copy."
      : source.status === "running" || source.status === "paused"
        ? "Changes apply to the emails not sent yet."
        : `Copied from ${source.business_type_name || "the default emails"} – changes here only affect this campaign.`;
    els.edTypeFields.hidden = !isType;
    els.edLoadRow.hidden = isType;
    els.edSaveAs.hidden = isType;
    els.edDeleteType.hidden = !isType;
    if (isType) {
      els.edName.value = source.name;
      els.edSearch.value = source.search || "";
    } else {
      els.edLoad.innerHTML = "";
      els.edLoad.appendChild(new Option("—", ""));
      for (const t of state.types) els.edLoad.appendChild(new Option(t.name, t.id));
    }
    els.edTrade.value = source.trade || "";
    els.edFooter.value = source.footer || "";
    renderSteps(source.steps.map((s) => ({ ...s })));
    renderPlaceholderHelp();
  }

  function renderSteps(steps) {
    els.edSteps.innerHTML = "";
    steps.forEach((step, index) => els.edSteps.appendChild(stepBox(step, index)));
    els.edAddStep.hidden = steps.length >= 5;
    refreshEditor();
  }

  function stepBox(step, index) {
    const box = el("details", "step");
    box.open = state.openSteps.has(index);
    box.addEventListener("toggle", () => {
      if (box.open) state.openSteps.add(index); else state.openSteps.delete(index);
      updateExpandButton();
    });
    const summary = el("summary", "step-summary");
    summary.append(el("span", "check-mark"), el("strong", "", index === 0 ? "First email" : `Follow-up ${index}`),
      el("span", "muted step-day"), el("span", "step-glance"));
    if (index > 0) {
      const remove = el("button", "btn ghost small", "Remove");
      remove.type = "button";
      remove.title = "Remove this follow-up";
      remove.addEventListener("click", (e) => {
        e.preventDefault();                       // a click inside <summary> would also open / close the block
        const steps = collectSteps();
        steps.splice(index, 1);
        state.openSteps = new Set([...state.openSteps].filter((i) => i !== index).map((i) => (i > index ? i - 1 : i)));
        renderSteps(steps);
        markDirty();
      });
      summary.appendChild(remove);
    }
    box.appendChild(summary);

    const edit = el("div", "step-edit");
    if (index > 0) {
      const label = el("label", "inline day");
      const day = el("input", "st-day");
      day.type = "number"; day.min = "1"; day.max = "90"; day.value = String(step.day);
      label.append("Goes out on day", day, "after the first email");
      edit.appendChild(label);
    }
    const subject = el("input", "st-subject");
    subject.type = "text"; subject.maxLength = 200; subject.value = step.subject || "";
    subject.placeholder = index === 0 ? "Subject" : "Empty = “Re: ” + the first subject (same email thread)";
    const body = el("textarea", "st-body");
    body.rows = index === 0 ? 14 : 8; body.value = step.body || "";
    const subjectLabel = el("label", "", "Subject");
    subjectLabel.appendChild(subject);
    const bodyLabel = el("label", "", "Text");
    bodyLabel.appendChild(body);
    edit.append(subjectLabel, bodyLabel);

    const email = el("article", "email");
    const head = el("div", "email-head");
    const sent = el("span", "tag done sent-tag");
    sent.hidden = true;
    const test = el("button", "btn tiny", "✉ Send me this");
    test.type = "button";
    test.title = "Send this email, filled in like this, to your own address – [Test] in front of the subject";
    test.addEventListener("click", () => sendTest(index + 1, test));
    head.append(el("span", "muted email-to"), sent, test);
    email.append(head, el("div", "email-subject"), el("pre", "email-body"));
    const preview = el("div", "step-preview");
    preview.appendChild(email);

    const grid = el("div", "step-grid");
    grid.append(edit, preview);
    box.appendChild(grid);
    return box;
  }

  const stepBoxes = () => Array.from(els.edSteps.querySelectorAll(":scope > details.step"));

  function collectSteps() {
    return stepBoxes().map((box, index) => ({
      day: index === 0 ? 0 : Number(box.querySelector(".st-day").value || 0),
      subject: box.querySelector(".st-subject").value,
      body: box.querySelector(".st-body").value,
    }));
  }

  // the values the previews fill in: the picked business's (campaign) or a made-up one's (business type)
  function previewValues() {
    const p = state.preview;
    if (!p) return null;
    const values = state.view.kind === "type"
      ? (p.samples[Number(state.previewPick || 0)] || p.samples[0]).values : p.values;
    return { ...values, Trade: els.edTrade.value.trim() };          // what you are typing, not the saved one
  }

  // check marks, the headers' summaries and the previews - after every key press
  function refreshEditor() {
    const steps = collectSteps();
    const footer = els.edFooter.value;
    const values = previewValues();
    const sent = state.view.kind === "campaign" && state.preview && state.preview.emails ? state.preview.emails : [];
    const to = !state.preview ? "" : state.view.kind === "type" ? "To: Acme (made up)" : `To: ${state.preview.to}`;
    stepBoxes().forEach((box, index) => {
      const step = steps[index];
      const problems = [...templateProblems(step.subject), ...templateProblems(step.body)];
      if (index > 0 && !(step.day > steps[index - 1].day)) {
        problems.push(`goes out on day ${Number.isFinite(step.day) ? step.day : "?"} – that must come after ` +
          `day ${steps[index - 1].day}`);
      }
      const filled = step.body.trim() && (index > 0 || step.subject.trim());
      setMark(box.querySelector(".check-mark"), problems.length ? "error" : filled ? "done" : "todo",
        problems.join("\n"));
      box.querySelector(".step-day").textContent = `day ${index === 0 ? 0 : step.day || "?"}`;
      box.querySelector(".step-glance").textContent = step.subject.trim()
        || (index === 0 ? "no subject yet" : "Re: + the first subject");
      box.querySelector(".email-to").textContent = to;
      const email = values ? composeEmail(steps, footer, index, values) : null;
      box.querySelector(".email-subject").textContent = email ? email.subject : "";
      box.querySelector(".email-body").textContent = email ? email.body : "Loading the preview…";
      const sentAt = sent[index] && sent[index].sent_at;
      const tag = box.querySelector(".sent-tag");
      tag.hidden = !sentAt;
      tag.textContent = sentAt ? `sent to them ${when(sentAt)}` : "";
    });
    const texts = [footer, ...steps.flatMap((s) => [s.subject, s.body])];
    const needsTrade = texts.some((t) => t.includes("{Trade}"));
    const noTrade = needsTrade && !els.edTrade.value.trim();
    setMark(els.edTradeMark, noTrade ? "todo" : "done", noTrade ? "The emails use {Trade} – fill it in" : "");
    const footerProblems = templateProblems(footer);
    if (!footer.includes("{SenderAddress}")) footerProblems.push("needs {SenderAddress} – your postal address");
    setMark(els.edFooterMark, footerProblems.length ? "error" : "done", footerProblems.join("\n"));
    els.edFooterPreview.textContent = values ? fillTemplate(footer, values) : "";
    updateExpandButton();
  }

  function updateExpandButton() {
    const boxes = [...stepBoxes(), els.edFooterBox];
    els.edExpand.textContent = boxes.every((b) => b.open) ? "Close all" : "Open all";
  }

  els.edExpand.addEventListener("click", () => {
    const boxes = [...stepBoxes(), els.edFooterBox];
    const open = !boxes.every((b) => b.open);
    boxes.forEach((b) => { b.open = open; });
    updateExpandButton();
  });

  function markDirty() {
    state.dirty = true;
    els.edSave.disabled = false;
    refreshEditor();
  }

  els.editorForm.addEventListener("input", (e) => { if (e.target !== els.previewPick) markDirty(); });
  els.edFooterBox.addEventListener("toggle", updateExpandButton);
  els.editorForm.addEventListener("focusin", (e) => {
    if (e.target.matches("textarea, input[type=text]")) state.lastField = e.target;
  });

  els.edAddStep.addEventListener("click", () => {
    const steps = collectSteps();
    const last = steps[steps.length - 1];
    steps.push({ day: (last ? last.day : 0) + 4, subject: "", body: "Hi there,\n\n\n\n{SenderName}" });
    state.openSteps.add(steps.length - 1);
    renderSteps(steps);
    markDirty();
  });

  function renderPlaceholderHelp() {
    const s = state.status;
    if (!s) return;
    els.edChips.innerHTML = "";
    els.edChips.appendChild(el("span", "muted", "Insert:"));
    for (const [name, description] of Object.entries(s.placeholders)) {
      const chip = el("button", "ph-chip", `{${name}}`);
      chip.type = "button";
      chip.title = description;
      chip.addEventListener("mousedown", (e) => e.preventDefault());    // keep the cursor where it is
      chip.addEventListener("click", () => insertPlaceholder(`{${name}}`));
      els.edChips.appendChild(chip);
    }
    els.edHelp.innerHTML = "";
    const list = el("ul");
    for (const [name, description] of Object.entries(s.placeholders)) {
      const li = el("li");
      li.append(el("code", "", `{${name}}`), ` – ${description}`);
      list.appendChild(li);
    }
    els.edHelp.appendChild(list);
    els.edHelp.appendChild(el("p", "", "Optional parts: [[ in {City}]] is left out when {City} is empty. " +
      "[[A||B]] gives A when every placeholder in A has a value, else B – the first email uses it for the " +
      `stars: {Rating} and {Reviews} only have a value with at least ${s.min_reviews} reviews and ` +
      `${s.min_rating} stars.`));
    els.edHelp.appendChild(el("p", "", "Follow-ups without a subject answer the first email (Re: …), so they " +
      "stay in the same thread. The footer goes under every email."));
  }

  function insertPlaceholder(text) {
    let field = state.lastField;
    if (!field || !els.editorForm.contains(field)) field = els.edSteps.querySelector(".st-body");
    if (!field) return;
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    field.setRangeText(text, start, end, "end");
    field.focus();
    markDirty();
  }

  async function saveEditor() {
    const body = { trade: els.edTrade.value.trim(), steps: collectSteps(), footer: els.edFooter.value };
    els.edError.hidden = true;
    els.edSave.disabled = true;
    try {
      if (state.view.kind === "type") {
        body.name = els.edName.value.trim();
        body.search = els.edSearch.value.trim();
        const saved = await patch(`/api/business-types/${state.type.id}`, body);
        state.type = saved;
        renderEditor(saved, "type", { keepOpen: true });
        await loadTypes();
        window.panel.refreshBusinessTypes();
      } else {
        const saved = await patch(`/api/campaigns/${state.campaign.id}`, body);
        state.campaign = saved;
        renderEditor(saved, "campaign", { keepOpen: true });
        renderCampaign();
        renderLeads();
      }
      toast("Saved");
      loadPreview();
    } catch (err) {
      els.edError.textContent = err.message.split("; ").join("\n");
      els.edError.hidden = false;
      els.edSave.disabled = false;
    }
  }

  els.edSave.addEventListener("click", saveEditor);

  els.edLoad.addEventListener("change", () => {
    const type = state.types.find((t) => t.id === els.edLoad.value);
    els.edLoad.value = "";
    if (!type || !confirm(`Replace this campaign's emails with the ones of “${type.name}”? ` +
      "(Press Save to keep them.)")) return;
    els.edTrade.value = type.trade || "";
    els.edFooter.value = type.footer || "";
    renderSteps(type.steps.map((s) => ({ ...s })));
    markDirty();
  });

  els.edSaveAs.addEventListener("click", async () => {
    const suggestion = state.campaign ? state.campaign.business_type_name : "";
    const name = (prompt("Save these emails as the business type (its name is also the Drive folder):",
      suggestion) || "").trim();
    if (!name) return;
    const body = { trade: els.edTrade.value.trim(), steps: collectSteps(), footer: els.edFooter.value };
    const existing = state.types.find((t) => t.name.toLowerCase() === name.toLowerCase());
    try {
      if (existing) {
        if (!confirm(`Replace the emails of the business type “${existing.name}”?`)) return;
        await patch(`/api/business-types/${existing.id}`, body);
      } else {
        await post("/api/business-types", { ...body, name, search: name.toLowerCase() });
      }
      toast(`Saved as the business type “${existing ? existing.name : name}”`);
      await loadTypes();
      window.panel.refreshBusinessTypes();
    } catch (err) { toast(err.message, true); }
  });

  els.edDeleteType.addEventListener("click", async () => {
    const type = state.type;
    if (!confirm(`Delete the business type “${type.name}”? Its searches, campaigns and Drive files stay.`)) return;
    try {
      await api(`/api/business-types/${type.id}`, { method: "DELETE" });
      state.dirty = false;
      state.view = { kind: "intro" };
      state.type = null;
      show("intro");
      toast(`Business type “${type.name}” deleted`);
      await loadTypes();
      window.panel.refreshBusinessTypes();
    } catch (err) { toast(err.message, true); }
  });

  els.newTypeBtn.addEventListener("click", async () => {
    const name = (prompt("Name of the new business type (e.g. Dentist) – also its Google Drive folder:") || "").trim();
    if (!name) return;
    try {
      const created = await post("/api/business-types", { name, search: name.toLowerCase(), trade: "" });
      await loadTypes();
      window.panel.refreshBusinessTypes();
      await openType(created.id);
      toast("Created with the default emails – fill in what the emails call them, change the text, Save");
    } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------------
  // preview
  // ------------------------------------------------------------------
  async function loadPreview() {
    try {
      if (state.view.kind === "campaign" && state.campaign) {
        const query = state.previewPick ? `?lead_id=${encodeURIComponent(state.previewPick)}` : "";
        state.preview = await api(`/api/campaigns/${state.campaign.id}/preview${query}`);
        state.previewPick = state.preview.lead_id;
      } else if (state.view.kind === "type" && state.type) {
        state.preview = await api(`/api/business-types/${state.type.id}/preview`);
      } else {
        return;
      }
    } catch (err) {
      state.preview = null;
      els.previewPick.innerHTML = "";
      els.previewNote.textContent = err.message;
      refreshEditor();
      return;
    }
    renderPreviewPicker();
    refreshEditor();
  }

  function renderPreviewPicker() {
    const p = state.preview;
    const isType = state.view.kind === "type";
    els.previewPick.innerHTML = "";
    if (isType) {
      p.samples.forEach((sample, i) => els.previewPick.appendChild(new Option(`Example: ${sample.label}`, String(i))));
      els.previewPick.value = state.previewPick || "0";
    } else {
      for (const lead of state.campaign.leads) {
        els.previewPick.appendChild(new Option(`${lead.business} (${STATUS_TEXT[lead.status]})`, lead.id));
      }
      els.previewPick.value = p.lead_id;
    }
    const from = p.from || "you (set SENDER_NAME and MAIL_USERNAME)";
    els.previewNote.textContent = isType ? `From ${from} – “Acme” is a made-up business in Houston.`
      : `From ${from}` + (p.missing.length ? ` – no value for ${p.missing.map((m) => `{${m}}`).join(", ")}` : "");
  }

  els.previewPick.addEventListener("change", () => {
    state.previewPick = els.previewPick.value;
    if (state.view.kind === "type") refreshEditor(); else loadPreview();
  });

  async function sendTest(step, button) {
    if (state.dirty) {
      toast("Save first – the test email is made from the saved text", true);
      return;
    }
    const isType = state.view.kind === "type";
    const url = isType ? `/api/business-types/${state.type.id}/test` : `/api/campaigns/${state.campaign.id}/test`;
    const body = isType ? { step } : { step, lead_id: state.previewPick };
    button.disabled = true;
    try {
      const sent = await post(url, body);
      toast(`Test sent to ${sent.to}: “${sent.subject}”`);
    } catch (err) { toast(err.message, true); } finally { button.disabled = false; }
  }

  // ------------------------------------------------------------------
  // polling while the tab is open
  // ------------------------------------------------------------------
  async function poll() {
    await Promise.all([loadStatus(), loadCampaigns(), window.autopilotPanel ? window.autopilotPanel.refresh() : null]);
    if (state.view.kind === "campaign" && state.campaign) {
      try {
        state.campaign = await api(`/api/campaigns/${state.campaign.id}`);
      } catch {
        return;
      }
      renderCampaign();
      renderLeads();
    }
  }

  function visible(on) {
    state.visible = on;
    clearInterval(state.timer);
    state.timer = setInterval(on ? poll : loadCampaigns, on ? POLL_MS : BACKGROUND_POLL_MS);
    if (on) refreshAll().then(() => renderSetup());
  }

  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  window.outreachPanel = { visible, newCampaign: openNewCampaign, refresh: () => poll() };
  show("intro");
  loadCampaigns();
  state.timer = setInterval(loadCampaigns, BACKGROUND_POLL_MS);
})();
