import { useEffect, useState } from "react";
import { request } from "@/lib/http";

interface DNASectionProps {
  sessionId: string;
}

export default function DNASection({ sessionId }: DNASectionProps) {
  const [activeTab, setActiveTab] = useState<"brand" | "product">("brand");
  const [brandDna, setBrandDna] = useState("");
  const [productDna, setProductDna] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    const fetchSession = async () => {
      setLoading(true);
      try {
        const data = await request<any>(`/api/v1/sessions/${sessionId}`);
        setBrandDna(data.brief?.brand_dna || "");
        setProductDna(data.brief?.product_dna || "");
      } catch (e) {
        console.error(e);
        setError("Failed to load DNA");
      } finally {
        setLoading(false);
      }
    };
    if (sessionId) {
      fetchSession();
    }
  }, [sessionId]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      await request(`/api/v1/sessions/${sessionId}/dna`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ brand_dna: brandDna, product_dna: productDna }),
      });
      setSuccessMsg("DNA updated and guardrails re-derived!");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (e) {
      console.error(e);
      setError(e instanceof Error ? e.message : "Failed to save DNA");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-surface-400">
        <svg className="w-6 h-6 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>Loading DNA...</span>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-surface-700/50 p-4 shrink-0">
        <div>
          <h2 className="text-base font-semibold text-white">Brand & Product DNA</h2>
          <p className="mt-0.5 text-xs text-surface-400">Provide the foundational facts. The AI will synthesize these into strict Guardrails.</p>
        </div>
      </div>

      <div className="flex border-b border-surface-700/50 bg-surface-900/50 shrink-0">
        <button
          onClick={() => setActiveTab("brand")}
          className={`flex-1 py-3 text-sm font-medium transition-colors ${
            activeTab === "brand"
              ? "border-b-2 border-brand-500 text-brand-400"
              : "text-surface-400 hover:text-surface-200"
          }`}
        >
          Brand DNA
        </button>
        <button
          onClick={() => setActiveTab("product")}
          className={`flex-1 py-3 text-sm font-medium transition-colors ${
            activeTab === "product"
              ? "border-b-2 border-brand-500 text-brand-400"
              : "text-surface-400 hover:text-surface-200"
          }`}
        >
          Product DNA
        </button>
      </div>

      {error && (
        <div className="mx-4 mt-3 shrink-0 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
      {successMsg && (
        <div className="mx-4 mt-3 shrink-0 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
          {successMsg}
        </div>
      )}

      <div className="flex-1 p-4 overflow-y-auto space-y-4">
        {activeTab === "brand" ? (
          <div className="h-full flex flex-col space-y-4">
            <div className="flex-1 flex flex-col">
              <label className="text-xs font-semibold text-surface-300 mb-2">Brand Guidelines</label>
              <textarea
                className="flex-1 w-full rounded-xl border border-surface-700 bg-surface-800 p-3 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 resize-none min-h-[140px]"
                placeholder="E.g. We are a modern, minimalist brand. Our primary colors are #FF0000 and white. Our logo should always be in the bottom right corner..."
                value={brandDna}
                onChange={(e) => setBrandDna(e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3 pt-2">
              <div className="rounded-xl border border-surface-700/60 bg-surface-800/60 p-3">
                <label className="block text-xs font-semibold text-surface-200 mb-1">Brand Logo (PNG)</label>
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/svg+xml"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const formData = new FormData();
                    formData.append("file", file);
                    try {
                      setSuccessMsg("Uploading logo...");
                      await fetch(`/api/v1/brands/default/logo`, { method: "POST", body: formData });
                      setSuccessMsg("Brand logo saved!");
                    } catch {
                      setError("Logo upload failed");
                    }
                  }}
                  className="block w-full text-xs text-surface-400 file:mr-2 file:py-1 file:px-2 file:rounded-md file:border-0 file:bg-surface-700 file:text-xs file:text-surface-200 hover:file:bg-surface-600"
                />
              </div>

              <div className="rounded-xl border border-surface-700/60 bg-surface-800/60 p-3">
                <label className="block text-xs font-semibold text-surface-200 mb-1">Custom Font (TTF/OTF)</label>
                <input
                  type="file"
                  accept=".ttf,.otf"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const formData = new FormData();
                    formData.append("file", file);
                    try {
                      setSuccessMsg("Uploading font...");
                      await fetch(`/api/v1/brands/default/fonts`, { method: "POST", body: formData });
                      setSuccessMsg("Custom brand font saved!");
                    } catch {
                      setError("Font upload failed");
                    }
                  }}
                  className="block w-full text-xs text-surface-400 file:mr-2 file:py-1 file:px-2 file:rounded-md file:border-0 file:bg-surface-700 file:text-xs file:text-surface-200 hover:file:bg-surface-600"
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="h-full flex flex-col space-y-4">
            <div className="flex-1 flex flex-col">
              <label className="text-xs font-semibold text-surface-300 mb-2">Product Specifics</label>
              <textarea
                className="flex-1 w-full rounded-xl border border-surface-700 bg-surface-800 p-3 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 resize-none min-h-[140px]"
                placeholder="E.g. The Audit Test Sneaker. Price: $150. Key features: lightweight, breathable mesh, red accents. Never show it being used in mud..."
                value={productDna}
                onChange={(e) => setProductDna(e.target.value)}
              />
            </div>

            <div className="rounded-xl border border-surface-700/60 bg-surface-800/60 p-3">
              <label className="block text-xs font-semibold text-surface-200 mb-1">Product Subject Photo (Grounding PNG/JPG)</label>
              <input
                type="file"
                accept="image/*"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  const formData = new FormData();
                  formData.append("file", file);
                  try {
                    setSuccessMsg("Uploading product photo...");
                    await fetch(`/api/v1/products/default/photo`, { method: "POST", body: formData });
                    setSuccessMsg("Product subject photo saved!");
                  } catch {
                    setError("Product photo upload failed");
                  }
                }}
                className="block w-full text-xs text-surface-400 file:mr-2 file:py-1 file:px-2 file:rounded-md file:border-0 file:bg-surface-700 file:text-xs file:text-surface-200 hover:file:bg-surface-600"
              />
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-surface-700/50 bg-surface-900 p-4 flex justify-end">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center rounded-lg bg-brand-500 px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-600 disabled:opacity-50"
        >
          {saving ? (
            <>
              <svg className="w-4 h-4 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              Synthesizing Rules...
            </>
          ) : (
            "Save & Synthesize"
          )}
        </button>
      </div>
    </div>
  );
}
