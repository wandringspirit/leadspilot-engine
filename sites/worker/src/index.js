/* The preview websites at sites.leadspilot.co/<slug>/ - built by sites/build.py, uploaded by sites/deploy.py.
 *
 * Every request comes here first. _status.json (uploaded with the sites, never served) says how long each
 * site stays up: { "<slug>": { "expires": "<ISO date>", "hold": false } }. A site past its date shows an
 * "ended" page; a site on hold (the business wants to keep it) never ends, and its countdown is hidden.
 * The countdown on each page is set from the same date, so the page and the server always agree.
 */

const HOME = "https://leadspilot.co/";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const slug = decodeURIComponent(url.pathname.split("/")[1] || "");
    if (!slug) return Response.redirect(HOME, 302);
    if (slug.startsWith("_") || slug.startsWith(".")) return notFound();

    const statusResponse = await env.ASSETS.fetch(new URL("/_status.json", url));
    const status = statusResponse.ok ? await statusResponse.json() : {};
    const site = Object.hasOwn(status, slug) ? status[slug] : null;
    if (!site) return notFound();
    if (!site.hold && Date.now() >= Date.parse(site.expires)) return ended();

    const asset = await env.ASSETS.fetch(request);
    const response = new Response(asset.body, asset);
    response.headers.set("X-Robots-Tag", "noindex, nofollow");
    if (!(response.headers.get("Content-Type") || "").includes("text/html")) return response;
    response.headers.set("Cache-Control", "no-store");      // the countdown and the hold can change any day

    const rewriter = new HTMLRewriter();
    if (site.hold) {
      rewriter
        .on(".pv-timer", { element: (el) => el.remove() })
        .on(".pv-long", { element: (el) => el.setInnerContent("Your new website is being set up.") })
        .on(".pv-short", { element: (el) => el.setInnerContent("Being set up") });
    } else {
      rewriter.on("[data-ends]", { element: (el) => el.setAttribute("data-ends", site.expires) });
    }
    return rewriter.transform(response);
  },
};

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
