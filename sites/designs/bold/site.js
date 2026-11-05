/* "Bold" design: everything that moves. No libraries; every effect checks reduced motion. */
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

  /* ---- rain (hero and closing band) + lightning (hero) ---- */
  const rain = (canvas) => {
    const density = Number(canvas.dataset.rain || 1);
    if (!(density > 0) || calm) return;
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
    const stepsF = $$(".qf-step", form);
    const back = $("[data-qf-back]", form);
    const next = $("[data-qf-next]", form);
    const bar = $("[data-qf-bar]", form);
    const count = $("[data-qf-step]", form);
    const err = $("[data-qf-error]", form);
    let at = 0;
    const go = (i) => {
      at = i;
      stepsF.forEach((s, n) => { s.hidden = n !== i; });
      back.hidden = i === 0;
      next.firstChild.textContent = i === stepsF.length - 1 ? "Send request" : "Next";
      bar.style.transform = `scaleX(${(i + 1) / stepsF.length})`;
      count.textContent = i + 1;
      err.hidden = true;
    };
    const valid = () => {
      const step = stepsF[at];
      const radios = $$("input[type=radio]", step);
      if (radios.length && !radios.some((r) => r.checked)) return "Pick the option that fits best.";
      for (const input of $$("input:not([type=radio])[required]", step)) {
        const bad = !input.value.trim() || (input.type === "tel" && input.value.replace(/\D/g, "").length < 10);
        input.setAttribute("aria-invalid", String(bad));
        if (bad) { input.focus(); return input.type === "tel" ? "Please enter a 10-digit phone number." : `Please fill in "${input.labels[0].firstElementChild.textContent}".`; }
      }
      return "";
    };
    $$("input[type=radio]", form).forEach((r) => r.addEventListener("change", () => setTimeout(() => { if (at === 0) go(1); }, 260)));
    back.addEventListener("click", () => go(at - 1));
    $$("[data-pick]").forEach((a) => a.addEventListener("click", () => {
      const map = { "storm-damage": "storm", "leak-detection": "leak", "roof-replacement": "replace", "roof-repair": "leak", gutters: "check" };
      const want = map[a.dataset.pick] || "check";
      const radio = $(`input[data-val="${want}"]`, form);
      if (radio) { radio.checked = true; go(1); }
    }));
    $$("[data-service]").forEach((a) => a.addEventListener("click", () => {
      const map = { "storm-damage": "storm", "leak-detection": "leak", "roof-replacement": "replace", "roof-repair": "leak" };
      const radio = $(`input[data-val="${map[a.dataset.service] || "check"}"]`, form);
      if (radio) { radio.checked = true; go(1); }
    }));
    let sending = false;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (sending) return;
      const problem = valid();
      if (problem) { err.textContent = problem; err.hidden = false; return; }
      if (at < stepsF.length - 1) { go(at + 1); return; }
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
    go(0);
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

  onScroll();
})();
