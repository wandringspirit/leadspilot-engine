/* "Air" design (the "Bold" design's effects + the AIR ones): everything that moves. No libraries; every effect
   checks reduced motion. */
(() => {
  const root = document.documentElement;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = matchMedia("(hover: hover) and (pointer: fine)").matches;

  window.siteReady = true;
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.add("is-ready")));
  setTimeout(() => root.classList.add("is-ready"), 600);

  /* ---- preview countdown ---- */
  const timer = $("[data-ends]");
  if (timer) {
    const ends = new Date(timer.dataset.ends).getTime();
    const units = Object.fromEntries($$("[data-u]", timer).map((el) => [el.dataset.u, el]));
    const pad = (n) => String(n).padStart(2, "0");
    const tick = () => {
      let left = Math.max(0, Math.floor((ends - Date.now()) / 1000));
      if (!left) {
        timer.closest(".pv").classList.add("is-over");
        $(".pv-long").textContent = "This preview has ended.";
        $(".pv-short").textContent = "Ended";
        return;
      }
      units.d.textContent = Math.floor(left / 86400);
      units.h.textContent = pad(Math.floor((left % 86400) / 3600));
      units.m.textContent = pad(Math.floor((left % 3600) / 60));
      units.s.textContent = pad(left % 60);
      setTimeout(tick, 1000 - (Date.now() % 1000));
    };
    tick();
  }

  /* ---- header glass, scroll progress, mobile call bar ---- */
  const top = $("[data-top]");
  const progress = $("[data-progress]");
  const mbar = $("[data-mbar]");
  const hero = $("[data-hero]");
  let ticking = false;
  const onScroll = () => {
    const y = scrollY;
    top.classList.toggle("is-scrolled", y > 30);
    const max = document.body.scrollHeight - innerHeight;
    progress.style.transform = `scaleX(${max > 0 ? y / max : 0})`;
    if (mbar) mbar.classList.toggle("is-on", y > hero.offsetHeight * 0.6);
    steps();
    parallax();
    ticking = false;
  };
  addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });

  /* ---- mobile menu ---- */
  const burger = $("[data-burger]");
  const menu = $("[data-menu]");
  const setMenu = (open) => {
    burger.setAttribute("aria-expanded", String(open));
    menu.hidden = !open;
    document.body.style.overflow = open ? "hidden" : "";
    top.classList.toggle("is-scrolled", open || scrollY > 30);
  };
  burger.addEventListener("click", () => setMenu(burger.getAttribute("aria-expanded") !== "true"));
  menu.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
  addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) { setMenu(false); burger.focus(); } });

  /* ---- reveal on scroll + count-ups ---- */
  const countUp = (el) => {
    const end = parseFloat(el.dataset.count);
    const dec = parseInt(el.dataset.decimals || "0", 10);
    if (calm) { el.textContent = end.toFixed(dec); return; }
    const t0 = performance.now();
    const dur = 1600;
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      const eased = 1 - Math.pow(2, -10 * p);
      el.textContent = (end * (p === 1 ? 1 : eased)).toFixed(dec);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  const seen = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("is-in");
      $$("[data-count]", e.target).forEach(countUp);
      if (e.target.matches("[data-count]")) countUp(e.target);
      seen.unobserve(e.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
  $$("[data-reveal], [data-ring]").forEach((el) => seen.observe(el));

  /* ---- air (the "air" design): streams of air across the hero and the closing band. The thermostat sets
     their colour and way: cooling sinks, heating rises. The pointer pushes the air aside; a tap is a gust. ---- */
  let temp = { t: Number(getComputedStyle(root).getPropertyValue("--t")) || 0.48, mode: 0 };
  document.addEventListener("air:temp", (e) => { temp = e.detail; });
  const COOL = [76, 180, 255], WARM = [255, 138, 76];
  const air = (canvas, density) => {
    const ctx = canvas.getContext("2d");
    let parts = [], running = false, w = 0, h = 0, px = -9999, py = -9999, gust = 0, time = 0;
    const spawn = (p, anywhere) => {
      Object.assign(p, { trail: [], life: 0, speed: 0.55 + Math.random() * 1.1, phase: Math.random() * 6.3,
        a: 0.16 + Math.random() * 0.38, width: 0.8 + Math.random() * 1.7 });
      if (anywhere) { p.x = Math.random() * w; p.y = Math.random() * h; }
      else if (temp.mode > 0) { p.x = Math.random() * w; p.y = h + 12; }          // heating: from the floor
      else if (temp.mode < 0) { p.x = Math.random() * w; p.y = -12; }             // cooling: from the ceiling
      else { p.x = -20; p.y = Math.random() * h; }
      return p;
    };
    const size = () => {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      parts = Array.from({ length: Math.max(12, Math.round(Math.min(90, w / 15) * density)) }, (_, i) => spawn({ kind: i % 4 === 0 }, true));
    };
    const frame = () => {
      if (!running) return;
      time += 0.008;
      gust *= 0.95;
      ctx.clearRect(0, 0, w, h);
      ctx.lineCap = "round";
      const k = temp.t, mid = [94, 214, 170];
      const rgb = (k <= 0.5 ? COOL.map((c, i) => Math.round(c + (mid[i] - c) * k * 2))
        : mid.map((c, i) => Math.round(c + (WARM[i] - c) * (k - 0.5) * 2))).join(",");
      const lift = temp.mode * -1.1;
      for (const p of parts) {
        const sp = p.speed * (1 + gust);
        let vx = sp * (temp.mode ? 1.2 : 2.2) + Math.cos(p.y * 0.006 + time * 3 + p.phase) * 0.7;
        let vy = Math.sin(p.x * 0.004 + time * 2 + p.phase) * 0.8 + lift * p.speed;
        const dx = p.x - px, dy = p.y - py, d = Math.hypot(dx, dy);
        if (d < 130) { const f = (1 - d / 130) * 5; vx += (dx / (d || 1)) * f; vy += (dy / (d || 1)) * f; }
        p.x += vx; p.y += vy; p.life++;
        p.trail.push(p.x, p.y);
        if (p.trail.length > 44) p.trail.splice(0, 2);
        if (p.x > w + 40 || p.x < -60 || p.y < -50 || p.y > h + 50) { spawn(p, false); continue; }
        if (p.kind && (temp.t < 0.32 || temp.t > 0.7)) {           // a snowflake or an ember instead of a stream
          const cold = temp.t < 0.32, k = cold ? (0.32 - temp.t) / 0.32 : (temp.t - 0.7) / 0.3;
          ctx.fillStyle = cold ? `rgba(225,242,255,${(0.35 + 0.55 * k).toFixed(3)})` : `rgba(255,${150 + Math.round(60 * Math.sin(time * 9 + p.phase))},80,${(0.35 + 0.55 * k).toFixed(3)})`;
          ctx.beginPath(); ctx.arc(p.x, p.y, p.width * (cold ? 1.3 : 1.1), 0, Math.PI * 2); ctx.fill();
          continue;
        }
        const n = p.trail.length;
        if (n < 6) continue;
        const g = ctx.createLinearGradient(p.trail[0], p.trail[1], p.x, p.y);
        const alpha = p.a * Math.min(1, p.life / 40);
        g.addColorStop(0, `rgba(${rgb},0)`);
        g.addColorStop(1, `rgba(${rgb},${alpha.toFixed(3)})`);
        ctx.strokeStyle = g;
        ctx.lineWidth = p.width;
        ctx.beginPath();
        ctx.moveTo(p.trail[0], p.trail[1]);
        for (let i = 2; i < n; i += 2) ctx.lineTo(p.trail[i], p.trail[i + 1]);
        ctx.stroke();
      }
      requestAnimationFrame(frame);
    };
    size();
    addEventListener("resize", size);
    new IntersectionObserver(([e]) => {
      const was = running;
      running = e.isIntersecting;
      if (running && !was) requestAnimationFrame(frame);
    }).observe(canvas);
    const host = canvas.parentElement;
    const at = (e) => { const r = canvas.getBoundingClientRect(); px = e.clientX - r.left; py = e.clientY - r.top; };
    host.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse") at(e); }, { passive: true });
    host.addEventListener("pointerleave", () => { px = py = -9999; });
    host.addEventListener("pointerdown", (e) => { at(e); gust = 1.6; setTimeout(() => { if (e.pointerType !== "mouse") px = py = -9999; }, 500); }, { passive: true });
  };

  /* ---- rain (hero and closing band) + lightning (hero) ---- */
  const rain = (canvas) => {
    const density = Number(canvas.dataset.rain || 1);
    if (!(density > 0) || calm) return;
    if (canvas.dataset.fx === "air") return air(canvas, density);
    const ctx = canvas.getContext("2d");
    const flash = canvas.closest("[data-hero]") ? $("[data-flash]") : null;
    let drops = [];
    let running = false;
    let w = 0, h = 0;
    const size = () => {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.round(Math.min(160, (w * h) / 9000) * density);
      drops = Array.from({ length: count }, () => ({
        x: Math.random() * (w + 200), y: Math.random() * h, len: 10 + Math.random() * 22, v: 9 + Math.random() * 10, a: 0.08 + Math.random() * 0.22,
      }));
    };
    const wind = 0.22;
    const frame = () => {
      if (!running) return;
      ctx.clearRect(0, 0, w, h);
      ctx.lineCap = "round";
      ctx.lineWidth = 1;
      for (const d of drops) {
        ctx.strokeStyle = `rgba(200,215,240,${d.a})`;
        ctx.beginPath();
        ctx.moveTo(d.x, d.y);
        ctx.lineTo(d.x - d.len * wind, d.y + d.len);
        ctx.stroke();
        d.y += d.v; d.x -= d.v * wind;
        if (d.y > h) { d.y = -d.len; d.x = Math.random() * (w + 200); }
      }
      requestAnimationFrame(frame);
    };
    size();
    addEventListener("resize", size);
    new IntersectionObserver(([e]) => {
      const was = running;
      running = e.isIntersecting;
      if (running && !was) requestAnimationFrame(frame);
    }).observe(canvas);
    if (flash) {
      const strike = () => {
        if (running && document.visibilityState === "visible") {
          flash.classList.remove("is-on"); void flash.offsetWidth; flash.classList.add("is-on");
        }
        setTimeout(strike, 7000 + Math.random() * 9000);
      };
      setTimeout(strike, 2600);
    }
  };
  $$("[data-rain]").forEach(rain);

  /* ---- tilt cards + magnetic buttons (mouse only) ---- */
  if (finePointer && !calm) {
    $$("[data-tilt]").forEach((card) => {
      card.addEventListener("pointermove", (e) => {
        const r = card.getBoundingClientRect();
        const px = (e.clientX - r.left) / r.width - 0.5;
        const py = (e.clientY - r.top) / r.height - 0.5;
        card.style.setProperty("--ry", `${px * 6}deg`);
        card.style.setProperty("--rx", `${-py * 6}deg`);
      });
      card.addEventListener("pointerleave", () => { card.style.setProperty("--rx", "0deg"); card.style.setProperty("--ry", "0deg"); });
    });
    $$("[data-magnetic]").forEach((btn) => {
      btn.addEventListener("pointermove", (e) => {
        const r = btn.getBoundingClientRect();
        btn.style.setProperty("--mx", `${(e.clientX - r.left - r.width / 2) * 0.18}px`);
        btn.style.setProperty("--my", `${(e.clientY - r.top - r.height / 2) * 0.3}px`);
      });
      btn.addEventListener("pointerleave", () => { btn.style.setProperty("--mx", "0px"); btn.style.setProperty("--my", "0px"); });
    });
  }

  /* ---- roof check: pins on the photo, a card per pin, arrows to step through ---- */
  const zoom = $("[data-zoom]");
  if (zoom) {
    const pins = $$(".pin", zoom);
    const keys = pins.map((p) => p.dataset.spot);
    let current = keys[0];
    const show = (key, closer = true) => {
      current = key;
      pins.forEach((p) => {
        const on = p.dataset.spot === key;
        p.classList.toggle("is-on", on);
        p.setAttribute("aria-pressed", String(on));
        if (on && closer && !calm) {
          zoom.style.transformOrigin = `${p.dataset.x}% ${p.dataset.y}%`;
          zoom.classList.add("is-zoomed");
        }
      });
      $$("[data-card]").forEach((c) => { c.hidden = c.dataset.card !== key; });
      $$("[data-dot]").forEach((d) => d.classList.toggle("is-on", d.dataset.dot === key));
    };
    pins.forEach((p) => p.addEventListener("click", () => show(p.dataset.spot)));
    $$("[data-xp-step]").forEach((b) => b.addEventListener("click", () => {
      const i = keys.indexOf(current) + Number(b.dataset.xpStep);
      show(keys[(i + keys.length) % keys.length]);
    }));
    show(current, false);
  }

  /* ---- process line ---- */
  const stepsWrap = $(".steps-wrap");
  const stepEls = $$(".step");
  function steps() {
    if (!stepsWrap) return;
    const r = stepsWrap.getBoundingClientRect();
    const p = Math.min(1, Math.max(0, (innerHeight * 0.75 - r.top) / (r.height || 1)));
    stepsWrap.style.setProperty("--p", calm ? 1 : p.toFixed(3));
    stepEls.forEach((s, i) => s.classList.toggle("is-lit", calm || p >= i / stepEls.length + 0.02));
  }

  /* ---- parallax ---- */
  const para = $$("[data-parallax]");
  function parallax() {
    if (calm) return;
    for (const el of para) {
      const r = el.parentElement.getBoundingClientRect();
      if (r.bottom < 0 || r.top > innerHeight) continue;
      const p = (r.top + r.height / 2 - innerHeight / 2) / innerHeight;
      el.style.transform = `translateY(${(-p * 8).toFixed(2)}%)`;
    }
  }

  /* ---- gallery: drag to scroll + progress ---- */
  const rail = $("[data-rail]");
  const railBar = $("[data-rail-bar]");
  if (rail) {
    const update = () => {
      const max = rail.scrollWidth - rail.clientWidth;
      const view = rail.clientWidth / rail.scrollWidth;
      railBar.style.transform = `translateX(${max > 0 ? (rail.scrollLeft / max) * (1 / view - 1) * 100 : 0}%) scaleX(1)`;
      railBar.style.width = `${view * 100}%`;
    };
    rail.addEventListener("scroll", update, { passive: true });
    addEventListener("resize", update);
    update();
    let down = null;
    rail.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "mouse") return;
      down = { x: e.clientX, left: rail.scrollLeft, moved: false };
      rail.classList.add("is-drag");
    });
    addEventListener("pointermove", (e) => {
      if (!down) return;
      const dx = e.clientX - down.x;
      if (Math.abs(dx) > 4) down.moved = true;
      rail.scrollLeft = down.left - dx;
    });
    addEventListener("pointerup", () => { down = null; rail.classList.remove("is-drag"); });
  }

  /* ---- quote form ---- */
  const form = $("[data-quote]");
  if (form) {
    // one form, everything visible; the Quick start chips fill the "what's going on" dropdown
    const next = $("[data-qf-next]", form);
    const err = $("[data-qf-error]", form);
    const need = form.elements.problem;
    const chips = $$("[data-chip]", form);
    const sync = () => {
      const val = need.selectedOptions[0] ? need.selectedOptions[0].dataset.val : "";
      chips.forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.chip === val)));
    };
    const pick = (val) => {
      const option = $$("option", need).find((o) => o.dataset.val === val);
      if (!option) return;
      need.value = option.value;
      need.removeAttribute("aria-invalid");
      err.hidden = true;
      sync();
    };
    chips.forEach((c) => c.addEventListener("click", () => pick(c.dataset.chip)));
    need.addEventListener("change", sync);
    // a service card or "we'll check it for free" link elsewhere on the page picks it too
    // a pack can say which option each service picks (data-for: {"for": {slug: option}, "else": option})
    const own = form.dataset.for ? JSON.parse(form.dataset.for) : null;
    const SERVICE = own ? own.for : { "storm-damage": "storm", "leak-detection": "leak", "roof-replacement": "replace", "roof-repair": "leak" };
    const OTHER = own ? own.else : "check";
    $$("[data-pick]").forEach((a) => a.addEventListener("click", () => pick(SERVICE[a.dataset.pick] || OTHER)));
    $$("[data-service]").forEach((a) => a.addEventListener("click", () => pick(SERVICE[a.dataset.service] || OTHER)));
    const fields = $$("input[required], select[required]", form);
    fields.forEach((f) => f.addEventListener("input", () => { f.removeAttribute("aria-invalid"); err.hidden = true; }));
    const valid = () => {
      let first = null, message = "";
      for (const f of fields) {
        const bad = !f.value.trim() || (f.type === "tel" && f.value.replace(/\D/g, "").length < 10);
        f.setAttribute("aria-invalid", String(bad));
        if (bad && !first) {
          first = f;
          message = f === need ? "Please choose what you need."
            : f.type === "tel" ? "Please enter a 10-digit phone number."
            : `Please fill in "${f.labels[0].firstElementChild.textContent}".`;
        }
      }
      if (first) first.focus();
      return message;
    };
    let sending = false;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (sending) return;
      const problem = valid();
      if (problem) { err.textContent = problem; err.hidden = false; return; }
      const data = Object.fromEntries(new FormData(form));
      const endpoint = form.dataset.endpoint;
      if (endpoint) {
        sending = true;
        next.disabled = true;
        let sent = false;
        try {
          const page = { slug: decodeURIComponent(location.pathname.split("/")[1] || ""), copy: new URLSearchParams(location.search).has("copy") };
          const answer = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...data, ...page }) });
          sent = answer.ok;
        } catch { /* said below */ }
        sending = false;
        next.disabled = false;
        if (!sent) {                                     // never thank someone whose request went nowhere
          const phone = form.dataset.phone;
          err.textContent = phone ? `That didn't go through. Please call ${phone}.` : "That didn't go through. Please try again.";
          err.hidden = false;
          return;
        }
      }
      $("[data-qf-name]", form).textContent = data.name ? `, ${data.name.split(" ")[0]}` : "";
      form.classList.add("is-done");
      $("[data-qf-done]", form).hidden = false;
      if (!endpoint && form.dataset.email && !root.closest(".is-preview") && !document.body.classList.contains("is-preview")) {
        const body = Object.entries(data).map(([k, v]) => `${k}: ${v}`).join("\n");
        location.href = `mailto:${form.dataset.email}?subject=${encodeURIComponent("Inspection request")}&body=${encodeURIComponent(body)}`;
      }
    });
    sync();
  }

  /* ---- FAQ: smooth open/close ---- */
  if (!calm) {
    $$(".faq-list details").forEach((d) => {
      const summary = $("summary", d);
      const body = $(".faq-a", d);
      summary.addEventListener("click", (e) => {
        e.preventDefault();
        if (d.open) {
          body.animate([{ height: `${body.offsetHeight}px` }, { height: "0px" }], { duration: 320, easing: "cubic-bezier(.2,.8,.2,1)" }).onfinish = () => { d.open = false; };
        } else {
          d.open = true;
          body.animate([{ height: "0px" }, { height: `${body.offsetHeight}px` }], { duration: 380, easing: "cubic-bezier(.2,.8,.2,1)" });
        }
      });
    });
  }

  /* ---- right now: the helper's tabs (arrow keys move between them) ---- */
  const tabs = $$("[data-help] [role=tab]");
  const showTab = (tab, focus) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
    if (focus) tab.focus();
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => {
      showTab(t);
      if (innerWidth <= 900) {
        document.getElementById(t.getAttribute("aria-controls")).scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
      }
    });
    t.addEventListener("keydown", (e) => {
      const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      e.preventDefault();
      showTab(tabs[(i + step + tabs.length) % tabs.length], true);
    });
  });

  /* ---- the thermostat: drag it round, use the arrow keys or the buttons. Like a real one it cools or heats until
     the room reaches the setting, then holds; the page's colour (--temp), its photo and its air follow. ---- */
  const dial = $("[data-dial]");
  if (dial) {
    const ring = $("[data-dial-ring]", dial), fill = $(".dial-fill", dial), ds = dial.dataset;
    const roomCard = $("[data-room-card]"), roomEl = $("[data-room]"), roomState = $("[data-room-state]");
    const min = Number(ds.min), max = Number(ds.max);
    const STOPS = [[0, [76, 180, 255]], [0.5, [94, 214, 170]], [1, [255, 138, 76]]];     // icy blue, mint, warm
    const colour = (p) => {
      const i = p <= 0.5 ? 0 : 1, [p0, c0] = STOPS[i], [p1, c1] = STOPS[i + 1], k = (p - p0) / (p1 - p0);
      return `rgb(${c0.map((c, j) => Math.round(c + (c1[j] - c) * k)).join(",")})`;
    };
    let value = Number(ring.getAttribute("aria-valuenow")), room = Number(ds.roomStart) || value;
    let dragging = false, demo = null, drift = null;
    const show = () => {
      const p = (value - min) / (max - min);
      const mode = room > value ? -1 : room < value ? 1 : 0;           // -1 cooling, 1 heating, 0 holding
      const word = mode < 0 ? ds.cool : mode > 0 ? ds.heat : ds.hold;
      dial.style.setProperty("--p", p.toFixed(3));
      fill.style.strokeDasharray = `${(p * 75).toFixed(2)} 100`;
      root.style.setProperty("--t", p.toFixed(3));
      root.style.setProperty("--temp", colour(p));
      $("[data-dial-val]", dial).textContent = value;
      $("[data-dial-mode]", dial).textContent = word;
      ring.setAttribute("aria-valuenow", value);
      ring.setAttribute("aria-valuetext", `${word} ${value}°F`);
      if (roomCard) {
        roomState.textContent = mode < 0 ? ds.cooling : mode > 0 ? ds.heating : ds.steady;
        roomCard.classList.toggle("is-steady", !mode);
      }
      document.dispatchEvent(new CustomEvent("air:temp", { detail: { t: p, mode } }));
    };
    const step = () => {                                // the room: one degree at a time towards the setting
      drift = null;
      if (room === value) return;
      room += room < value ? 1 : -1;
      if (roomEl) {
        roomEl.textContent = room;
        roomEl.parentElement.classList.remove("is-tick"); void roomEl.offsetWidth; roomEl.parentElement.classList.add("is-tick");
      }
      show();
      drift = setTimeout(step, calm ? 0 : 380);
    };
    const set = (v) => {
      value = Math.max(min, Math.min(max, Math.round(v)));
      show();
      if (!drift) drift = setTimeout(step, calm ? 0 : 700);
    };
    const stopDemo = () => { clearTimeout(demo); demo = null; };
    const fromPointer = (e) => {
      const r = ring.getBoundingClientRect();
      const deg = Math.atan2(e.clientX - (r.left + r.width / 2), -(e.clientY - (r.top + r.height / 2))) * 180 / Math.PI;
      set(min + ((Math.max(-135, Math.min(135, deg)) + 135) / 270) * (max - min));    // 0 = up; the gap at the bottom
    };
    ring.addEventListener("pointerdown", (e) => { stopDemo(); dragging = true; ring.setPointerCapture(e.pointerId); fromPointer(e); });
    ring.addEventListener("pointermove", (e) => { if (dragging) fromPointer(e); });
    ring.addEventListener("pointerup", () => { dragging = false; });
    ring.addEventListener("pointercancel", () => { dragging = false; });
    ring.addEventListener("keydown", (e) => {
      const keys = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 5, PageDown: -5 };
      const to = e.key === "Home" ? min : e.key === "End" ? max : e.key in keys ? value + keys[e.key] : null;
      if (to === null) return;
      e.preventDefault(); stopDemo(); set(to);
    });
    $$("[data-dial-step]", dial).forEach((b) => b.addEventListener("click", () => { stopDemo(); set(value + Number(b.dataset.dialStep)); }));
    set(value);
    // once, on arrival, after the room has settled: a cold snap, then a warm spell, then back - so it's plain the dial
    // does something. Any touch of the dial stops it.
    if (!calm) {
      const home = value, path = [];
      for (let v = home - 1; v >= home - 7; v--) path.push(v);
      for (let v = home - 6; v <= home + 6; v++) path.push(v);
      for (let v = home + 5; v >= home; v--) path.push(v);
      const go = (i) => { if (i < path.length) { set(path[i]); demo = setTimeout(() => go(i + 1), i === 6 || i === 19 ? 3400 : 90); } };
      demo = setTimeout(() => go(0), 5200);
    }
  }

  /* ---- is your old AC costing you? Cooling energy goes with 1 / SEER: the saving is the bill x (1 - old / new) ---- */
  const ec = $("[data-ecalc]");
  if (ec) {
    const input = $("[data-ec-in]", ec), art = $(".ec-art", ec);
    const newSeer = Number(ec.dataset.new), months = Number(ec.dataset.months), max = Number(ec.dataset.max);
    const money = (n) => "$" + Math.round(n).toLocaleString("en-US");
    const set = () => {
      const bill = Number(input.value);
      const old = Number(($("input[name=ec-age]:checked", ec) || {}).value) || newSeer;
      const save = bill * (1 - old / newSeer);
      $("[data-ec-bill]", ec).textContent = money(bill);
      $("[data-ec-month]", ec).textContent = money(save);
      $("[data-ec-season]", ec).textContent = money(save * months);
      $("[data-ec-old]", ec).textContent = `${old} SEER`;
      art.style.setProperty("--old-h", (bill / max).toFixed(3));
      art.style.setProperty("--new-h", ((bill * old) / newSeer / max).toFixed(3));
      input.style.setProperty("--p", `${((bill - Number(input.min)) / (Number(input.max) - Number(input.min))) * 100}%`);
    };
    input.addEventListener("input", set);
    $$("input[name=ec-age]", ec).forEach((r) => r.addEventListener("change", set));
    set();
  }

  onScroll();
})();
