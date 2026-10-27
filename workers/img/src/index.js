// img.dergi.coze.org: the site's images, shown only on dergi.coze.org.
//
// The bucket (gs://dergi-coze-org-site) is private; this Worker reads it with a read-only HMAC
// key (secrets HMAC_ID, HMAC_SECRET) and answers only requests whose Referer is one of the
// site's own origins. Browsers send that Referer for images and downloads on the site
// (Referrer-Policy strict-origin-when-cross-origin); a page elsewhere, a direct visit or a
// script without it gets 403. Cross-Origin-Resource-Policy: same-site makes browsers refuse to
// show the images on other sites even if a Referer were faked. Scans as PDF are never served.
import { AwsClient } from "aws4fetch";

const KEEP = ["content-type", "content-length", "etag", "last-modified"];

function deny(status, text) {
  return new Response(text, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") return deny(405, "Method not allowed");
    if (!url.pathname.startsWith("/i/") || url.pathname.toLowerCase().endsWith(".pdf")) return deny(404, "Not found");

    let origin = "";
    try {
      origin = new URL(request.headers.get("Referer") || "").origin;
    } catch {}
    if (!env.ALLOWED_ORIGINS.split(",").includes(origin)) {
      return deny(403, "Bu görsel yalnızca dergi.coze.org üzerinde gösterilir.");
    }

    // The edge cache is consulted only after the Referer check, so it cannot be used to get round it.
    const cache = caches.default;
    const key = new Request(`${url.origin}${url.pathname}`, { method: "GET" });
    let response = await cache.match(key);
    if (!response) {
      const gcs = new AwsClient({ accessKeyId: env.HMAC_ID, secretAccessKey: env.HMAC_SECRET, service: "s3", region: "auto" });
      const upstream = await gcs.fetch(`https://storage.googleapis.com/${env.BUCKET}${url.pathname}`);
      if (!upstream.ok) return deny(upstream.status === 404 || upstream.status === 403 ? 404 : 502, "Not found");
      const headers = new Headers();
      for (const h of KEEP) if (upstream.headers.has(h)) headers.set(h, upstream.headers.get(h));
      headers.set("Cache-Control", "public, max-age=604800");
      response = new Response(upstream.body, { status: 200, headers });
      ctx.waitUntil(cache.put(key, response.clone()));
    }

    // To the browser: its own cache only (no shared caches that would skip the check).
    const headers = new Headers(response.headers);
    headers.set("Cache-Control", "private, max-age=86400");
    headers.set("Cross-Origin-Resource-Policy", "same-site");
    headers.set("X-Content-Type-Options", "nosniff");
    return new Response(request.method === "HEAD" ? null : response.body, { status: 200, headers });
  },
};
