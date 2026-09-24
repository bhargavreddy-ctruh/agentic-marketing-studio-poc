import { useEffect, useState } from "react";
import { request } from "@/lib/http";

interface DNASectionProps {
  sessionId: string;
}

export default function DNASection({ sessionId }: DNASectionProps) {
  const [activeTab, setActiveTab] = useState<"campaign" | "brand" | "product">("campaign");
  
  const [campaignDetails, setCampaignDetails] = useState({
    campaignIdea: "",
    audience: "",
    goal: ""
  });

  const [brandDetails, setBrandDetails] = useState({
    voiceAndTone: "",
    visualIdentity: "",
    logoRules: "",
    logoImage: ""
  });

  const [productDetails, setProductDetails] = useState({
    name: "",
    category: "",
    productDescription: ""
  });

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    const fetchSession = async () => {
      setLoading(true);
      try {
        const data = await request<any>(`/api/v1/sessions/${sessionId}`);
        if (data.brief?.campaignDetails) setCampaignDetails(data.brief.campaignDetails);
        
        if (data.brief?.brandDetails) {
          setBrandDetails(data.brief.brandDetails);
        } else if (data.brief?.brand_dna) {
          setBrandDetails(prev => ({ ...prev, visualIdentity: data.brief.brand_dna }));
        }

        if (data.brief?.productDetails) {
          setProductDetails(data.brief.productDetails);
        } else if (data.brief?.product_dna) {
          setProductDetails(prev => ({ ...prev, productDescription: data.brief.product_dna }));
        }
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
        body: JSON.stringify({ 
          campaignDetails,
          brandDetails,
          productDetails,
          // keep backward compatibility for backend schema
          brand_dna: brandDetails.visualIdentity, 
          product_dna: productDetails.productDescription
        }),
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
          <h2 className="text-base font-semibold text-white">Campaign, Brand & Product DNA</h2>
          <p className="mt-0.5 text-xs text-surface-400">Provide the foundational facts. The AI will synthesize these into strict Guardrails.</p>
        </div>
      </div>

      <div className="flex border-b border-surface-700/50 bg-surface-900/50 shrink-0">
        <button
          onClick={() => setActiveTab("campaign")}
          className={`flex-1 py-3 text-sm font-medium transition-colors ${
            activeTab === "campaign"
              ? "border-b-2 border-brand-500 text-brand-400"
              : "text-surface-400 hover:text-surface-200"
          }`}
        >
          Campaign
        </button>
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
        {activeTab === "campaign" && (
          <div className="h-full flex flex-col space-y-4">
            <div>
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Campaign Idea / Tagline</label>
              <input
                className="w-full rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="E.g. Summer vibes collection..."
                value={campaignDetails.campaignIdea}
                onChange={(e) => setCampaignDetails({ ...campaignDetails, campaignIdea: e.target.value })}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Audience / Persona</label>
              <input
                className="w-full rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="E.g. Gen Z, urban lifestyle..."
                value={campaignDetails.audience}
                onChange={(e) => setCampaignDetails({ ...campaignDetails, audience: e.target.value })}
              />
            </div>
            <div className="flex-1">
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Primary Goals</label>
              <textarea
                className="w-full h-[100px] rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 resize-none"
                placeholder="E.g. Brand awareness, direct sales..."
                value={campaignDetails.goal}
                onChange={(e) => setCampaignDetails({ ...campaignDetails, goal: e.target.value })}
              />
            </div>
          </div>
        )}

        {activeTab === "brand" && (
          <div className="h-full flex flex-col space-y-4">
            <div>
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Voice and Tone</label>
              <input
                className="w-full rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="E.g. Playful, energetic, professional..."
                value={brandDetails.voiceAndTone}
                onChange={(e) => setBrandDetails({ ...brandDetails, voiceAndTone: e.target.value })}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Visual Identity & Colors</label>
              <input
                className="w-full rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="E.g. Neon colors, futuristic styling..."
                value={brandDetails.visualIdentity}
                onChange={(e) => setBrandDetails({ ...brandDetails, visualIdentity: e.target.value })}
              />
            </div>
            <div className="flex-1">
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Logo Rules & Constraints</label>
              <textarea
                className="w-full h-[100px] rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 resize-none"
                placeholder="E.g. Logo must always have 20px padding..."
                value={brandDetails.logoRules}
                onChange={(e) => setBrandDetails({ ...brandDetails, logoRules: e.target.value })}
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
        )}

        {activeTab === "product" && (
          <div className="h-full flex flex-col space-y-4">
            <div>
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Product Name</label>
              <input
                className="w-full rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="E.g. Audit Test Sneaker"
                value={productDetails.name}
                onChange={(e) => setProductDetails({ ...productDetails, name: e.target.value })}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Category</label>
              <input
                className="w-full rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="E.g. Footwear"
                value={productDetails.category}
                onChange={(e) => setProductDetails({ ...productDetails, category: e.target.value })}
              />
            </div>
            <div className="flex-1">
              <label className="text-xs font-semibold text-surface-300 mb-1 block">Product Description & Specs</label>
              <textarea
                className="w-full h-[100px] rounded-lg border border-surface-700 bg-surface-800 p-2.5 text-sm text-white focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 resize-none"
                placeholder="E.g. Price: $150. Key features: lightweight..."
                value={productDetails.productDescription}
                onChange={(e) => setProductDetails({ ...productDetails, productDescription: e.target.value })}
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
