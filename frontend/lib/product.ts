/**
 * Typed client for the real backend Product DNA API. Mirrors
 * `poc/backend/src/schemas/product/{requests,responses}.py` field-for-field.
 *
 * Real, disclosed limit (2026-09-23, frontend audit — this whole file didn't exist before): unlike
 * Brand DNA, the backend has NO `GET /api/v1/products` list route, only `POST /api/v1/products`
 * (onboard) and `GET /api/v1/products/{id}` (fetch one, by a known id) — see
 * `backend/src/api/v1/product/routes.py`. There is no way to ask the backend "what products has
 * this user onboarded" at all. `listOnboardedProducts` below works around that honestly: it keeps
 * the ids of products onboarded FROM THIS BROWSER in `localStorage` (the same real, working
 * pattern `components/canvas/CanvasEngine.tsx` already uses for tile positions) and re-fetches each
 * one by id. This is a real, working list for the browser that onboarded them — not a account-wide
 * list, since the backend genuinely has no way to produce one.
 */
import { API_BASE_URL, ApiError, request } from "./http";

export interface ProductAttributes {
  summary: string;
  price: number | null;
  discount_percent: number | null;
  // The product's real, physical color (2026-09-30) — a genuine product fact, distinct from any
  // brand color guideline. See backend/src/core/guardrails.py's _product_rules "color" handling.
  color: string;
  // The product's real currency symbol/code (2026-10-05, fidelity bug fix) — the UI used to
  // hardcode "$" on every price regardless of what currency the product actually uses. Empty
  // string when unclear (never guess a currency the source didn't actually state).
  currency: string;
  must_show: string[];
  never_show: string[];
  claims_allowed: string[];
  claims_disallowed: string[];
  label_visibility: string;
}

export interface ProductProfile {
  id: string;
  name: string;
  attributes: ProductAttributes;
  indexed: boolean;
  created_at: string;
}

const STORAGE_KEY = "ams_onboarded_product_ids";

function loadOnboardedIds(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function saveOnboardedIds(ids: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  } catch {
    // localStorage quota exceeded — silently ignore, same tradeoff CanvasEngine.tsx already makes.
  }
}

export async function onboardProduct(
  name: string,
  description: string,
  price?: number,
  discountPercent?: number,
  color?: string,
): Promise<ProductProfile> {
  const product = await request<ProductProfile>("/api/v1/products", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      description,
      price: price ?? null,
      discount_percent: discountPercent ?? null,
      color: color?.trim() || null,
    }),
  });
  saveOnboardedIds([...loadOnboardedIds().filter((id) => id !== product.id), product.id]);
  return product;
}

export async function getProduct(productId: string): Promise<ProductProfile> {
  return request<ProductProfile>(`/api/v1/products/${productId}`);
}

/** Edit/delete a Product DNA profile (2026-09-28) — patch semantics: only the fields actually
 * given are changed. Lets the user fix a wrongly-crawled or outdated product from the UI. */
export async function updateProduct(
  productId: string,
  patch: { name?: string; attributes_patch?: Record<string, unknown> },
): Promise<ProductProfile> {
  return request<ProductProfile>(`/api/v1/products/${productId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function deleteProduct(productId: string): Promise<void> {
  // `request()` always calls `.json()` on the response — a real 204 No Content has no body to
  // parse, so this calls fetch directly rather than reusing it (same reasoning as the raw
  // `fetch(...)` calls elsewhere in this file's DNASection.tsx caller for file uploads).
  const res = await fetch(`${API_BASE_URL}/api/v1/products/${productId}`, {
    method: "DELETE",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(res.statusText, res.status);
  }
  saveOnboardedIds(loadOnboardedIds().filter((id) => id !== productId));
}

/** See the file-level note above — a real, working, browser-scoped list, not an account-wide one.
 * An id whose product no longer resolves (deleted server-side, or from a different backend/DB
 * reset during local dev) is dropped rather than left to error the whole list. */
export async function listOnboardedProducts(): Promise<ProductProfile[]> {
  const ids = loadOnboardedIds();
  const results = await Promise.all(
    ids.map((id) =>
      getProduct(id).catch(() => null),
    ),
  );
  const products = results.filter((p): p is ProductProfile => p !== null);
  if (products.length !== ids.length) {
    saveOnboardedIds(products.map((p) => p.id));
  }
  return products;
}
