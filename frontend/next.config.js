/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
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
    const backendOrigin = process.env.BACKEND_ORIGIN;
    if (!backendOrigin) return [];
    return [{ source: "/api/:path*", destination: `${backendOrigin}/api/:path*` }];
  },
};

module.exports = nextConfig;
