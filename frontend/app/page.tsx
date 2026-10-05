"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, User, logout, me } from "@/lib/auth";
import {
  SessionResponse,
  createSession,
  crawlUrl,
  deleteSession,
  listSessions,
  updateSessionTitle,
} from "@/lib/api";
import { BrandProfile, listBrands, onboardBrand } from "@/lib/brand";
import { ProductProfile, listOnboardedProducts, onboardProduct } from "@/lib/product";
import { MoodBoardAsset, listMoodBoardAssets, moodBoardAssetUrl, uploadMoodBoardAsset } from "@/lib/moodboard";
import { Spinner } from "@/components/Spinner";
import { ErrorBanner } from "@/components/ErrorBanner";
import { ThemeToggle } from "@/components/ThemeToggle";

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
  const [authError, setAuthError] = useState<string | null>(null);

  const [workflows, setWorkflows] = useState<SessionResponse[]>([]);
  const [workflowsError, setWorkflowsError] = useState<string | null>(null);
  const [showNewWorkflow, setShowNewWorkflow] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newApprovalMode, setNewApprovalMode] = useState<"auto" | "approve">("auto");
  // Product/Brand crawler (2026-09-28) — optional, additive: filling either kicks off a crawl
  // right after the session is created, alongside the existing create flow.
  const [newCompanyUrl, setNewCompanyUrl] = useState("");
  const [newProductUrl, setNewProductUrl] = useState("");
  const [creating, setCreating] = useState(false);

  // Edit/delete a workflow (2026-09-30, explicit user ask: "add a delete/edit button on
  // workflows") — `editingId` tracks which row is showing its inline rename input, if any.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [rowBusyId, setRowBusyId] = useState<string | null>(null);

  const [showSettings, setShowSettings] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"brand" | "product" | "moodboard">("brand");

  const [brands, setBrands] = useState<BrandProfile[]>([]);
  const [brandsError, setBrandsError] = useState<string | null>(null);
  const [brandName, setBrandName] = useState("");
  const [brandColors, setBrandColors] = useState("");
  const [brandVoice, setBrandVoice] = useState("");
  const [brandProhibited, setBrandProhibited] = useState("");
  const [onboardingBrand, setOnboardingBrand] = useState(false);

  const [products, setProducts] = useState<ProductProfile[]>([]);
  const [productsError, setProductsError] = useState<string | null>(null);
  const [productName, setProductName] = useState("");
  const [productDescription, setProductDescription] = useState("");
  const [productPrice, setProductPrice] = useState("");
  const [productDiscount, setProductDiscount] = useState("");
  // The product's real, physical color (2026-09-30) — a genuine product fact the guardrail system
  // now checks against, distinct from any brand color guideline (fixes a real bug where a red
  // product was refused as a brand-color violation).
  const [productColor, setProductColor] = useState("");
  const [onboardingProduct, setOnboardingProduct] = useState(false);

  const [moodBoardAssets, setMoodBoardAssets] = useState<MoodBoardAsset[]>([]);
  const [moodBoardError, setMoodBoardError] = useState<string | null>(null);
  const [moodBoardDescription, setMoodBoardDescription] = useState("");
  const [uploadingMoodBoard, setUploadingMoodBoard] = useState(false);

  async function checkAuth() {
    setAuthError(null);
    try {
      const current = await me();
      if (!current) {
        router.replace("/login");
        return;
      }
      setUser(current);
      setAuthChecked(true);
      refreshWorkflows();
      refreshBrands();
      refreshProducts();
      refreshMoodBoard();
    } catch (err) {
      setAuthError(err instanceof ApiError ? err.message : "the server didn't respond.");
    }
  }

  useEffect(() => {
    checkAuth();
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
      // Fire-and-forget — the crawl runs as the session's own background task (same
      // POST /{id}/crawl route ChatPanel's "🔗 Add Link" popover uses); never blocks navigation.
      const crawlingKinds: string[] = [];
      if (newCompanyUrl.trim()) {
        crawlUrl(created.id, newCompanyUrl.trim(), "brand").catch(() => {});
        crawlingKinds.push("brand");
      }
      if (newProductUrl.trim()) {
        crawlUrl(created.id, newProductUrl.trim(), "product").catch(() => {});
        crawlingKinds.push("product");
      }
      // Real, live-found gap (2026-10-05, explicit user report: "it was showing empty not even
      // 'scraping etc', which will confuse user") — the studio page had NOTHING indicating a
      // crawl kicked off at creation time, since the DNA modal (the only place crawl status ever
      // showed) isn't open yet. A `?crawling=brand,product` query param tells the studio page to
      // show a real status banner for these specific kinds right from its first render.
      const crawlingParam = crawlingKinds.length ? `?crawling=${crawlingKinds.join(",")}` : "";
      router.push(`/studio/${created.id}${crawlingParam}`);
    } catch (err) {
      setWorkflowsError(err instanceof ApiError ? err.message : "Could not create a new workflow.");
      setCreating(false);
    }
  }

  function startEditingWorkflow(w: SessionResponse) {
    setEditingId(w.id);
    setEditingTitle(w.title);
  }

  async function handleSaveTitle(sessionId: string) {
    const trimmed = editingTitle.trim();
    if (!trimmed) {
      setEditingId(null);
      return;
    }
    setRowBusyId(sessionId);
    try {
      await updateSessionTitle(sessionId, trimmed);
      setEditingId(null);
      await refreshWorkflows();
    } catch (err) {
      setWorkflowsError(err instanceof ApiError ? err.message : "Could not rename this workflow.");
    } finally {
      setRowBusyId(null);
    }
  }

  async function handleDeleteWorkflow(w: SessionResponse) {
    if (!window.confirm(`Delete "${w.title}"? This permanently removes it and everything on its canvas — this cannot be undone.`)) {
      return;
    }
    setRowBusyId(w.id);
    try {
      await deleteSession(w.id);
      await refreshWorkflows();
    } catch (err) {
      setWorkflowsError(err instanceof ApiError ? err.message : "Could not delete this workflow.");
    } finally {
      setRowBusyId(null);
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

  async function refreshProducts() {
    try {
      setProducts(await listOnboardedProducts());
      setProductsError(null);
    } catch (err) {
      setProductsError(err instanceof ApiError ? err.message : "Could not load product profiles.");
    }
  }

  async function handleOnboardProduct(e: React.FormEvent) {
    e.preventDefault();
    if (!productName.trim() || !productDescription.trim()) return;
    setOnboardingProduct(true);
    try {
      const price = productPrice.trim() ? Number(productPrice) : undefined;
      const discount = productDiscount.trim() ? Number(productDiscount) : undefined;
      await onboardProduct(productName.trim(), productDescription.trim(), price, discount, productColor.trim() || undefined);
      setProductName("");
      setProductDescription("");
      setProductPrice("");
      setProductDiscount("");
      setProductColor("");
      await refreshProducts();
    } catch (err) {
      setProductsError(err instanceof ApiError ? err.message : "Could not onboard this product.");
    } finally {
      setOnboardingProduct(false);
    }
  }

  async function refreshMoodBoard() {
    try {
      setMoodBoardAssets(await listMoodBoardAssets());
      setMoodBoardError(null);
    } catch (err) {
      setMoodBoardError(err instanceof ApiError ? err.message : "Could not load mood board assets.");
    }
  }

  async function handleUploadMoodBoard(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fileInput = e.currentTarget.elements.namedItem("moodBoardFile") as HTMLInputElement;
    const file = fileInput?.files?.[0];
    if (!file || !moodBoardDescription.trim()) return;
    setUploadingMoodBoard(true);
    try {
      await uploadMoodBoardAsset(file, moodBoardDescription.trim());
      setMoodBoardDescription("");
      fileInput.value = "";
      await refreshMoodBoard();
    } catch (err) {
      setMoodBoardError(err instanceof ApiError ? err.message : "Could not upload this asset.");
    } finally {
      setUploadingMoodBoard(false);
    }
  }

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  // ── Auth error / loading screens ──────────────────────────────────────────
  if (authError) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-4 text-sm">
        <p className="text-surface-400">Could not reach the backend: {authError}</p>
        <button
          onClick={() => checkAuth()}
          className="rounded-lg border border-surface-700/50 px-4 py-2 text-sm text-surface-200 transition-colors hover:bg-surface-800/60"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!authChecked || !user) {
    return (
      <div className="flex h-screen w-screen items-center justify-center">
        <div className="flex items-center gap-3 text-sm text-surface-400">
          <Spinner size={5} />
          Loading…
        </div>
      </div>
    );
  }

  // ── Input class helper ─────────────────────────────────────────────────────
  const inputCls =
    "rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-surface-50 placeholder-surface-500 transition-colors focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500";

  return (
    <div className="mx-auto min-h-screen w-full max-w-5xl px-6 py-10">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <header className="mb-8 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-500 shadow-[0_0_14px_rgba(99,102,241,0.4)]">
            <svg className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
            </svg>
          </div>
          <div>
            <h1 className="text-lg font-semibold text-surface-50">Agentic Marketing Studio</h1>
            <p className="text-xs text-surface-500">Signed in as <span className="text-surface-300">{user.username}</span></p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <button
            onClick={() => setShowSettings((s) => !s)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition-all ${
              showSettings
                ? "border-brand-500/50 bg-brand-500/10 text-brand-300"
                : "border-surface-700/50 text-surface-300 hover:bg-surface-800/60 hover:text-surface-50"
            }`}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Settings
          </button>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 rounded-lg border border-surface-700/50 px-3 py-1.5 text-sm text-surface-300 transition-all hover:bg-surface-800/60 hover:text-surface-50"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
            Log out
          </button>
        </div>
      </header>

      {/* ── Two-column layout when settings open ──────────────────────────── */}
      <div className={showSettings ? "flex gap-6" : ""}>

        {/* ── Workflows column ──────────────────────────────────────────────── */}
        <section className={showSettings ? "flex-1 min-w-0" : "w-full"}>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold text-surface-50">Your workflows</h2>
            <button
              onClick={() => setShowNewWorkflow((s) => !s)}
              className="flex items-center gap-1.5 rounded-lg bg-brand-500 px-3 py-1.5 text-sm font-medium text-white shadow-[0_0_10px_rgba(99,102,241,0.3)] transition-all hover:bg-brand-600 hover:shadow-[0_0_14px_rgba(99,102,241,0.5)]"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              New workflow
            </button>
          </div>

          {/* New workflow form */}
          {showNewWorkflow && (
            <form
              onSubmit={handleCreateWorkflow}
              className="mb-4 animate-fade-in-up rounded-2xl border border-brand-500/30 bg-surface-900/60 p-4 shadow-lg ring-1 ring-brand-500/20 backdrop-blur-xl"
            >
              <p className="mb-3 text-sm font-medium text-surface-200">New workflow</p>
              <div className="flex flex-col gap-2">
                <input
                  placeholder="Workflow name (optional)"
                  className={inputCls}
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  autoFocus
                />
                <input
                  placeholder="Company / brand URL (optional — auto-extracts brand DNA)"
                  className={inputCls}
                  value={newCompanyUrl}
                  onChange={(e) => setNewCompanyUrl(e.target.value)}
                />
                <input
                  placeholder="Product URL (optional — auto-extracts product DNA)"
                  className={inputCls}
                  value={newProductUrl}
                  onChange={(e) => setNewProductUrl(e.target.value)}
                />
                <label className="flex items-center gap-2 text-sm text-surface-400">
                  Mode
                  <select
                    className="rounded-lg border border-surface-700 bg-surface-800 px-2.5 py-1.5 text-sm text-surface-200 focus:border-brand-500 focus:outline-none"
                    value={newApprovalMode}
                    onChange={(e) => setNewApprovalMode(e.target.value as "auto" | "approve")}
                  >
                    <option value="auto" className="bg-surface-900">Auto (no pauses)</option>
                    <option value="approve" className="bg-surface-900">Approve (real HITL gates)</option>
                  </select>
                </label>
                <button
                  type="submit"
                  disabled={creating}
                  className="mt-1 self-start rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-brand-600 disabled:opacity-50"
                >
                  {creating ? "Creating…" : "Create & open"}
                </button>
              </div>
            </form>
          )}

          {workflowsError && (
            <div className="mb-3">
              <ErrorBanner message={workflowsError} onDismiss={() => setWorkflowsError(null)} />
            </div>
          )}

          <div className="flex flex-col gap-2">
            {workflows.length === 0 && (
              <p className="text-sm text-surface-500">
                No workflows yet — create one to start a real conversation with the studio.
              </p>
            )}
            {workflows.map((w) => (
              <div
                key={w.id}
                className="group flex items-center justify-between rounded-xl border border-surface-700/50 bg-surface-900/60 p-4 shadow-sm backdrop-blur-sm transition-all hover:-translate-y-0.5 hover:border-surface-600/60 hover:bg-surface-800/60 hover:shadow-md"
              >
                {editingId === w.id ? (
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    <input
                      autoFocus
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveTitle(w.id);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      className="min-w-0 flex-1 rounded-lg border border-brand-500/50 bg-surface-800 px-2.5 py-1.5 text-sm text-surface-50 focus:outline-none focus:ring-1 focus:ring-brand-500"
                    />
                    <button
                      onClick={() => handleSaveTitle(w.id)}
                      disabled={rowBusyId === w.id}
                      className="rounded-lg bg-brand-500 px-2.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600 disabled:opacity-50"
                    >
                      Save
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="rounded-lg border border-surface-700/50 px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-800/60"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => router.push(`/studio/${w.id}`)}
                    className="flex min-w-0 flex-1 items-center justify-between text-left"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-surface-50">{w.title}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        <WorkflowStatusBadge status={w.status} />
                        <ApprovalBadge mode={w.approval_mode} />
                        <span className="text-xs text-surface-500">
                          · updated {new Date(w.updated_at).toLocaleString()}
                        </span>
                      </div>
                    </div>
                    <svg className="ml-3 h-4 w-4 shrink-0 text-surface-500 transition-transform group-hover:translate-x-0.5 group-hover:text-surface-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                )}

                {editingId !== w.id && (
                  <div className="ml-2 flex shrink-0 items-center gap-1">
                    <button
                      onClick={() => startEditingWorkflow(w)}
                      disabled={rowBusyId === w.id}
                      title="Rename"
                      className="rounded-lg p-1.5 text-surface-500 opacity-0 transition-all hover:bg-surface-800 hover:text-surface-200 disabled:opacity-50 group-hover:opacity-100"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                    </button>
                    <button
                      onClick={() => handleDeleteWorkflow(w)}
                      disabled={rowBusyId === w.id}
                      title="Delete"
                      className="rounded-lg p-1.5 text-surface-500 opacity-0 transition-all hover:bg-red-900/40 hover:text-red-400 disabled:opacity-50 group-hover:opacity-100"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* ── Settings panel ────────────────────────────────────────────────── */}
        {showSettings && (
          <aside className="w-full md:w-[400px] shrink-0 animate-fade-in">
            <div className="rounded-2xl border border-surface-700/50 bg-surface-900/60 p-5 shadow-xl backdrop-blur-xl">
              {/* Tab bar */}
              <div className="mb-5 flex items-center gap-1 rounded-full border border-surface-700/50 bg-surface-800/60 p-1">
                {(["brand", "product", "moodboard"] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setSettingsTab(tab)}
                    className={`flex-1 rounded-full px-3 py-1.5 text-xs font-medium transition-all duration-200 ${
                      settingsTab === tab
                        ? "bg-brand-500 text-white shadow-[0_0_10px_rgba(99,102,241,0.4)]"
                        : "text-surface-400 hover:text-surface-50"
                    }`}
                  >
                    {tab === "brand" ? "Brand DNA" : tab === "product" ? "Product DNA" : "Mood Board"}
                  </button>
                ))}
              </div>

              {/* ── Brand DNA tab ── */}
              {settingsTab === "brand" && (
                <div>
                  <p className="mb-3 text-xs text-surface-500">
                    Real brand facts specialists ground their work in — colors, voice, prohibited imagery.
                  </p>
                  {brandsError && (
                    <div className="mb-3">
                      <ErrorBanner message={brandsError} onDismiss={() => setBrandsError(null)} />
                    </div>
                  )}
                  <div className="mb-4 flex flex-col gap-2">
                    {brands.length === 0 && (
                      <p className="text-sm text-surface-500">No brand profiles onboarded yet.</p>
                    )}
                    {brands.map((b) => (
                      <div key={b.id} className="rounded-lg border border-surface-700/50 bg-surface-800/40 p-3 text-sm">
                        <span className="font-medium text-surface-200">{b.name}</span>{" "}
                        <span className="text-xs text-surface-500">
                          {b.indexed ? "· indexed" : "· not yet indexed"}
                        </span>
                      </div>
                    ))}
                  </div>
                  <form onSubmit={handleOnboardBrand} className="flex flex-col gap-2 border-t border-surface-700/50 pt-4">
                    <p className="text-xs font-medium text-surface-300">Add a brand</p>
                    <input placeholder="Brand name" className={inputCls} value={brandName} onChange={(e) => setBrandName(e.target.value)} required />
                    <input placeholder="Colors (e.g. #FF5733, #111111)" className={inputCls} value={brandColors} onChange={(e) => setBrandColors(e.target.value)} />
                    <input placeholder="Voice (e.g. bold, energetic, youthful)" className={inputCls} value={brandVoice} onChange={(e) => setBrandVoice(e.target.value)} />
                    <input placeholder="Prohibited imagery (optional)" className={inputCls} value={brandProhibited} onChange={(e) => setBrandProhibited(e.target.value)} />
                    <button type="submit" disabled={onboardingBrand} className="mt-1 self-start rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-brand-600 disabled:opacity-50">
                      {onboardingBrand ? "Onboarding…" : "Add brand"}
                    </button>
                  </form>
                </div>
              )}

              {/* ── Product DNA tab ── */}
              {settingsTab === "product" && (
                <div>
                  <p className="mb-3 text-xs text-surface-500">
                    Real product facts specialists ground pricing and claims in — never invented.
                  </p>
                  {productsError && (
                    <div className="mb-3">
                      <ErrorBanner message={productsError} onDismiss={() => setProductsError(null)} />
                    </div>
                  )}
                  <div className="mb-4 flex flex-col gap-2">
                    {products.length === 0 && (
                      <p className="text-sm text-surface-500">No products onboarded yet.</p>
                    )}
                    {products.map((p) => (
                      <div key={p.id} className="rounded-lg border border-surface-700/50 bg-surface-800/40 p-3 text-sm">
                        <span className="font-medium text-surface-200">{p.name}</span>{" "}
                        <span className="text-xs text-surface-500">
                          {p.indexed ? "· indexed" : "· not yet indexed"}
                          {p.attributes.price != null && ` · $${p.attributes.price}`}
                          {p.attributes.discount_percent != null && ` · ${p.attributes.discount_percent}% off`}
                          {p.attributes.color && ` · ${p.attributes.color}`}
                        </span>
                        {p.attributes.summary && (
                          <p className="mt-1 text-xs text-surface-500">{p.attributes.summary}</p>
                        )}
                      </div>
                    ))}
                  </div>
                  <form onSubmit={handleOnboardProduct} className="flex flex-col gap-2 border-t border-surface-700/50 pt-4">
                    <p className="text-xs font-medium text-surface-300">Add a product</p>
                    <input placeholder="Product name" className={inputCls} value={productName} onChange={(e) => setProductName(e.target.value)} required />
                    <textarea placeholder="Description — what it is, what makes it real" className={`${inputCls} resize-none`} rows={2} value={productDescription} onChange={(e) => setProductDescription(e.target.value)} required />
                    <div className="flex gap-2">
                      <input placeholder="Price (optional)" type="number" step="0.01" className={`w-1/2 ${inputCls}`} value={productPrice} onChange={(e) => setProductPrice(e.target.value)} />
                      <input placeholder="Discount % (optional)" type="number" step="1" className={`w-1/2 ${inputCls}`} value={productDiscount} onChange={(e) => setProductDiscount(e.target.value)} />
                    </div>
                    <input placeholder="Real color (optional, e.g. red — never restricted by brand colors)" className={inputCls} value={productColor} onChange={(e) => setProductColor(e.target.value)} />
                    <button type="submit" disabled={onboardingProduct} className="mt-1 self-start rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-brand-600 disabled:opacity-50">
                      {onboardingProduct ? "Onboarding…" : "Add product"}
                    </button>
                  </form>
                </div>
              )}

              {/* ── Mood Board tab ── */}
              {settingsTab === "moodboard" && (
                <div>
                  <p className="mb-3 text-xs text-surface-500">
                    Prior-campaign assets for semantic search by specialists.
                  </p>
                  {moodBoardError && (
                    <div className="mb-3">
                      <ErrorBanner message={moodBoardError} onDismiss={() => setMoodBoardError(null)} />
                    </div>
                  )}
                  <div className="mb-4 grid grid-cols-3 gap-2">
                    {moodBoardAssets.length === 0 && (
                      <p className="col-span-full text-sm text-surface-500">No mood board assets yet.</p>
                    )}
                    {moodBoardAssets.map((a) => (
                      <div key={a.id} className="group relative aspect-square overflow-hidden rounded-lg border border-surface-700/50">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={moodBoardAssetUrl(a.storage_ref)} alt={a.description} className="h-full w-full object-cover" />
                        {/* Hover overlay */}
                        <div className="absolute inset-0 flex items-end bg-black/60 p-2 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                          <p className="text-[11px] text-white leading-tight">{a.description}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                  <form onSubmit={handleUploadMoodBoard} className="flex flex-col gap-2 border-t border-surface-700/50 pt-4">
                    <p className="text-xs font-medium text-surface-300">Add an asset</p>
                    <input name="moodBoardFile" type="file" accept="image/*" className="rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-surface-300 file:mr-3 file:rounded-md file:border-0 file:bg-surface-700 file:px-2 file:py-1 file:text-xs file:text-surface-200" required />
                    <input placeholder="Description (e.g. last year's summer campaign hero shot)" className={inputCls} value={moodBoardDescription} onChange={(e) => setMoodBoardDescription(e.target.value)} required />
                    <button type="submit" disabled={uploadingMoodBoard} className="mt-1 self-start rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-brand-600 disabled:opacity-50">
                      {uploadingMoodBoard ? "Uploading…" : "Add asset"}
                    </button>
                  </form>
                </div>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

// ── Badge helpers ─────────────────────────────────────────────────────────────

function WorkflowStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    completed: "bg-emerald-900/40 text-emerald-400 border-emerald-700/40",
    generating: "bg-blue-900/40 text-blue-400 border-blue-700/40 animate-pulse-soft",
    error: "bg-red-900/40 text-red-400 border-red-700/40",
    ideating: "bg-surface-800 text-surface-400 border-surface-700/50",
    awaiting_approval: "bg-amber-900/40 text-amber-400 border-amber-700/40",
  };
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${map[status] ?? map.ideating}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function ApprovalBadge({ mode }: { mode: string }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${
      mode === "approve"
        ? "border-amber-700/40 bg-amber-900/30 text-amber-400"
        : "border-surface-700/50 bg-surface-800 text-surface-500"
    }`}>
      {mode}
    </span>
  );
}
