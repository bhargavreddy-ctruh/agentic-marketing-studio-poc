import nextConfig from "eslint-config-next";

/**
 * Real, live-found gap (2026-09-23 frontend audit): eslint was not installed at all despite
 * `package.json`'s `lint` script calling `next lint` — this codebase had zero real lint coverage.
 * Flat config for eslint 9 + Next 16, matching the installed `eslint-config-next` version.
 */
export default [
  ...nextConfig,
  {
    ignores: [".next/**", "node_modules/**"],
  },
];
