/* "Spark" design (the "Bold" design's effects + the SPARK ones): everything that moves. No libraries; every effect
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

  /* ---- spark (the "spark" design): circuit traces across the hero and the closing band, current running
     along them; near the mouse an arc jumps to the nearest trace, a tap throws sparks. The switch sets how lit. ---- */
  let power = 0.8;                                   // 0 off .. 1: the wall switch and the dimmer
  document.addEventListener("spark:power", (e) => { power = e.detail; });
  const hexRgb = (hex) => {
    const m = /^#?([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(hex.trim());
    return m ? m.slice(1).map((x) => parseInt(x, 16)).join(",") : "255,210,63";
  };
  const spark = (canvas, density) => {
    const ctx = canvas.getContext("2d");
    const c = hexRgb(getComputedStyle(root).getPropertyValue("--accent"));
    let traces = [], pulses = [], bits = [], running = false, w = 0, h = 0, px = -9999, py = -9999, tick = 0;
    const size = () => {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const step = 48;
      traces = Array.from({ length: Math.max(4, Math.round(Math.min(14, h / 60) * density)) }, () => {
        let x = -10, y = Math.round((Math.random() * h) / step) * step + 0.5;
        const pts = [[x, y]];
        while (x < w + 20) {                         // a circuit trace: straight runs and 45° jogs
          x += step * (2 + Math.floor(Math.random() * 5));
          pts.push([x, y]);
          if (Math.random() < 0.6) { const dy = (Math.random() < 0.5 ? -1 : 1) * step; x += step; y += dy; pts.push([x, y]); }
        }
        const segs = pts.slice(1).map((p, k) => Math.hypot(p[0] - pts[k][0], p[1] - pts[k][1]));
        return { pts, segs, len: segs.reduce((a, b) => a + b, 0) };
      });
      pulses = traces.map((tr) => ({ tr, at: Math.random() * tr.len, v: 2 + Math.random() * 3 }));
    };
    const pointAt = (tr, d) => {
      for (let k = 0; k < tr.segs.length; k++) {
        if (d <= tr.segs[k]) {
          const [ax, ay] = tr.pts[k], [bx, by] = tr.pts[k + 1], f = d / tr.segs[k];
          return [ax + (bx - ax) * f, ay + (by - ay) * f];
        }
        d -= tr.segs[k];
      }
      return tr.pts[tr.pts.length - 1];
    };
    const path = (pts) => { ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke(); };
    const bolt = (x0, y0, x1, y1, lit) => {         // a jagged arc, new every frame
      const pts = [[x0, y0]];
      for (let i = 1; i < 7; i++) pts.push([x0 + (x1 - x0) * i / 7 + (Math.random() - 0.5) * 18, y0 + (y1 - y0) * i / 7 + (Math.random() - 0.5) * 18]);
      pts.push([x1, y1]);
      ctx.save();
      ctx.shadowColor = `rgb(${c})`; ctx.shadowBlur = 14;
      ctx.strokeStyle = `rgba(255,255,255,${(0.85 * lit).toFixed(3)})`; ctx.lineWidth = 1.5;
      path(pts);
      ctx.restore();
    };
    const frame = () => {
      if (!running) return;
      tick++;
      ctx.clearRect(0, 0, w, h);
      const lit = 0.1 + power * 0.9;
      ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = `rgba(${c},${(0.07 + power * 0.1).toFixed(3)})`;
      for (const tr of traces) path(tr.pts);
      if (power > 0.02) {
        ctx.lineWidth = 2.2;
        for (const p of pulses) {
          p.at = (p.at + p.v * (0.4 + power)) % p.tr.len;
          for (let i = 0; i < 10; i++) {               // the current, with a fading tail
            const d0 = p.at - i * 6, d1 = d0 - 6;
            if (d1 < 0) break;
            ctx.strokeStyle = `rgba(${c},${((1 - i / 10) * lit).toFixed(3)})`;
            path([pointAt(p.tr, d0), pointAt(p.tr, d1)]);
          }
          const [hx, hy] = pointAt(p.tr, p.at);
          ctx.fillStyle = `rgba(255,255,255,${lit.toFixed(3)})`;
          ctx.beginPath(); ctx.arc(hx, hy, 2.2, 0, Math.PI * 2); ctx.fill();
        }
        if (px > -999 && tick % 3) {
          let best = null, bd = 170;
          for (const tr of traces) for (const [x, y] of tr.pts) { const d = Math.hypot(x - px, y - py); if (d < bd) { bd = d; best = [x, y]; } }
          if (best) bolt(px, py, best[0], best[1], lit);
        }
      }
      bits = bits.filter((p) => p.life > 0);
      ctx.lineWidth = 1.6;
      for (const p of bits) {
        p.vy += 0.25; p.x += p.vx; p.y += p.vy; p.life--;
        ctx.strokeStyle = `rgba(255,${190 + (p.life % 50)},110,${(p.life / 40).toFixed(3)})`;
        path([[p.x, p.y], [p.x - p.vx * 2, p.y - p.vy * 2]]);
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
    host.addEventListener("pointerdown", (e) => {
      const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      for (let i = 0; i < 22; i++) {
        const a = Math.random() * Math.PI * 2, v = 2 + Math.random() * 5;
        bits.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 2, life: 24 + Math.random() * 18 });
      }
    }, { passive: true });
  };

  /* ---- rain (hero and closing band) + lightning (hero) ---- */
  const rain = (canvas) => {
    const density = Number(canvas.dataset.rain || 1);
    if (!(density > 0) || calm) return;
    if (canvas.dataset.fx === "spark") return spark(canvas, density);
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

  /* ---- the wall switch and the dimmer: they set how lit the page is (--b, 0 - 1) ---- */
  const sw = $("[data-switch]");
  if (sw) {
    const btn = $("[data-switch-btn]", sw), dim = $("[data-dim]", sw), out = $("[data-dim-out]", sw);
    const state = $("[data-switch-state]", sw), watts = $("[data-watts]");
    const bulbs = Number(sw.dataset.bulbs), bulbWatts = Number(sw.dataset.bulbWatts);
    let on = false;
    const show = () => {
      const level = Number(dim.value) / 100, b = on ? level : 0;
      root.style.setProperty("--b", b.toFixed(2));
      btn.setAttribute("aria-pressed", String(on));
      state.textContent = on ? sw.dataset.on : sw.dataset.off;
      out.textContent = `${dim.value}%`;
      dim.style.setProperty("--p", `${((Number(dim.value) - 10) / 90) * 100}%`);
      if (watts) watts.textContent = on ? Math.round(bulbs * bulbWatts * level) : 0;
      document.dispatchEvent(new CustomEvent("spark:power", { detail: b }));
    };
    const flip = (to) => {
      on = to;
      if (on && !calm) {                             // the power coming on flickers once
        root.classList.remove("is-flicker"); void root.offsetWidth; root.classList.add("is-flicker");
        setTimeout(() => root.classList.remove("is-flicker"), 800);
      }
      show();
    };
    btn.addEventListener("click", () => flip(!on));
    dim.addEventListener("input", () => { if (on) show(); else flip(true); });
    show();
    setTimeout(() => flip(true), calm ? 0 : 1100);   // the lights come on as the page arrives
  }

  /* ---- what are your bulbs really costing? bulbs x watts x hours x 365 / 1000 x the rate, old bulbs vs LEDs ---- */
  const led = $("[data-led]");
  if (led) {
    const oldW = Number(led.dataset.old), newW = Number(led.dataset.new);
    const input = (k) => $(`[data-led-in="${k}"]`, led);
    const money = (n) => "$" + Math.round(n).toLocaleString("en-US");
    const set = () => {
      const bulbs = Number(input("bulbs").value), hours = Number(input("hours").value), rate = Number(input("rate").value);
      const kwh = (watt) => (bulbs * watt * hours * 365) / 1000;
      $('[data-led-out="bulbs"]', led).textContent = bulbs;
      $('[data-led-out="hours"]', led).textContent = hours;
      $('[data-led-out="rate"]', led).textContent = `${Math.round(rate * 100)}¢`;
      $("[data-led-save]", led).textContent = money((kwh(oldW) - kwh(newW)) * rate);
      $("[data-led-kwh]", led).textContent = Math.round(kwh(oldW) - kwh(newW)).toLocaleString("en-US");
      $('[data-led-cost="old"]', led).textContent = money(kwh(oldW) * rate);
      $('[data-led-cost="new"]', led).textContent = money(kwh(newW) * rate);
      // the meters turn with the power each set of bulbs draws
      $(".ld-old", led).style.setProperty("--spin", `${Math.min(6, Math.max(0.15, 240 / (bulbs * oldW))).toFixed(2)}s`);
      $(".ld-new", led).style.setProperty("--spin", `${Math.min(6, Math.max(0.15, 240 / (bulbs * newW))).toFixed(2)}s`);
      for (const k of ["bulbs", "hours", "rate"]) {
        const el = input(k);
        el.style.setProperty("--p", `${((Number(el.value) - Number(el.min)) / (Number(el.max) - Number(el.min))) * 100}%`);
      }
    };
    $$("[data-led-in]", led).forEach((el) => el.addEventListener("input", set));
    set();
  }

  onScroll();
})();
