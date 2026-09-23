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

import { createPortal } from "react-dom";
import { request } from "@/lib/http";

export default function GuardrailsSection({ sessionId }: GuardrailsSectionProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [rules, setRules] = useState<GuardrailRule[]>([]);
  const [loading, setLoading] = useState(false);
  // Real, live-found bug (2026-09-23 frontend audit): every failure here (fetch/add/edit/delete)
  // only did `console.error` — no `error` state, nothing shown to the user, unlike `CanvasView.tsx`,
  // which does surface a visible banner on failure. A failed delete/edit/add looked like nothing
  // happened, with zero explanation. Surfaced below, same pattern as `CanvasView.tsx`.
  const [error, setError] = useState<string | null>(null);
  // Always the latest `rules`, kept in sync by the effect below — `mutateGuardrails` reads THIS,
  // not the `rules` closed over at click time, so two quick actions (e.g. delete then edit) each
  // compute their diff against the other's real result instead of a stale pre-action snapshot.
  // Real, live-found race (2026-09-23 frontend audit): without this, editing rule B right after
  // deleting rule A could silently resurrect A if edit's PUT (built from the pre-delete list)
  // resolved after delete's PUT. `pendingRef` below additionally serializes the actual requests so
  // they can never even overlap in flight.
  const rulesRef = useRef<GuardrailRule[]>([]);
  useEffect(() => {
    rulesRef.current = rules;
  }, [rules]);
  const pendingRef = useRef<Promise<void>>(Promise.resolve());

  // Add new rule state
  const [newRuleSource, setNewRuleSource] = useState<"brand" | "product" | "custom">("custom");
  const [newRuleText, setNewRuleText] = useState("");
  const [newRuleScope, setNewRuleScope] = useState("all");

  // Edit-in-place state — which rule (by id) is currently being edited, and its draft text.
  // The backend has no dedicated "edit one rule" endpoint; the existing PUT (full-set replace,
  // already used by `handleDelete` below) is reused here too, just with this one rule's `rule`
  // text swapped in the array before sending.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");

  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const fetchGuardrails = async () => {
    setLoading(true);
    try {
      const data = await request<{ rules: GuardrailRule[] }>(`/api/v1/sessions/${sessionId}/guardrails`);
      setRules(data.rules || []);
      setError(null);
    } catch (e) {
      console.error(e);
      setError(e instanceof Error ? e.message : "Failed to load guardrails.");
    } finally {
      setLoading(false);
    }
  };

  /** Every rule mutation (delete/edit) goes through here: `compute` reads the LATEST rules
   * (`rulesRef.current`) at the moment it actually runs, and successive calls are chained onto
   * `pendingRef` so their real PUT requests can never overlap in flight — fixes the real race
   * described above `rulesRef`'s declaration. */
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
    if (isOpen) {
      fetchGuardrails();
    }
  }, [isOpen, sessionId]);

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

  const modalContent = isOpen ? (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-200 p-4">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-surface-700 bg-surface-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-surface-800 p-6">
          <div>
            <h2 className="text-xl font-bold text-white">Active Guardrails</h2>
            <p className="mt-1 text-sm text-surface-400">Strict rules the AI must follow when generating content.</p>
          </div>
          <button
            onClick={() => setIsOpen(false)}
            className="rounded-full p-2 text-surface-400 hover:bg-surface-800 hover:text-white transition-colors"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {error && (
          <div className="mx-6 mt-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2.5 text-sm text-red-300">
            {error}
          </div>
        )}

        {/* List */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {loading ? (
            <div className="text-center text-surface-400 py-8 animate-pulse">Processing guardrails...</div>
          ) : rules.length === 0 ? (
            <div className="text-center text-surface-400 py-8">No guardrails active.</div>
          ) : (
            rules.map((r) => (
              <div key={r.id} className="group relative rounded-xl border border-surface-700/50 bg-surface-800/30 p-4 transition-all hover:bg-surface-800/50 hover:border-surface-600">
                <div className="flex justify-between gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full ${
                        r.source === 'brand' ? 'bg-purple-500/20 text-purple-300' :
                        r.source === 'product' ? 'bg-blue-500/20 text-blue-300' :
                        'bg-emerald-500/20 text-emerald-300'
                      }`}>
                        {r.source}
                      </span>
                      <span className="text-xs text-surface-400">Scope: {r.scope}</span>
                    </div>
                    {editingId === r.id ? (
                      <div className="space-y-2">
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
                          className="w-full resize-none rounded-lg border border-brand-500 bg-surface-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-brand-500"
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleSaveEdit(r.id)}
                            disabled={!editingText.trim()}
                            className="rounded-lg bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            Save
                          </button>
                          <button
                            onClick={cancelEdit}
                            className="rounded-lg border border-surface-700 px-3 py-1.5 text-xs font-medium text-surface-300 hover:bg-surface-800"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm text-surface-200">{r.rule}</p>
                    )}
                  </div>
                  {editingId !== r.id && (
                    <div className="flex flex-shrink-0 gap-1 opacity-0 group-hover:opacity-100 transition-all">
                      <button
                        onClick={() => startEdit(r)}
                        className="self-start p-2 text-surface-300 hover:bg-surface-700/50 rounded-lg transition-all"
                        title="Edit guardrail"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                      </button>
                      <button
                        onClick={() => handleDelete(r.id)}
                        className="self-start p-2 text-red-400 hover:bg-red-500/10 rounded-lg transition-all"
                        title="Delete guardrail"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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
        <div className="border-t border-surface-800 bg-surface-900/50 p-6">
          <h3 className="text-sm font-medium text-surface-300 mb-4">Add Custom Guardrail</h3>
          <div className="flex gap-3">
            <input
              type="text"
              placeholder="E.g., Never use the color red"
              value={newRuleText}
              onChange={(e) => setNewRuleText(e.target.value)}
              className="flex-1 rounded-lg border border-surface-700 bg-surface-800 px-4 py-2.5 text-sm text-white placeholder-surface-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
            <button
              onClick={handleAdd}
              disabled={!newRuleText.trim()}
              className="rounded-lg bg-brand-500 px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Add
            </button>
          </div>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <>
      <button
        onClick={() => setIsOpen(true)}
        className="pointer-events-auto flex items-center gap-2 rounded-full border border-surface-700/50 bg-surface-900/40 px-4 py-2 text-sm font-medium text-surface-200 shadow-xl backdrop-blur-xl transition-all hover:-translate-y-0.5 hover:bg-surface-800/60 hover:text-white"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
        Guardrails
      </button>

      {mounted && typeof document !== "undefined" ? createPortal(modalContent, document.body) : modalContent}
    </>
  );
}
