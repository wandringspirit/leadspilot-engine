/* Leads Pilot Engine - Settings tab: every setting of the .env file, grouped and explained. Saving
 * writes .env, and the server uses the new values right away (the Panel group's after a restart). Also the
 * panel's Restart and Stop buttons. Talks to /api/settings and /api/server/* (docs/API.md); shares its
 * helpers with app.js through window.panel. */
(() => {
  "use strict";

  const { api, toast } = window.panel;
  const $ = (id) => document.getElementById(id);
  const NEEDED_FOR = { email: "needed to send emails", drive: "needed for Google Drive" };
  const GMAIL = { SMTP_HOST: "smtp.gmail.com", SMTP_PORT: "587", IMAP_HOST: "imap.gmail.com", IMAP_PORT: "993" };

  const els = {
    nav: $("settings-nav"), file: $("settings-file"), facts: $("server-facts"), restartBtn: $("restart-btn"),
    stopBtn: $("stop-btn-2"), note: $("server-note"), pending: $("settings-pending"),
    pendingText: $("settings-pending-text"), restartNow: $("settings-restart"), fileError: $("settings-file-error"),
    fileErrorText: $("settings-file-error-text"), form: $("settings-form"), groups: $("settings-groups"),
    bar: $("settings-bar"), barText: $("settings-bar-text"), discard: $("settings-discard"),
    save: $("settings-save"), tabCount: $("tab-settings-count"),
  };

  const state = {
    data: null,          // GET /api/settings
    edits: new Map(),    // name -> new value: text, true/false, or null = back to the default
    errors: {},          // name -> what the last save said about it
    rows: new Map(),     // name -> {row, input, error, field}
    visible: false,
    focus: null,         // a group to scroll to once the settings are shown
    saving: false,
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
  const fieldOf = (name) => state.data && state.data.fields.find((f) => f.name === name);

  // ------------------------------------------------------------------
  // values: what the box shows, and whether a setting is still empty
  // ------------------------------------------------------------------
  function shown(field) {
    if (state.edits.has(field.name)) {
      const edit = state.edits.get(field.name);
      if (edit === null) return field.kind === "bool" ? field.default : (field.kind === "secret" ? "" : field.default);
      return edit;
    }
    return field.kind === "secret" ? "" : field.value;
  }

  function isEmpty(field) {
    if (field.kind === "secret") {
      const edit = state.edits.get(field.name);
      return state.edits.has(field.name) ? !edit : !field.is_set;
    }
    return field.kind !== "bool" && !String(shown(field) || "").trim();
  }

  // settings that must be filled in before emails can go out (the Settings tab's red count)
  function missingForEmail() {
    if (!state.data) return [];
    return state.data.fields.filter((f) => f.needed === "email" && !f.locked && isEmpty(f));
  }

  // ------------------------------------------------------------------
  // loading + rendering
  // ------------------------------------------------------------------
  async function load() {
    try {
      state.data = await api("/api/settings");
    } catch (err) {
      if (state.visible) els.groups.replaceChildren(el("p", "error", `Can't load the settings: ${err.message}`));
      return;
    }
    render();
  }

  function render() {
    const data = state.data;
    if (!data) return;
    state.rows.clear();
    const fragment = document.createDocumentFragment();
    for (const group of data.groups) {
      const fields = data.fields.filter((f) => f.group === group.key);
      const card = el(group.advanced ? "details" : "section", "card settings-group");
      card.id = `settings-${group.key}`;
      const head = el(group.advanced ? "summary" : "div", "card-head");
      head.appendChild(el("h2", "", group.title));
      if (group.key === "mailbox") head.appendChild(gmailButton());
      card.appendChild(head);
      if (group.help) card.appendChild(el("p", "muted group-help", group.help));
      for (const field of fields) card.appendChild(renderField(field));
      fragment.appendChild(card);
    }
    els.groups.replaceChildren(fragment);
    els.file.textContent = `Saved in ${data.env_file}`;
    els.fileError.hidden = !data.file_error;
    els.fileErrorText.textContent = data.file_error ?
      `The .env file has a value the server can't use – ${data.file_error}. Fix it here and save.` : "";
    renderMarks();
    renderServer();
    renderPending();
    updateBar();
    showErrors();
    scrollToFocus();
  }

  function renderField(field) {
    const row = el("div", "setting");
    const id = `set-${field.name}`;
    const label = el("label", "setting-label");
    label.htmlFor = id;
    const title = el("span", "setting-title", field.label);
    if (field.restart) title.appendChild(el("span", "chip small restart", "after a restart"));
    label.append(title, el("code", "setting-name", field.name));
    if (field.help) label.appendChild(el("small", "setting-help", field.help));

    const control = el("div", "setting-control");
    const line = el("div", "input-row");
    let input;
    if (field.kind === "bool") {
      const wrap = el("label", "switch");
      input = el("input");
      input.type = "checkbox";
      input.checked = !!shown(field);
      const text = el("span", "", input.checked ? "On" : "Off");
      input.addEventListener("change", () => { text.textContent = input.checked ? "On" : "Off"; });
      wrap.append(input, text);
      line.appendChild(wrap);
    } else if (field.kind === "choice") {
      input = el("select");
      for (const choice of field.choices) {
        const option = el("option", "", choice);
        option.value = choice;
        input.appendChild(option);
      }
      input.value = shown(field);
      line.appendChild(input);
    } else {
      input = el("input");
      input.spellcheck = false;
      if (field.kind === "int" || field.kind === "float") {
        input.type = "number";
        input.step = field.kind === "int" ? "1" : "any";
        if (field.min !== null) input.min = field.min;
        if (field.max !== null) input.max = field.max;
      } else {
        input.type = field.kind === "secret" ? "password" : "text";
      }
      if (field.kind === "secret") {
        input.autocomplete = "new-password";
        input.placeholder = secretPlaceholder(field);
      } else {
        input.placeholder = field.placeholder ? `e.g. ${field.placeholder}` : (field.default ? field.default : "");
      }
      input.value = shown(field);
      line.appendChild(input);
      if (field.kind === "secret") line.appendChild(revealButton(input));
    }
    input.id = id;
    input.name = field.name;
    input.addEventListener(field.kind === "bool" || field.kind === "choice" ? "change" : "input",
      () => edited(field, input));
    control.appendChild(line);

    if (field.locked) {
      input.disabled = true;
      control.appendChild(el("small", "setting-note",
        `Set by the environment variable ${field.locked} on this computer – change or remove it there.`));
    } else {
      const reset = resetButton(field, input);
      if (reset) control.appendChild(reset);
    }
    const error = el("small", "setting-error");
    error.hidden = true;
    control.appendChild(error);
    row.append(label, control);
    state.rows.set(field.name, { row, input, error, field });
    markRow(field, false);
    return row;
  }

  function secretPlaceholder(field) {
    if (state.edits.has(field.name) && state.edits.get(field.name) === null) return "removed when you save";
    if (field.is_set) return "saved – type to replace";
    return field.placeholder ? `e.g. ${field.placeholder}` : "not set";
  }

  function revealButton(input) {
    const button = el("button", "btn ghost small", "Show");
    button.type = "button";
    button.title = "Show what you typed";
    button.addEventListener("click", () => {
      const hidden = input.type === "password";
      input.type = hidden ? "text" : "password";
      button.textContent = hidden ? "Hide" : "Show";
    });
    return button;
  }

  // "Use the default (587)" for a setting .env sets, "Remove the saved password" for a secret
  function resetButton(field, input) {
    if (!field.saved && !state.edits.has(field.name)) return null;
    if (field.kind === "secret") {
      if (!field.is_set) return null;
      const button = el("button", "link-btn", "Remove the saved value");
      button.type = "button";
      button.addEventListener("click", () => {
        state.edits.set(field.name, null);
        input.value = "";
        input.placeholder = secretPlaceholder(field);
        markRow(field);
        updateBar();
      });
      return button;
    }
    const fallback = field.kind === "bool" ? (field.default ? "on" : "off") : (field.default || "empty");
    const button = el("button", "link-btn", `Use the default (${fallback})`);
    button.type = "button";
    button.addEventListener("click", () => {
      state.edits.set(field.name, null);
      if (field.kind === "bool") {
        input.checked = !!field.default;
        input.dispatchEvent(new Event("change"));
        state.edits.set(field.name, null);        // the change handler compared values - keep "default"
      } else {
        input.value = field.default;
      }
      markRow(field);
      updateBar();
    });
    return button;
  }

  function gmailButton() {
    const button = el("button", "btn small", "Use Gmail's servers");
    button.type = "button";
    button.title = "Fill in smtp.gmail.com / imap.gmail.com and their ports";
    button.addEventListener("click", () => {
      for (const [name, value] of Object.entries(GMAIL)) {
        const entry = state.rows.get(name);
        if (!entry || entry.field.locked) continue;
        entry.input.value = value;
        edited(entry.field, entry.input);
      }
      const user = state.rows.get("MAIL_USERNAME");
      if (user && !user.input.value) user.input.focus();
    });
    return button;
  }

  function edited(field, input) {
    let value = field.kind === "bool" ? input.checked : input.value;
    let same;
    if (field.kind === "bool") same = value === field.value;
    else if (field.kind === "secret") same = value === "";
    else same = String(value).trim() === String(field.value).trim();
    if (same) state.edits.delete(field.name);
    else state.edits.set(field.name, value);
    delete state.errors[field.name];
    markRow(field);
    updateBar();
  }

  function markRow(field, marks = true) {
    const entry = state.rows.get(field.name);
    if (!entry) return;
    entry.row.classList.toggle("changed", state.edits.has(field.name));
    const needed = field.needed && !field.locked && isEmpty(field);
    entry.row.classList.toggle("needed", !!needed);
    entry.row.title = needed ? NEEDED_FOR[field.needed] : "";
    const error = state.errors[field.name];
    entry.row.classList.toggle("invalid", !!error);
    entry.error.hidden = !error;
    entry.error.textContent = error || "";
    if (marks) renderMarks();
  }

  // the group list: a dot for unsaved changes, a red count for what's still needed
  function renderMarks() {
    if (!state.data) return;
    els.nav.replaceChildren();
    for (const group of state.data.groups) {
      const fields = state.data.fields.filter((f) => f.group === group.key);
      const link = el("a", "", group.title);
      link.href = `#settings-${group.key}`;
      link.addEventListener("click", (event) => { event.preventDefault(); open(group.key); });
      const marks = el("span", "nav-marks");
      if (fields.some((f) => state.edits.has(f.name))) {
        const dot = el("span", "nav-dot");
        dot.title = "Unsaved changes";
        marks.appendChild(dot);
      }
      const needed = fields.filter((f) => f.needed && !f.locked && isEmpty(f)).length;
      if (needed) {
        const count = el("span", "count warn", String(needed));
        count.title = `${plural(needed, "setting")} still to fill in`;
        marks.appendChild(count);
      }
      link.appendChild(marks);
      els.nav.appendChild(link);
    }
    const missing = missingForEmail().length;
    els.tabCount.hidden = !missing;
    els.tabCount.textContent = String(missing);
  }

  function renderServer() {
    const server = state.data.server || {};
    els.facts.replaceChildren();
    const facts = [["Address", server.url || location.origin], ["Settings file", state.data.env_file]];
    for (const [term, value] of facts) els.facts.append(el("dt", "", term), el("dd", "", value));
    els.restartBtn.hidden = !server.can_restart;
    els.stopBtn.hidden = !server.can_stop;
    els.note.textContent = server.can_stop ? (server.can_restart ? "" :
      "Restart: stop the panel here, then start it again with the desktop shortcut.")
      : "Started from a terminal: stop it there with Ctrl+C.";
  }

  function renderPending() {
    const pending = (state.data.pending || []).map(fieldOf).filter(Boolean);
    els.pending.hidden = !pending.length;
    if (!pending.length) return;
    const names = pending.map((f) => `${f.label} (${f.name})`).join(", ");
    const server = state.data.server || {};
    els.pendingText.textContent = server.can_restart ? `${names}. Restart the panel to use the new values.`
      : `${names}. Stop the panel and start it again (desktop shortcut) to use the new values.`;
    els.restartNow.hidden = !server.can_restart;
  }

  function updateBar() {
    const count = state.edits.size;
    els.bar.hidden = !count;
    els.barText.textContent = `${plural(count, "unsaved change")}`;
  }

  function showErrors() {
    for (const [name, entry] of state.rows) {
      const error = state.errors[name];
      entry.row.classList.toggle("invalid", !!error);
      entry.error.hidden = !error;
      entry.error.textContent = error || "";
    }
  }

  function scrollToFocus() {
    if (!state.focus || !state.visible) return;
    const card = $(`settings-${state.focus}`);
    state.focus = null;
    if (!card) return;
    if (card.tagName === "DETAILS") card.open = true;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.remove("flash");
    void card.offsetWidth;                      // restart the animation
    card.classList.add("flash");
    const first = Array.from(card.querySelectorAll(".setting.needed input, .setting input"))
      .find((input) => !input.disabled);
    if (first) setTimeout(() => first.focus({ preventScroll: true }), 400);
  }

  // ------------------------------------------------------------------
  // saving
  // ------------------------------------------------------------------
  async function save(event) {
    event.preventDefault();
    if (!state.edits.size || state.saving) return;
    state.saving = true;
    els.save.disabled = true;
    try {
      const result = await api("/api/settings", {
        method: "PATCH", body: JSON.stringify({ values: Object.fromEntries(state.edits) }),
      });
      state.edits.clear();
      state.errors = {};
      state.data = result;
      render();
      const labels = (names) => names.map((name) => (fieldOf(name) || { label: name }).label).join(", ");
      if (!result.changed.length) toast("Saved – nothing changed");
      else if (result.restart.length) toast(`Saved. After a restart: ${labels(result.restart)}`);
      else toast(`Saved – in use now: ${labels(result.changed)}`);
      afterSave();
    } catch (err) {
      if (err.detail && err.detail.fields) {
        state.errors = { ...err.detail.fields };
        showErrors();
        renderMarks();
        const first = Object.keys(state.errors).map((name) => state.rows.get(name)).find(Boolean);
        if (first) first.row.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      toast(err.message, true);
    } finally {
      state.saving = false;
      els.save.disabled = false;
    }
  }

  // the rest of the panel shows what the new settings change (the setup checklist, Drive, the search form)
  function afterSave() {
    if (window.outreachPanel) window.outreachPanel.refresh();
    window.panel.refreshDrive();
    window.panel.refreshConfig();
  }

  function discard() {
    state.edits.clear();
    state.errors = {};
    render();
  }

  // ------------------------------------------------------------------
  // wiring
  // ------------------------------------------------------------------
  function open(groupKey) {
    state.focus = groupKey || null;
    if (!state.visible) window.panel.showView("settings");      // calls visible(true), which loads
    else scrollToFocus();
  }

  function visible(on) {
    state.visible = on;
    if (!on) return;
    if (state.edits.size && state.data) render();     // keep what's being edited
    else load();
  }

  els.form.addEventListener("submit", save);
  els.discard.addEventListener("click", discard);
  els.restartBtn.addEventListener("click", () => window.panel.restartServer());
  els.restartNow.addEventListener("click", () => window.panel.restartServer());
  els.stopBtn.addEventListener("click", () => window.panel.stopServer());
  window.addEventListener("beforeunload", (event) => {
    if (!state.edits.size) return;
    event.preventDefault();
    event.returnValue = "";                     // "Leave site? Changes you made may not be saved."
  });

  window.settingsPanel = { visible, open, refresh: load };
  load();                                       // for the Settings tab's count of what's still needed
})();
