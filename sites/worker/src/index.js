/* The preview websites at sites.leadspilot.co/<slug>/ - built by sites/build.py, uploaded by sites/deploy.py.
 *
 * Every request comes here first. _status.json (uploaded with the sites, never served) says how long each
 * site stays up: { "<slug>": { "expires": "<ISO date>", "hold": false } }. A site past its date shows an
 * "ended" page; a site on hold (the business wants to keep it) never ends, and its countdown is hidden.
 * The countdown on each page is set from the same date, so the page and the server always agree.
 *
 * Visits go into the VISITS database (D1, migrations/0001_visits.sql): "view" when a page is loaded,
 * "ended" when an ended site is opened, and - from a small script added to each page - "seen" once the
 * page has been open 3 seconds, "call" / "email" / "keep" when a phone, email or keep-my-website link is
 * tapped. The visitor's IP address is never stored: only a hash of it (with VISITS_KEY), their network,
 * city and country. The panel reads them with GET /_visits (VISITS_KEY as a bearer token) - the answer also
 * holds the panel's own hash and country, so its own visits are told apart (app/outreach/visits.py).
 *
 * The quote form posts to /_q: the request waits in the same database (migrations/0002_requests.sql) until
 * the panel emails it to the business and deletes it (GET and POST /_requests, app/outreach/site_requests.py).
 */

const HOME = "https://leadspilot.co/";
const TAPS = new Set(["seen", "call", "email", "keep"]);
const PAGE_SIZE = 1000;
const QUOTE_FIELDS = { problem: 100, address: 200, when: 40, name: 100, phone: 30, email: 120 };
const QUOTES_PER_VISITOR = 5;   // an hour
const QUOTES_PER_SITE = 20;     // an hour
const NOT_SAVED = "Your request didn't go through.";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/_v" && request.method === "POST") return beacon(request, env, ctx);
    if (url.pathname === "/_visits") return visits(request, env, url);
    if (url.pathname === "/_q" && request.method === "POST") return quote(request, env, url);
    if (url.pathname === "/_requests") return requests(request, env, url);

    const slug = decodeURIComponent(url.pathname.split("/")[1] || "");
    if (!slug) return Response.redirect(HOME, 302);
    if (slug.startsWith("_") || slug.startsWith(".")) return notFound();

    const site = (await statusOf(env, url))[slug] || null;
    if (!site) return notFound();
    const copy = url.searchParams.has("copy");
    if (!site.hold && Date.now() >= Date.parse(site.expires)) {
      if (request.method === "GET") record(ctx, env, request, slug, "ended", copy);
      return ended();
    }

    const asset = await env.ASSETS.fetch(request);
    const response = new Response(asset.body, asset);
    response.headers.set("X-Robots-Tag", "noindex, nofollow");
    if (!(response.headers.get("Content-Type") || "").includes("text/html")) return response;
    response.headers.set("Cache-Control", "no-store");      // the countdown and the hold can change any day
    if (request.method === "GET" && asset.ok) record(ctx, env, request, slug, "view", copy);

    const rewriter = new HTMLRewriter();
    if (site.hold) {
      rewriter
        .on(".pv-timer", { element: (el) => el.remove() })
        .on(".pv-long", { element: (el) => el.setInnerContent("Your new website is being set up.") })
        .on(".pv-short", { element: (el) => el.setInnerContent("Being set up") });
    } else {
      rewriter.on("[data-ends]", { element: (el) => el.setAttribute("data-ends", site.expires) });
    }
    rewriter.on("body", { element: (el) => el.append(tracker(slug, copy), { html: true }) });
    return rewriter.transform(response);
  },
};

async function statusOf(env, url) {
  const response = await env.ASSETS.fetch(new URL("/_status.json", url));
  const status = response.ok ? await response.json() : {};
  return Object.setPrototypeOf(status, null);
}

// ---------------------------------------------------------------------------
// visits
// ---------------------------------------------------------------------------
function tracker(slug, copy) {
  const data = JSON.stringify({ s: slug, c: copy ? 1 : 0 }).replace(/</g, "\\u003c");
  return `<script>(function(){var d=${data};function b(k){d.k=k;try{navigator.sendBeacon("/_v",JSON.stringify(d))}catch(e){}}
var seen=false;function look(){if(seen||document.visibilityState!=="visible")return;seen=true;setTimeout(function(){b("seen")},3000)}
look();document.addEventListener("visibilitychange",look);
document.addEventListener("click",function(e){var a=e.target&&e.target.closest&&e.target.closest("a[href]");if(!a)return;
var h=a.getAttribute("href")||"";if(h.indexOf("tel:")===0)b("call");else if(h.indexOf("mailto:")===0)b(/keep/i.test(h)?"keep":"email")},true);
})();</script>`;
}

async function beacon(request, env, ctx) {
  let data;
  try {
    data = JSON.parse((await request.text()).slice(0, 500));
  } catch {
    return new Response(null, { status: 400 });
  }
  const slug = typeof data.s === "string" ? data.s : "";
  if (!TAPS.has(data.k) || !slug || !Object.hasOwn(await statusOf(env, new URL(request.url)), slug)) {
    return new Response(null, { status: 400 });
  }
  record(ctx, env, request, slug, data.k, data.c === 1);
  return new Response(null, { status: 204 });
}

function record(ctx, env, request, slug, kind, copy) {
  if (!env.VISITS) return;
  ctx.waitUntil((async () => {
    const cf = request.cf || {};
    try {
      await env.VISITS.prepare(
        "INSERT INTO visits (at, slug, kind, who, org, country, city, ua, copy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      ).bind(new Date().toISOString(), slug, kind, await who(request, env), String(cf.asOrganization || ""),
        String(cf.country || ""), String(cf.city || ""), (request.headers.get("User-Agent") || "").slice(0, 200),
        copy ? 1 : 0).run();
    } catch (err) {
      console.error("visit not recorded", err);
    }
  })());
}

async function who(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "";
  if (!ip) return "";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${env.VISITS_KEY || ""}|${ip}`));
  return [...new Uint8Array(digest)].slice(0, 8).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function visits(request, env, url) {
  const key = env.VISITS_KEY || "";
  if (!key || !env.VISITS || request.headers.get("Authorization") !== `Bearer ${key}`) return notFound();
  const after = Math.max(0, Number.parseInt(url.searchParams.get("after") || "0", 10) || 0);
  const { results } = await env.VISITS.prepare(
    "SELECT id, at, slug, kind, who, org, country, city, ua, copy FROM visits WHERE id > ? ORDER BY id LIMIT ?",
  ).bind(after, PAGE_SIZE).all();
  const cf = request.cf || {};
  return Response.json({ visits: results, more: results.length === PAGE_SIZE,
                         me: { who: await who(request, env), country: String(cf.country || "") } },
                       { headers: { "Cache-Control": "no-store" } });
}

// ---------------------------------------------------------------------------
// quote requests
// ---------------------------------------------------------------------------
async function quote(request, env, url) {
  const origin = request.headers.get("Origin");
  if (!(request.headers.get("Content-Type") || "").startsWith("application/json") || (origin && origin !== url.origin)) {
    return Response.json({ error: NOT_SAVED }, { status: 415 });
  }
  let data;
  try {
    data = JSON.parse((await request.text()).slice(0, 4000));
  } catch {
    return Response.json({ error: NOT_SAVED }, { status: 400 });
  }
  const slug = typeof data.slug === "string" ? data.slug : "";
  const site = (await statusOf(env, url))[slug] || null;
  if (!site || (!site.hold && Date.now() >= Date.parse(site.expires))) {
    return Response.json({ error: NOT_SAVED }, { status: 404 });
  }
  if (data.fax) return Response.json({ ok: true });       // the hidden field: a bot gets no hint it was caught
  const fields = {};
  for (const [name, longest] of Object.entries(QUOTE_FIELDS)) {
    fields[name] = String(data[name] ?? "").replace(/\s+/g, " ").trim().slice(0, longest);
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(fields.email)) fields.email = "";
  if (!fields.name || fields.phone.replace(/\D/g, "").length < 10) {
    return Response.json({ error: NOT_SAVED }, { status: 400 });
  }
  if (!env.VISITS) return Response.json({ error: NOT_SAVED }, { status: 503 });
  try {
    const hash = await who(request, env);
    const hourAgo = new Date(Date.now() - 3600000).toISOString();
    const seen = await env.VISITS.prepare(
      "SELECT COALESCE(SUM(who = ?), 0) AS mine, COALESCE(SUM(slug = ?), 0) AS here FROM visits WHERE kind = 'quote' AND at > ?",
    ).bind(hash, slug, hourAgo).first();
    if (seen.mine >= QUOTES_PER_VISITOR || seen.here >= QUOTES_PER_SITE) {
      return Response.json({ error: NOT_SAVED }, { status: 429 });
    }
    const cf = request.cf || {};
    const [at, org, country, city] = [new Date().toISOString(), String(cf.asOrganization || ""), String(cf.country || ""),
      String(cf.city || "")];
    const ua = (request.headers.get("User-Agent") || "").slice(0, 200);
    const copy = data.copy === true ? 1 : 0;
    await env.VISITS.batch([
      env.VISITS.prepare(
        "INSERT INTO requests (at, slug, who, org, country, ua, copy, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      ).bind(at, slug, hash, org, country, ua, copy, JSON.stringify(fields)),
      env.VISITS.prepare(
        "INSERT INTO visits (at, slug, kind, who, org, country, city, ua, copy) VALUES (?, ?, 'quote', ?, ?, ?, ?, ?, ?)",
      ).bind(at, slug, hash, org, country, city, ua, copy),
    ]);
  } catch (err) {
    console.error("quote request not saved", err);
    return Response.json({ error: NOT_SAVED }, { status: 503 });
  }
  return Response.json({ ok: true });
}

async function requests(request, env, url) {
  const key = env.VISITS_KEY || "";
  if (!key || !env.VISITS || request.headers.get("Authorization") !== `Bearer ${key}`) return notFound();
  if (request.method === "POST") {
    const { done } = await request.json();
    const ids = (Array.isArray(done) ? done : []).map(Number).filter(Number.isInteger);
    if (ids.length) {
      await env.VISITS.batch(ids.map((id) => env.VISITS.prepare("DELETE FROM requests WHERE id = ?").bind(id)));
    }
    return Response.json({ deleted: ids.length });
  }
  const { results } = await env.VISITS.prepare(
    "SELECT id, at, slug, who, org, country, ua, copy, data FROM requests ORDER BY id LIMIT 100",
  ).all();
  return Response.json({ requests: results }, { headers: { "Cache-Control": "no-store" } });
}

// ---------------------------------------------------------------------------
// pages
// ---------------------------------------------------------------------------
function notFound() {
  return page(404, "Nothing here", "There's no website at this address.");
}

function ended() {
  return page(410, "This preview has ended",
    `Want it back? Email <a href="mailto:piyush@leadspilot.co">piyush@leadspilot.co</a> and it can be up again the same day.`);
}

function page(status, title, text) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>${title}</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; box-sizing: border-box;
         background: #0A1120; color: #F4EFE7; font: 17px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 34rem; text-align: center; }
  h1 { margin: 0 0 12px; font-size: clamp(1.8rem, 5vw, 2.6rem); line-height: 1.15; }
  p { margin: 0; color: #A9B3C6; }
  a { color: #FFB21E; }
</style></head>
<body><main><h1>${title}</h1><p>${text}</p></main></body></html>`;
  return new Response(html, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", "X-Robots-Tag": "noindex", "Cache-Control": "no-store" },
  });
}
