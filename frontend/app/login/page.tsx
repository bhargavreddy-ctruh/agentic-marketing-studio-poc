"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, login, register } from "@/lib/auth";

/** Real (test-grade) auth UI — Tasks_Workflows.md #1/#4: one real login mechanism, no email
 * verification/reset/OAuth, backed by a genuine hashed-password + signed-cookie backend. */
export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "register") {
        await register(username, password);
      } else {
        await login(username, password);
      }
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Network error — is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-screen w-screen items-center justify-center">
      <div className="w-full max-w-sm rounded-2xl border border-surface-700/50 bg-surface-900/80 p-8 shadow-2xl backdrop-blur-xl">

        {/* 2b — App logo / wordmark lockup */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-500 shadow-[0_0_20px_rgba(99,102,241,0.5)]">
            <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
            </svg>
          </div>
          <div className="text-center">
            <h1 className="text-xl font-semibold text-surface-50">Agentic Marketing Studio</h1>
            <p className="mt-1 text-sm text-surface-400">
              {mode === "login" ? "Log in to see your workflows." : "Create a test account."}
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm text-surface-300">
            Username
            <input
              className="rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-surface-50 placeholder-surface-500 transition-colors focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              minLength={3}
              maxLength={64}
              required
              autoFocus
              placeholder="your username"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm text-surface-300">
            Password
            <input
              type="password"
              className="rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-surface-50 placeholder-surface-500 transition-colors focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              maxLength={200}
              required
              placeholder="••••••••"
            />
          </label>

          {error && (
            <p className="rounded-lg border border-red-700/40 bg-red-900/20 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="mt-1 rounded-lg bg-brand-500 px-4 py-2.5 text-sm font-medium text-white shadow-[0_0_12px_rgba(99,102,241,0.3)] transition-all hover:bg-brand-600 hover:shadow-[0_0_16px_rgba(99,102,241,0.5)] disabled:opacity-50 disabled:shadow-none"
          >
            {loading ? "…" : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>

        <button
          onClick={() => {
            setMode((m) => (m === "login" ? "register" : "login"));
            setError(null);
          }}
          className="mt-5 w-full text-center text-xs text-brand-400 transition-colors hover:text-brand-300"
        >
          {mode === "login" ? "New here? Create a test account" : "Already have an account? Log in"}
        </button>
      </div>
    </div>
  );
}
