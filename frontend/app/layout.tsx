import type { Metadata } from "next";
import "./globals.css";

import { Inter } from "next/font/google";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Agentic Marketing Studio",
  description: "Phase 4a — chat + ideation, wired to the real backend",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.className} bg-mesh-dark text-surface-50`}>{children}</body>
    </html>
  );
}
