"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, User, logout, me } from "@/lib/auth";
import { SessionResponse, createSession, listSessions } from "@/lib/api";
import { BrandProfile, listBrands, onboardBrand } from "@/lib/brand";

/**
 * Home / workflow-selection page — Tasks_Workflows.md #4. Real data: the current user's own
 * workflows (`GET /api/v1/sessions`, owner-scoped) and their own Brand DNA profiles
 * (`GET /api/v1/brands`, owner-scoped) — both routes and the ownership scoping behind them are
 * new this pass (Tasks_Workflows.md #2/#3), not previously reachable from any UI.
 */
export default function HomePage() {
  const router = useRouter();
  const [authChecked, setAuthChecked] = useState(false);
  const [user, setUser] = useState<User | null>(null);

  const [workflows, setWorkflows] = useState<SessionResponse[]>([]);
  const [workflowsError, setWorkflowsError] = useState<string | null>(null);
  const [showNewWorkflow, setShowNewWorkflow] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newApprovalMode, setNewApprovalMode] = useState<"auto" | "approve">("auto");
  const [creating, setCreating] = useState(false);

  const [showSettings, setShowSettings] = useState(false);
  const [brands, setBrands] = useState<BrandProfile[]>([]);
  const [brandsError, setBrandsError] = useState<string | null>(null);
  const [brandName, setBrandName] = useState("");
  const [brandColors, setBrandColors] = useState("");
  const [brandVoice, setBrandVoice] = useState("");
  const [brandProhibited, setBrandProhibited] = useState("");
  const [onboardingBrand, setOnboardingBrand] = useState(false);

  useEffect(() => {
    (async () => {
      const current = await me();
      if (!current) {
        router.replace("/login");
        return;
      }
      setUser(current);
      setAuthChecked(true);
      refreshWorkflows();
      refreshBrands();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshWorkflows() {
    try {
      setWorkflows(await listSessions());
      setWorkflowsError(null);
    } catch (err) {
      setWorkflowsError(err instanceof ApiError ? err.message : "Could not load workflows.");
    }
  }

  async function refreshBrands() {
    try {
      setBrands(await listBrands());
      setBrandsError(null);
    } catch (err) {
      setBrandsError(err instanceof ApiError ? err.message : "Could not load brand profiles.");
    }
  }

  async function handleCreateWorkflow(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      const created = await createSession(newApprovalMode, newTitle.trim() || undefined);
      router.push(`/studio/${created.id}`);
    } catch (err) {
      setWorkflowsError(err instanceof ApiError ? err.message : "Could not create a new workflow.");
      setCreating(false);
    }
  }

  async function handleOnboardBrand(e: React.FormEvent) {
    e.preventDefault();
    if (!brandName.trim()) return;
    setOnboardingBrand(true);
    try {
      await onboardBrand(brandName.trim(), {
        colors: brandColors.trim() || undefined,
        voice: brandVoice.trim() || undefined,
        prohibited_imagery: brandProhibited.trim() || undefined,
      });
      setBrandName("");
      setBrandColors("");
      setBrandVoice("");
      setBrandProhibited("");
      await refreshBrands();
    } catch (err) {
      setBrandsError(err instanceof ApiError ? err.message : "Could not onboard this brand.");
    } finally {
      setOnboardingBrand(false);
    }
  }

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  if (!authChecked || !user) {
    return (
      <div className="flex h-screen w-screen items-center justify-center text-sm text-neutral-500">
        Loading…
      </div>
    );
  }

  return (
    <div className="mx-auto min-h-screen w-full max-w-3xl px-6 py-10">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Agentic Marketing Studio</h1>
          <p className="text-sm text-neutral-500">Signed in as {user.username}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowSettings((s) => !s)}
            className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100"
          >
            {showSettings ? "Hide" : "⚙"} Settings
          </button>
          <button
            onClick={handleLogout}
            className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100"
          >
            Log out
          </button>
        </div>
      </header>

      {showSettings && (
        <section className="mb-8 rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold">Brand DNA</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Your own onboarded brand profiles — real facts specialists ground their work in
            (colors, voice, prohibited imagery), never invented.
          </p>

          {brandsError && <p className="mt-3 text-sm text-red-700">{brandsError}</p>}

          <div className="mt-4 flex flex-col gap-2">
            {brands.length === 0 && (
              <p className="text-sm text-neutral-400">No brand profiles onboarded yet.</p>
            )}
            {brands.map((b) => (
              <div key={b.id} className="rounded-lg border border-neutral-200 p-3 text-sm">
                <span className="font-medium">{b.name}</span>{" "}
                <span className="text-xs text-neutral-500">
                  {b.indexed ? "· indexed" : "· not yet indexed"}
                </span>
              </div>
            ))}
          </div>

          <form onSubmit={handleOnboardBrand} className="mt-5 flex flex-col gap-2 border-t border-neutral-200 pt-4">
            <p className="text-sm font-medium">Add a brand</p>
            <input
              placeholder="Brand name"
              className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
              value={brandName}
              onChange={(e) => setBrandName(e.target.value)}
              required
            />
            <input
              placeholder="Colors (e.g. #FF5733, #111111)"
              className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
              value={brandColors}
              onChange={(e) => setBrandColors(e.target.value)}
            />
            <input
              placeholder="Voice (e.g. bold, energetic, youthful)"
              className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
              value={brandVoice}
              onChange={(e) => setBrandVoice(e.target.value)}
            />
            <input
              placeholder="Prohibited imagery (optional)"
              className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
              value={brandProhibited}
              onChange={(e) => setBrandProhibited(e.target.value)}
            />
            <button
              type="submit"
              disabled={onboardingBrand}
              className="mt-1 self-start rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {onboardingBrand ? "Onboarding…" : "Add brand"}
            </button>
          </form>
        </section>
      )}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Your workflows</h2>
          <button
            onClick={() => setShowNewWorkflow((s) => !s)}
            className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white"
          >
            + New workflow
          </button>
        </div>

        {showNewWorkflow && (
          <form
            onSubmit={handleCreateWorkflow}
            className="mb-4 flex flex-col gap-2 rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm"
          >
            <input
              placeholder="Workflow name (optional)"
              className="rounded-lg border border-neutral-300 px-3 py-2 text-sm"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              autoFocus
            />
            <label className="flex items-center gap-2 text-sm text-neutral-600">
              Mode
              <select
                className="rounded border border-neutral-300 px-2 py-1"
                value={newApprovalMode}
                onChange={(e) => setNewApprovalMode(e.target.value as "auto" | "approve")}
              >
                <option value="auto">Auto (no pauses)</option>
                <option value="approve">Approve (real HITL gates)</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={creating}
              className="mt-1 self-start rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {creating ? "Creating…" : "Create & open"}
            </button>
          </form>
        )}

        {workflowsError && <p className="mb-3 text-sm text-red-700">{workflowsError}</p>}

        <div className="flex flex-col gap-2">
          {workflows.length === 0 && (
            <p className="text-sm text-neutral-400">
              No workflows yet — create one to start a real conversation with the studio.
            </p>
          )}
          {workflows.map((w) => (
            <button
              key={w.id}
              onClick={() => router.push(`/studio/${w.id}`)}
              className="flex items-center justify-between rounded-xl border border-neutral-200 bg-white p-4 text-left shadow-sm hover:bg-neutral-50"
            >
              <div>
                <p className="font-medium">{w.title}</p>
                <p className="text-xs text-neutral-500">
                  {w.status} · {w.approval_mode} · updated {new Date(w.updated_at).toLocaleString()}
                </p>
              </div>
              <span className="text-neutral-400">→</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
