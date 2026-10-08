/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Real, confirmed fix (2026-10-06): Next's own rewrite proxy (used for BOTH local dev and the
  // Vercel HTTPS-terminating-rewrite trick below) hardcodes a 30-SECOND timeout
  // (`node_modules/next/dist/esm/server/lib/router-utils/proxy-request.js`'s
  // `proxyTimeout: proxyTimeout || 30000`) — confirmed via that file plus
  // `router-server.js`/`config-shared.js` that `experimental.proxyTimeout` is a real, live,
  // wired-through override point in this Next 16 build (undocumented, but not a dead end). A
  // backend turn can legitimately take longer than 30s; without this, the proxy gives up and
  // returns a bare, non-JSON 500 well before the backend has actually failed — the backend keeps
  // running to completion regardless, the response just never reaches the browser. A large finite
  // value (not `null`) on purpose — `null` disables the proxy's own timeout entirely, which would
  // make a genuinely hung backend request (see `models/base.py`'s own `command_timeout` fix) block
  // the browser forever instead of eventually failing loudly.
  experimental: {
    proxyTimeout: 120_000,
  },
  // Dev server rejects cross-origin requests (incl. the HMR websocket) by default; the
  // Cloudflare quick tunnel's domain changes on every restart, so allow the whole subdomain.
  allowedDevOrigins: ["*.trycloudflare.com"],
  // Real, live-found deploy blocker (2026-09-29): a Vercel deployment is always served over
  // HTTPS, but the backend (a plain EC2 IP:port, no TLS cert) is HTTP-only — a browser silently
  // blocks an HTTPS page's fetch to an HTTP origin ("mixed content"), which showed up as a
  // generic "the server didn't respond" with no real network error to debug. Rather than require
  // setting up a domain + TLS cert on the backend, this uses Vercel's own server-side rewrite as
  // an HTTPS-terminating proxy: the REWRITE itself runs server-to-server (not subject to the
  // browser's mixed-content rule), so the browser only ever talks to this same-origin HTTPS URL.
  // `BACKEND_ORIGIN` is a plain (non-NEXT_PUBLIC_) env var — server-only, never reaches the
  // client bundle — set in Vercel's project settings to e.g. "http://13.223.87.122:8080".
  // `NEXT_PUBLIC_API_BASE_URL` must then be set to an explicit EMPTY STRING (not left unset —
  // `lib/http.ts`'s `??` only falls back on null/undefined, not "") so the frontend calls
  // relative paths ("/api/v1/...") that this rewrite intercepts, same as the app's own Caddy
  // reverse-proxy setup already relies on for a shared-origin deployment.
  async rewrites() {
    const backendOrigin = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8080";
    console.log("Rewrites called. BACKEND_ORIGIN:", backendOrigin);
    return [{ source: "/api/:path*", destination: `${backendOrigin}/api/:path*` }];
  },
};

module.exports = nextConfig;
