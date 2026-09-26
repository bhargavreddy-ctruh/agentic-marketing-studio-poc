/**
 * Typed client for the real backend Brand DNA API — Tasks_Workflows.md #3. Mirrors
 * `poc/backend/src/schemas/brand/{requests,responses}.py` field-for-field.
 */
import { request } from "./http";

export interface BrandProfile {
  id: string;
  name: string;
  raw_facts: Record<string, unknown>;
  guardrails: Record<string, unknown>;
  indexed: boolean;
  created_at: string;
}

/** The current user's own onboarded brands (Tasks_Workflows.md #3) — the home page's Brand DNA
 * settings panel real data source. */
export async function listBrands(): Promise<BrandProfile[]> {
  return request<BrandProfile[]>("/api/v1/brands");
}

/** `rawFacts` is deliberately free-form (colors, voice, logo rules, prohibited imagery — whatever
 * the user actually has) — the backend's own LLM-based guardrail synthesis is what extracts real
 * structure from it, not this client. */
export async function onboardBrand(
  name: string,
  rawFacts: Record<string, unknown>,
): Promise<BrandProfile> {
  return request<BrandProfile>("/api/v1/brands", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, raw_facts: rawFacts }),
  });
}

/** 2026-09-25, real requirement: "give the user option to edit" their brand's real facts —
 * REPLACES an existing brand's `raw_facts` outright (backend does `merge=False` when `brand_id`
 * is given, see `api/v1/brand/routes.py`), so a key the user removed here actually stays removed. */
export async function updateBrandFacts(
  brandId: string,
  name: string,
  rawFacts: Record<string, unknown>,
): Promise<BrandProfile> {
  return request<BrandProfile>("/api/v1/brands", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, raw_facts: rawFacts, brand_id: brandId }),
  });
}
