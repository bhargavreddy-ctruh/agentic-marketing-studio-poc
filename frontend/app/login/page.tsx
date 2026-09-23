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
    <div className="flex h-screen w-screen items-center justify-center bg-neutral-50">
      <div className="w-full max-w-sm rounded-2xl border border-neutral-200 bg-white p-6 shadow-xl">
        <h1 className="text-lg font-semibold">Agentic Marketing Studio</h1>
        <p className="mt-1 text-sm text-neutral-500">
          {mode === "login" ? "Log in to see your workflows." : "Create a test account."}
        </p>

        <form onSubmit={handleSubmit} className="mt-5 flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Username
            <input
              className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              minLength={3}
              maxLength={64}
              required
              autoFocus
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Password
            <input
              type="password"
              className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              maxLength={200}
              required
            />
          </label>

          {error && <p className="text-sm text-red-700">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="mt-1 rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {loading ? "…" : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>

        <button
          onClick={() => {
            setMode((m) => (m === "login" ? "register" : "login"));
            setError(null);
          }}
          className="mt-4 w-full text-center text-xs text-neutral-500 underline"
        >
          {mode === "login" ? "New here? Create a test account" : "Already have an account? Log in"}
        </button>
      </div>
    </div>
  );
}
