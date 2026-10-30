// ozel.dergi.coze.org: the private build of the site (data/site-private), for titles that wait
// for their rights holders' permission. Cloudflare Access (Google login, Arda only) sits in front
// of the hostname; this Worker checks the Access token itself as well and refuses everything
// without a valid one, so the site stays closed even if the Access application is missing or
// misconfigured (fail closed: no TEAM_DOMAIN/POLICY_AUD, no pages).
//
// Bucket layout (gs://dergi-coze-org-site, read with the same HMAC key as img.dergi.coze.org):
//   ozel/site/...   the private build's pages, search index and static files
//   ozel/i/<slug>/  images of the private titles; other titles' images come from i/ as usual
// /api/* and /mcp go to the public API (public titles only).
import { AwsClient } from "aws4fetch";

const KEEP = ["content-type", "content-length", "etag", "last-modified"];
const TYPES = { html: "text/html; charset=utf-8", css: "text/css", js: "text/javascript", json: "application/json",
  wasm: "application/wasm", webp: "image/webp", svg: "image/svg+xml", png: "image/png", ico: "image/x-icon" };

function deny(status, text) {
  return new Response(text, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function b64url(s) {
  return Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")),
    (c) => c.charCodeAt(0));
}

let certs = { at: 0, keys: [] };

async function accessKeys(team) {
  if (Date.now() - certs.at < 3600_000 && certs.keys.length) return certs.keys;
  const r = await fetch(`https://${team}/cdn-cgi/access/certs`);
  if (!r.ok) throw new Error(`certs ${r.status}`);
  certs = { at: Date.now(), keys: (await r.json()).keys };
  return certs.keys;
}

// A valid Cloudflare Access token for this application: RS256 signature by the team's key,
// our audience tag, our team as issuer, not expired.
export async function allowed(request, env) {
  if (!env.TEAM_DOMAIN || !env.POLICY_AUD) return false;
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return false;
  const [h, p, sig] = token.split(".");
  if (!sig) return false;
  try {
    const header = JSON.parse(new TextDecoder().decode(b64url(h)));
    const claims = JSON.parse(new TextDecoder().decode(b64url(p)));
    const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (header.alg !== "RS256" || !aud.includes(env.POLICY_AUD)) return false;
    if (claims.iss !== `https://${env.TEAM_DOMAIN}` || !(claims.exp * 1000 > Date.now())) return false;
    const jwk = (await accessKeys(env.TEAM_DOMAIN)).find((k) => k.kid === header.kid);
    if (!jwk) return false;
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    return await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64url(sig), new TextEncoder().encode(`${h}.${p}`));
  } catch {
    return false;
  }
}

// Clean URLs as on Firebase Hosting: /x/ → x/index.html, /x → redirect to /x/, files as they are.
function objectFor(path) {
  if (path.startsWith("/i/")) return null;
  if (path.endsWith("/")) return `ozel/site${path}index.html`;
  if (!/\.[a-z0-9]+$/i.test(path.split("/").pop())) return "redirect";
  return `ozel/site${path}`;
}

export async function ozel(request, env, ctx) {
  const url = new URL(request.url);
  if (request.method !== "GET" && request.method !== "HEAD" && request.method !== "POST") return deny(405, "Method not allowed");
  if (!(await allowed(request, env))) return deny(403, "Bu sayfa özel.");
  if (url.pathname.includes("..")) return deny(404, "Not found");

  if (url.pathname.startsWith("/api/") || url.pathname === "/mcp" || url.pathname.startsWith("/mcp/")) {
    const upstream = new Request(`https://dergi.coze.org${url.pathname}${url.search}`, request);
    upstream.headers.delete("Cookie");
    upstream.headers.delete("Cf-Access-Jwt-Assertion");
    return fetch(upstream);
  }
  if (request.method === "POST") return deny(405, "Method not allowed");
  if (url.pathname.toLowerCase().endsWith(".pdf")) return deny(404, "Not found");

  const object = objectFor(url.pathname);
  if (object === "redirect") return Response.redirect(`${url.origin}${url.pathname}/${url.search}`, 301);
  // Private titles' images live under ozel/i/, everyone else's under i/ (shared with the public site).
  const candidates = object ? [object] : [`ozel${url.pathname}`, url.pathname.slice(1)];

  // Checked above, so the edge cache cannot be used to get round Access.
  const cache = caches.default;
  const key = new Request(`${url.origin}${url.pathname}`, { method: "GET" });
  let response = await cache.match(key);
  if (!response) {
    const gcs = new AwsClient({ accessKeyId: env.HMAC_ID, secretAccessKey: env.HMAC_SECRET, service: "s3", region: "auto" });
    let upstream;
    for (const name of candidates) {
      upstream = await gcs.fetch(`https://storage.googleapis.com/${env.BUCKET}/${name}`);
      if (upstream.ok || (upstream.status !== 404 && upstream.status !== 403)) break;
    }
    if (!upstream.ok) return deny(upstream.status === 404 || upstream.status === 403 ? 404 : 502, "Not found");
    const headers = new Headers();
    for (const h of KEEP) if (upstream.headers.has(h)) headers.set(h, upstream.headers.get(h));
    const ext = (object || url.pathname).split(".").pop().toLowerCase();
    if (TYPES[ext]) headers.set("Content-Type", TYPES[ext]);
    headers.set("Cache-Control", "public, max-age=3600");
    response = new Response(upstream.body, { status: 200, headers });
    ctx.waitUntil(cache.put(key, response.clone()));
  }

  const headers = new Headers(response.headers);
  headers.set("Cache-Control", "private, max-age=600");
  headers.set("X-Robots-Tag", "noindex, nofollow");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "same-origin");
  return new Response(request.method === "HEAD" ? null : response.body, { status: 200, headers });
}
