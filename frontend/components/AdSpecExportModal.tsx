"use client";

import { useState } from "react";
import { request } from "@/lib/http";

interface AdSpecExportModalProps {
  elementId: string | null;
  isOpen: boolean;
  onClose: () => void;
}

export default function AdSpecExportModal({ elementId, isOpen, onClose }: AdSpecExportModalProps) {
  const [exporting, setExporting] = useState(false);
  const [exports, setExports] = useState<Record<string, any> | null>(null);

  if (!isOpen || !elementId) return null;

  const handleExportAll = async () => {
    setExporting(true);
    try {
      const res = await request<any>(`/api/v1/canvas/elements/${elementId}/export-all-specs`, {
        method: "POST",
      });
      setExports(res.exports);
    } catch {
      alert("Failed to export ad specs");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-md p-4">
      <div className="w-full max-w-2xl rounded-2xl border border-surface-700 bg-surface-900 p-6 shadow-2xl">
        <div className="flex items-center justify-between border-b border-surface-800 pb-3 mb-4">
          <h3 className="text-base font-semibold text-white">Export Platform Ad Spec Presets</h3>
          <button onClick={onClose} className="text-surface-400 hover:text-white text-lg">✕</button>
        </div>

        {!exports ? (
          <div className="py-8 text-center space-y-4">
            <p className="text-sm text-surface-300">
              Batch convert this canvas element into standard dimensions:
            </p>
            <div className="flex flex-wrap justify-center gap-2 text-xs text-surface-400">
              <span className="rounded-md bg-surface-800 px-2.5 py-1 border border-surface-700">Meta Feed 1:1</span>
              <span className="rounded-md bg-surface-800 px-2.5 py-1 border border-surface-700">IG Story 9:16</span>
              <span className="rounded-md bg-surface-800 px-2.5 py-1 border border-surface-700">Meta Portrait 4:5</span>
              <span className="rounded-md bg-surface-800 px-2.5 py-1 border border-surface-700">Google Banner 16:9</span>
              <span className="rounded-md bg-surface-800 px-2.5 py-1 border border-surface-700">LinkedIn Post 1.91:1</span>
            </div>
            <button
              onClick={handleExportAll}
              disabled={exporting}
              className="mt-4 rounded-xl bg-brand-500 px-6 py-2.5 text-sm font-semibold text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {exporting ? "Rendering All Presets..." : "Generate All Platform Variants"}
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-xs text-emerald-400 font-medium">Successfully generated 5 ad-spec variants:</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 max-h-[360px] overflow-y-auto p-1">
              {Object.entries(exports).map(([key, item]) => (
                <div key={key} className="rounded-xl border border-surface-700 bg-surface-800/80 p-3 flex flex-col justify-between">
                  <div>
                    <span className="text-xs font-semibold text-white block">{item.spec_name}</span>
                    <span className="text-[11px] text-surface-400 block">{item.width} x {item.height} px</span>
                  </div>
                  <a
                    href={`/api/v1/canvas/assets/${item.storage_ref}`}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-3 block text-center rounded-lg bg-surface-700 hover:bg-surface-600 py-1.5 text-xs text-white transition-colors"
                  >
                    Download Asset
                  </a>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-6 flex justify-end pt-3 border-t border-surface-800">
          <button onClick={onClose} className="rounded-lg bg-surface-800 px-5 py-2 text-xs font-medium text-surface-200 hover:bg-surface-700">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
