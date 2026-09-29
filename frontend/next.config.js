/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Dev server rejects cross-origin requests (incl. the HMR websocket) by default; the
  // Cloudflare quick tunnel's domain changes on every restart, so allow the whole subdomain.
  allowedDevOrigins: ["*.trycloudflare.com"],
};

module.exports = nextConfig;
