import { useEffect, useRef, useState } from "react";

interface GuardrailRule {
  id: string;
  source: "brand" | "product" | "custom";
  rule: string;
  scope: string;
}

interface GuardrailsSectionProps {
  sessionId: string;
}

import { request } from "@/lib/http";
import { getSession, updateGuardrailsEnabled } from "@/lib/api";
import { ProductProfile, getProduct } from "@/lib/product";

export default function GuardrailsSection({ sessionId }: GuardrailsSectionProps) {
  const [rules, setRules] = useState<GuardrailRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rulesRef = useRef<GuardrailRule[]>([]);
  useEffect(() => {
    rulesRef.current = rules;
  }, [rules]);
  const pendingRef = useRef<Promise<void>>(Promise.resolve());

  // Real, live-found gap (2026-09-25, explicit user report: "why can't i see product dna in my
  // frontend/sessions") — the backend has been creating/updating real Product DNA rows from chat
  // and canvas uploads for a while (session.brief["product_profile_ids"]), but nothing here ever
  // read that field or showed a product's actual NAME — only the generic "product"-tagged rule
  // sentences below, with no identity attached to them. This fetches the session's real linked
  // products by id (`GET /api/v1/products/{id}`, the one per-id route that already exists) and
  // shows them as their own real cards, separate from the flat rule list.
  const [linkedProducts, setLinkedProducts] = useState<ProductProfile[]>([]);
  const [productsLoading, setProductsLoading] = useState(false);

  // Per-session on/off toggle (2026-09-25, explicit user ask: "add a toggle to turn off
  // guardrails if user wants to") — loaded off the same `getSession` call `fetchLinkedProducts`
  // already makes, rather than a separate request.
  const [guardrailsEnabled, setGuardrailsEnabled] = useState(true);
  const [guardrailsToggleBusy, setGuardrailsToggleBusy] = useState(false);

  const fetchLinkedProducts = async () => {
    setProductsLoading(true);
    try {
      const session = await getSession(sessionId);
      setGuardrailsEnabled(session.guardrails_enabled);
      const ids = (session.brief?.product_profile_ids as string[] | undefined) || [];
      const results = await Promise.all(ids.map((id) => getProduct(id).catch(() => null)));
      setLinkedProducts(results.filter((p): p is ProductProfile => p !== null));
    } catch (e) {
      console.error(e);
    } finally {
      setProductsLoading(false);
    }
  };

  const handleToggleGuardrails = async () => {
    const next = !guardrailsEnabled;
    setGuardrailsEnabled(next); // optimistic — reverted below on failure
    setGuardrailsToggleBusy(true);
    try {
      await updateGuardrailsEnabled(sessionId, next);
      setError(null);
    } catch (e) {
      console.error(e);
      setGuardrailsEnabled(!next);
      setError("Failed to update guardrails toggle.");
    } finally {
      setGuardrailsToggleBusy(false);
    }
  };

  const [newRuleSource, setNewRuleSource] = useState<"brand" | "product" | "custom">("custom");
  const [newRuleText, setNewRuleText] = useState("");
  const [newRuleScope, setNewRuleScope] = useState("all");

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");

  const fetchGuardrails = async () => {
    setLoading(true);
    try {
      const data = await request<{ rules: GuardrailRule[] }>(`/api/v1/sessions/${sessionId}/guardrails`);
      setRules(data.rules || []);
      setError(null);
    } catch (e) {
      console.error(e);
      setError("Failed to load guardrails.");
    } finally {
      setLoading(false);
    }
  };

  const mutateGuardrails = (compute: (current: GuardrailRule[]) => GuardrailRule[]) => {
    const run = async () => {
      try {
        const updated = compute(rulesRef.current);
        const data = await request<{ rules: GuardrailRule[] }>(`/api/v1/sessions/${sessionId}/guardrails`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rules: updated }),
        });
        rulesRef.current = data.rules || [];
        setRules(data.rules || []);
        setError(null);
      } catch (e) {
        console.error(e);
        setError(e instanceof Error ? e.message : "Failed to update guardrails.");
      }
    };
    pendingRef.current = pendingRef.current.then(run);
    return pendingRef.current;
  };

  useEffect(() => {
    if (sessionId) {
      fetchGuardrails();
      fetchLinkedProducts();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const handleDelete = (id: string) => {
    mutateGuardrails((current) => current.filter(r => r.id !== id));
  };

  const startEdit = (r: GuardrailRule) => {
    setEditingId(r.id);
    setEditingText(r.rule);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingText("");
  };

  const handleSaveEdit = (id: string) => {
    const trimmed = editingText.trim();
    if (!trimmed) return;
    mutateGuardrails((current) => current.map((r) => (r.id === id ? { ...r, rule: trimmed } : r)));
    setEditingId(null);
    setEditingText("");
  };

  const handleAdd = async () => {
    if (!newRuleText.trim()) return;
    setLoading(true);
    try {
      const data = await request<{ rules: GuardrailRule[] }>(`/api/v1/sessions/${sessionId}/guardrails`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rule: newRuleText,
          scope: newRuleScope,
          source: newRuleSource,
        }),
      });
      rulesRef.current = data.rules || [];
      setRules(data.rules || []);
      setError(null);
    } catch (e) {
      console.error(e);
      setError(e instanceof Error ? e.message : "Failed to add guardrail.");
    } finally {
      setLoading(false);
      setNewRuleText("");
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-surface-700/50 p-4 shrink-0">
        <div>
          <h2 className="text-base font-semibold text-white">DNA & Guardrails</h2>
          <p className="mt-0.5 text-xs text-surface-400">Strict rules the AI must follow when generating content.</p>
        </div>
        <button
          onClick={() => {
            fetchGuardrails();
            fetchLinkedProducts();
          }}
          className="text-surface-400 hover:text-white transition-colors"
          title="Refresh"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
      </div>

      {/* Per-session on/off toggle — when off, no guardrail rules are injected into any
          specialist's prompt and the post-generation compliance gate is skipped entirely for this
          session (session_service.py: `set_current_guardrails_xml`/`_run_compliance_background`).
          The rules below stay visible/editable either way; this only controls enforcement. */}
      <div className="mx-4 mt-3 shrink-0 flex items-center justify-between rounded-lg border border-surface-700/50 bg-surface-800/30 px-3 py-2">
        <div>
          <p className="text-xs font-medium text-white">Guardrails enforcement</p>
          <p className="text-[11px] text-surface-400">
            {guardrailsEnabled ? "Rules are enforced on every generation." : "Rules are OFF — nothing below is enforced right now."}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={guardrailsEnabled}
          disabled={guardrailsToggleBusy}
          onClick={handleToggleGuardrails}
          className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
            guardrailsEnabled ? "bg-emerald-500" : "bg-surface-600"
          }`}
        >
          <span
            className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
              guardrailsEnabled ? "translate-x-5" : "translate-x-0.5"
            }`}
          />
        </button>
      </div>

      {error && (
        <div className="mx-4 mt-3 shrink-0 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}

      {/* Products in this session — the real ProductProfileModel rows linked via
          session.brief["product_profile_ids"] (created/refined from chat and canvas uploads),
          shown by actual name/attributes instead of only as anonymous "product"-tagged rule
          sentences further down. */}
      {(productsLoading || linkedProducts.length > 0) && (
        <div className="mx-4 mt-3 shrink-0 space-y-2">
          <p className="text-[10px] uppercase font-bold tracking-wider text-surface-400">
            Products in this session
          </p>
          {productsLoading && linkedProducts.length === 0 ? (
            <div className="text-xs text-surface-500">Loading products…</div>
          ) : (
            linkedProducts.map((p) => (
              <div key={p.id} className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-3">
                <p className="text-sm font-medium text-white">{p.name}</p>
                {p.attributes?.summary && (
                  <p className="mt-1 text-xs text-surface-300">{p.attributes.summary}</p>
                )}
                {(p.attributes?.must_show?.length || p.attributes?.never_show?.length) ? (
                  <div className="mt-2 space-y-1 text-[11px]">
                    {p.attributes.must_show?.length > 0 && (
                      <p className="text-surface-400">
                        <span className="text-emerald-400">Must show:</span> {p.attributes.must_show.join(", ")}
                      </p>
                    )}
                    {p.attributes.never_show?.length > 0 && (
                      <p className="text-surface-400">
                        <span className="text-red-400">Never show:</span> {p.attributes.never_show.join(", ")}
                      </p>
                    )}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-surface-700">
        {loading && rules.length === 0 ? (
          <div className="flex items-center justify-center h-full text-surface-400">
            <svg className="w-5 h-5 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span className="text-sm">Loading DNA...</span>
          </div>
        ) : rules.length === 0 ? (
          <div className="flex items-center justify-center h-full text-sm text-surface-500 text-center px-4">
            No DNA rules set. Add a rule below, or ask the AI to generate some in the chat!
          </div>
        ) : (
          rules.map((r) => (
            <div key={r.id} className="group relative rounded-xl border border-surface-700/50 bg-surface-800/30 p-3 transition-all hover:bg-surface-800/50 hover:border-surface-600">
              <div className="flex justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className={`text-[9px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full ${
                      r.source === 'brand' ? 'bg-purple-500/20 text-purple-300' :
                      r.source === 'product' ? 'bg-blue-500/20 text-blue-300' :
                      'bg-emerald-500/20 text-emerald-300'
                    }`}>
                      {r.source}
                    </span>
                    <span className="text-[10px] text-surface-400">Scope: {r.scope}</span>
                  </div>
                  {editingId === r.id ? (
                    <div className="space-y-2 mt-2">
                      <textarea
                        autoFocus
                        value={editingText}
                        onChange={(e) => setEditingText(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            handleSaveEdit(r.id);
                          } else if (e.key === "Escape") {
                            cancelEdit();
                          }
                        }}
                        rows={2}
                        className="w-full resize-none rounded-lg border border-brand-500 bg-surface-900 px-2 py-1.5 text-xs text-white focus:outline-none"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={() => handleSaveEdit(r.id)}
                          disabled={!editingText.trim()}
                          className="rounded-lg bg-brand-500 px-2.5 py-1 text-[10px] font-medium text-white hover:bg-brand-600 disabled:opacity-50"
                        >
                          Save
                        </button>
                        <button
                          onClick={cancelEdit}
                          className="rounded-lg border border-surface-700 px-2.5 py-1 text-[10px] font-medium text-surface-300 hover:bg-surface-800"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-surface-200 mt-1">{r.rule}</p>
                  )}
                </div>
                {editingId !== r.id && (
                  <div className="flex flex-shrink-0 flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity pt-1">
                    <button onClick={() => startEdit(r)} className="p-1 text-surface-400 hover:text-white" title="Edit">
                      <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                    </button>
                    <button onClick={() => handleDelete(r.id)} className="p-1 text-red-400 hover:text-red-300" title="Delete">
                      <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Add New */}
      <div className="shrink-0 border-t border-surface-700/50 bg-surface-900/30 p-4">
        <div className="flex gap-2 mb-2">
          <select 
            value={newRuleSource}
            onChange={(e) => setNewRuleSource(e.target.value as any)}
            className="rounded border border-surface-700 bg-surface-800 px-2 py-1 text-[10px] text-surface-300 focus:outline-none"
          >
            <option value="brand">Brand</option>
            <option value="product">Product</option>
            <option value="custom">Custom</option>
          </select>
          <select 
            value={newRuleScope}
            onChange={(e) => setNewRuleScope(e.target.value)}
            className="rounded border border-surface-700 bg-surface-800 px-2 py-1 text-[10px] text-surface-300 focus:outline-none"
          >
            <option value="all">All</option>
            <option value="image">Image</option>
            <option value="video">Video</option>
          </select>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Add a new rule..."
            value={newRuleText}
            onChange={(e) => setNewRuleText(e.target.value)}
            className="flex-1 rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-xs text-white placeholder-surface-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          />
          <button
            onClick={handleAdd}
            disabled={!newRuleText.trim() || loading}
            className="rounded-lg bg-brand-500 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
