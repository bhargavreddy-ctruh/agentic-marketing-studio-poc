import type { Metadata } from "next";
import "./globals.css";

import { Inter } from "next/font/google";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Agentic Marketing Studio",
  description: "Agentic studio for creative marketing workflows",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="light" suppressHydrationWarning>
      <body className={`${inter.className} bg-mesh-light dark:bg-mesh-dark text-surface-50 antialiased min-h-screen transition-colors duration-200`}>
        {children}
      </body>
    </html>
  );
}
