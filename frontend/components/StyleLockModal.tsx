"use client";

import { useEffect, useState } from "react";
import { request } from "@/lib/http";
import { CloseButton } from "@/components/CloseButton";

interface StyleLockModalProps {
  sessionId: string;
  isOpen: boolean;
  onClose: () => void;
}

export default function StyleLockModal({ sessionId, isOpen, onClose }: StyleLockModalProps) {
  const [styleSeed, setStyleSeed] = useState<number | "">("");
  const [styleRef, setStyleRef] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Esc key closes the modal (8e) — same pattern as the Modal wrapper in page.tsx.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSave = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await request(`/api/v1/sessions/${sessionId}/style`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          style_ref_storage_ref: styleRef || null,
          style_seed: styleSeed !== "" ? Number(styleSeed) : null,
        }),
      });
      setMsg("Campaign style lock updated!");
      setTimeout(() => {
        setMsg(null);
        onClose();
      }, 1500);
    } catch {
      setMsg("Failed to update style lock");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-md rounded-2xl border border-surface-700 bg-surface-900 p-6 shadow-2xl">
        <CloseButton onClose={onClose} />
        <div className="flex items-center border-b border-surface-800 pb-3 mb-4">
          <h3 className="text-base font-semibold text-white">Campaign Style Lock</h3>
        </div>

        {msg && (
          <div className="mb-4 rounded-lg bg-brand-500/20 border border-brand-500/40 p-2.5 text-xs text-brand-300">
            {msg}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-surface-300 mb-1">Style Reference Storage Ref</label>
            <input
              type="text"
              placeholder="e.g., asset_abc123"
              value={styleRef}
              onChange={(e) => setStyleRef(e.target.value)}
              className="w-full rounded-xl border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-white focus:border-brand-500 focus:outline-none"
            />
            <p className="mt-1 text-[11px] text-surface-400">Lock aesthetic features from a previous tile across all new generations.</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-surface-300 mb-1">Reproducibility Seed</label>
            <input
              type="number"
              placeholder="e.g., 42"
              value={styleSeed}
              onChange={(e) => setStyleSeed(e.target.value ? Number(e.target.value) : "")}
              className="w-full rounded-xl border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-white focus:border-brand-500 focus:outline-none"
            />
            <p className="mt-1 text-[11px] text-surface-400">Fixed seed for identical composition grounding.</p>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3 pt-3 border-t border-surface-800">
          <button onClick={onClose} className="rounded-lg px-4 py-2 text-xs text-surface-300 hover:bg-surface-800">
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-lg bg-brand-500 px-5 py-2 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Lock Campaign Style"}
          </button>
        </div>
      </div>
    </div>
  );
}
