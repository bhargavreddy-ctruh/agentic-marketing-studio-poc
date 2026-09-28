import { useEffect, useState } from "react";
import { request } from "@/lib/http";
import { ProductProfile, getProduct } from "@/lib/product";
import { BrandProfile, listBrands, updateBrandFacts } from "@/lib/brand";

interface DNASectionProps {
  sessionId: string;
}

export default function DNASection({ sessionId }: DNASectionProps) {
  const [activeTab, setActiveTab] = useState<"campaign" | "brand" | "product">("campaign");

  // Real, live-found gap (2026-09-25, explicit user report: this tab "is still empty") — this
  // whole form only ever showed `session.brief.productDetails`/`brandDetails`, manual free-text
  // fields nothing populates automatically. The REAL onboarded profiles this session actually uses
  // for guardrails (`GuardrailService._load_brand_and_products_json`'s same explicit-link-else-
  // fallback logic) live entirely server-side and were never surfaced here. Brand DNA is simpler
  // than product: `GET /api/v1/brands` (`listBrands()`) is a real, auth-scoped, account-wide list —
  // no per-id lookup or localStorage workaround needed, unlike products. Ported from
  // `poc/frontend/components/DNASection.tsx` (2026-09-26 — the new frontend's own version of this
  // file was an earlier snapshot that predates this real data-wiring work entirely).
  const [detectedProducts, setDetectedProducts] = useState<ProductProfile[]>([]);
  const [detectedLoading, setDetectedLoading] = useState(false);
  const [detectedBrand, setDetectedBrand] = useState<BrandProfile | null>(null);
  const [detectedBrandLoading, setDetectedBrandLoading] = useState(false);

  // 2026-09-25, real requirement: "give the user option to edit" the brand facts shown above —
  // the card was read-only; this is a real add/edit/remove key-value editor over the brand's
  // actual `raw_facts` (genuinely free-form — no fixed schema — so a fixed form can't cover it).
  const [editingBrand, setEditingBrand] = useState(false);
  const [brandFactRows, setBrandFactRows] = useState<{ key: string; value: string }[]>([]);
  const [savingBrandFacts, setSavingBrandFacts] = useState(false);
  const [detectedCampaign, setDetectedCampaign] = useState<{ idea: string; audience: string; goal: string } | null>(null);

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
        // Real, live-found gap (2026-09-25, explicit user report: "campaign is empty, even that
        // has to be filled based on user chat history") — `campaignDetails` is a manual field
        // nothing populated automatically. Ideation already synthesizes a real, running campaign
        // idea into `brief.idea` on every turn, and now also `brief.audience`/`brief.goal` when
        // inferable (`ideation_service.py`) — this pre-fills from those real, already-computed
        // values instead of leaving the tab blank until the user types into it themselves.
        const detectedIdea = String(data.brief?.idea || "");
        const detectedAudience = String(data.brief?.audience || "");
        const detectedGoal = String(data.brief?.goal || "");
        if (detectedIdea || detectedAudience || detectedGoal) {
          setDetectedCampaign({ idea: detectedIdea, audience: detectedAudience, goal: detectedGoal });
        }
        if (data.brief?.campaignDetails) {
          setCampaignDetails(data.brief.campaignDetails);
        } else if (detectedIdea || detectedAudience || detectedGoal) {
          setCampaignDetails({ campaignIdea: detectedIdea, audience: detectedAudience, goal: detectedGoal });
        }

        setDetectedBrandLoading(true);
        let brand: BrandProfile | null = null;
        try {
          const brands = await listBrands();
          // Mirrors the backend's own resolution (`GuardrailService._load_brand_and_products_json`):
          // explicit `session.brand_profile_id` wins, else the user's first onboarded brand.
          brand = (data.brand_profile_id && brands.find((b) => b.id === data.brand_profile_id)) || brands[0] || null;
          setDetectedBrand(brand);
        } catch (e) {
          console.error(e);
        } finally {
          setDetectedBrandLoading(false);
        }

        if (data.brief?.brandDetails) {
          setBrandDetails(data.brief.brandDetails);
        } else if (data.brief?.brand_dna) {
          setBrandDetails(prev => ({ ...prev, visualIdentity: data.brief.brand_dna }));
        } else if (brand) {
          // Nothing manually entered yet — pre-fill from the real onboarded brand so this tab
          // reflects what the session actually uses instead of sitting empty.
          const facts = brand.raw_facts || {};
          setBrandDetails(prev => ({
            ...prev,
            // Real, live-found bug (2026-09-25, caught via live cross-session verification): a
            // brand saved through THIS form writes `raw_facts` under ITS OWN field labels
            // ("Voice and Tone"/"Visual Identity & Colors" — see the backend's `update_dna` route)
            // — but this read-side only checked the DIFFERENT key names the home page's separate
            // brand-onboarding form happens to use ("Brand Personality"/"Brand Colors"). A brand
            // saved from this exact tab, in a DIFFERENT session, silently failed to pre-fill.
            // Checks this form's own keys first, falls back to the other form's for brands
            // onboarded there instead.
            voiceAndTone: String(facts["Voice and Tone"] || facts["Brand Personality"] || facts["Core Values"] || ""),
            visualIdentity: String(facts["Visual Identity & Colors"] || facts["Brand Colors"] || ""),
          }));
        }

        const productIds: string[] = data.brief?.product_profile_ids || [];
        let detected: ProductProfile[] = [];
        if (productIds.length > 0) {
          setDetectedLoading(true);
          const results = await Promise.all(productIds.map((id) => getProduct(id).catch(() => null)));
          detected = results.filter((p): p is ProductProfile => p !== null);
          setDetectedProducts(detected);
          setDetectedLoading(false);
        }

        if (data.brief?.productDetails) {
          setProductDetails(data.brief.productDetails);
        } else if (data.brief?.product_dna) {
          setProductDetails(prev => ({ ...prev, productDescription: data.brief.product_dna }));
        } else if (detected.length > 0) {
          // Nothing manually entered yet — pre-fill this form from the real, auto-extracted
          // product so the tab reflects what the session actually knows instead of sitting empty.
          const primary = detected[0];
          setProductDetails({
            name: primary.name,
            category: "",
            productDescription: primary.attributes?.summary || "",
          });
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

  const startEditBrand = () => {
    if (!detectedBrand) return;
    setBrandFactRows(Object.entries(detectedBrand.raw_facts || {}).map(([key, value]) => ({ key, value: String(value) })));
    setEditingBrand(true);
  };

  const cancelEditBrand = () => {
    setEditingBrand(false);
    setBrandFactRows([]);
  };

  const saveBrandFacts = async () => {
    if (!detectedBrand) return;
    setSavingBrandFacts(true);
    setError(null);
    try {
      const facts: Record<string, string> = {};
      for (const row of brandFactRows) {
        const key = row.key.trim();
        if (key) facts[key] = row.value;
      }
      const updated = await updateBrandFacts(detectedBrand.id, detectedBrand.name, facts);
      setDetectedBrand(updated);
      setEditingBrand(false);
      setBrandFactRows([]);
      setSuccessMsg("Brand facts updated!");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (e) {
      console.error(e);
      setError(e instanceof Error ? e.message : "Failed to update brand facts");
    } finally {
      setSavingBrandFacts(false);
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
            {detectedCampaign && (
              <div className="space-y-2">
                <p className="text-[10px] uppercase font-bold tracking-wider text-surface-400">
                  Detected from chat
                </p>
                <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-3 space-y-1 text-[11px]">
                  {detectedCampaign.idea && <p className="text-surface-200">{detectedCampaign.idea}</p>}
                  {detectedCampaign.audience && (
                    <p className="text-surface-400"><span className="text-surface-300">Audience:</span> {detectedCampaign.audience}</p>
                  )}
                  {detectedCampaign.goal && (
                    <p className="text-surface-400"><span className="text-surface-300">Goal:</span> {detectedCampaign.goal}</p>
                  )}
                </div>
              </div>
            )}
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
            {(detectedBrandLoading || detectedBrand) && (
              <div className="space-y-2">
                <p className="text-[10px] uppercase font-bold tracking-wider text-surface-400">
                  Your brand (shared across all your sessions)
                </p>
                {detectedBrandLoading ? (
                  <div className="text-xs text-surface-500">Loading…</div>
                ) : detectedBrand && editingBrand ? (
                  <div className="rounded-xl border border-purple-500/30 bg-purple-500/5 p-3 space-y-2">
                    {brandFactRows.map((row, i) => (
                      <div key={i} className="flex gap-1.5 items-center">
                        <input
                          className="w-1/3 rounded border border-surface-700 bg-surface-800 px-1.5 py-1 text-[11px] text-white focus:outline-none"
                          placeholder="Fact name"
                          value={row.key}
                          onChange={(e) => setBrandFactRows(rows => rows.map((r, ri) => ri === i ? { ...r, key: e.target.value } : r))}
                        />
                        <input
                          className="flex-1 rounded border border-surface-700 bg-surface-800 px-1.5 py-1 text-[11px] text-white focus:outline-none"
                          placeholder="Value"
                          value={row.value}
                          onChange={(e) => setBrandFactRows(rows => rows.map((r, ri) => ri === i ? { ...r, value: e.target.value } : r))}
                        />
                        <button
                          onClick={() => setBrandFactRows(rows => rows.filter((_, ri) => ri !== i))}
                          className="text-red-400 hover:text-red-300 px-1"
                          title="Remove"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => setBrandFactRows(rows => [...rows, { key: "", value: "" }])}
                        className="rounded-lg border border-surface-700 px-2.5 py-1 text-[10px] font-medium text-surface-300 hover:bg-surface-800"
                      >
                        + Add fact
                      </button>
                      <div className="flex-1" />
                      <button
                        onClick={cancelEditBrand}
                        disabled={savingBrandFacts}
                        className="rounded-lg border border-surface-700 px-2.5 py-1 text-[10px] font-medium text-surface-300 hover:bg-surface-800 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={saveBrandFacts}
                        disabled={savingBrandFacts}
                        className="rounded-lg bg-brand-500 px-2.5 py-1 text-[10px] font-medium text-white hover:bg-brand-600 disabled:opacity-50"
                      >
                        {savingBrandFacts ? "Saving..." : "Save"}
                      </button>
                    </div>
                  </div>
                ) : detectedBrand ? (
                  <div className="group relative rounded-xl border border-purple-500/30 bg-purple-500/5 p-3">
                    <button
                      onClick={startEditBrand}
                      className="absolute top-3 right-3 text-[10px] font-medium text-surface-400 opacity-0 group-hover:opacity-100 hover:text-white transition-opacity"
                    >
                      Edit
                    </button>
                    <p className="text-sm font-medium text-white">{detectedBrand.name}</p>
                    <div className="mt-1 space-y-0.5 text-[11px] text-surface-300">
                      {Object.entries(detectedBrand.raw_facts || {}).map(([k, v]) => (
                        <p key={k}><span className="text-surface-400">{k}:</span> {String(v)}</p>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            )}
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
            {(detectedLoading || detectedProducts.length > 0) && (
              <div className="space-y-2">
                <p className="text-[10px] uppercase font-bold tracking-wider text-surface-400">
                  Detected from chat & canvas
                </p>
                {detectedLoading ? (
                  <div className="text-xs text-surface-500">Loading…</div>
                ) : (
                  detectedProducts.map((p) => (
                    <div key={p.id} className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-3">
                      <p className="text-sm font-medium text-white">{p.name}</p>
                      {p.attributes?.summary && (
                        <p className="mt-1 text-xs text-surface-300">{p.attributes.summary}</p>
                      )}
                    </div>
                  ))
                )}
                <p className="text-[10px] text-surface-500">
                  The form below is pre-filled from the first one — edit and save to add more detail on top of it.
                </p>
              </div>
            )}
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
