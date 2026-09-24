# Agentic Marketing Studio: Implementation Plan

## 1. What Exists (Current State)

Based on the requirements document, the combined architecture spec, and the current codebase state, the following components are already in place:

### Core Data Models
*   **`SessionModel`**: Tracks a user's creative-partner interaction (`user_id`, `brand_profile_id`, `product_profile_id`, `brief`, `status`, `approval_mode`).
*   **`BrandProfileModel`**: Stores brand facts and DNA (`user_id`, `name`, `raw_profile`, `indexed`).
*   **`ProductProfileModel`**: Stores product attributes and guardrails (`name`, `attributes`, `indexed`).
*   **`CanvasElementModel`**: Stores individual generated asset tiles (`session_id`, `element_type`, `storage_ref`, `compliance_status`, versioning).

### Services & Orchestration
*   **Storage**: Currently implemented via `LocalStorageBackend` storing assets to disk.
*   **Knowledge Backend**: LlamaIndex vector store handling indexing and querying of Brand and Product DNA (`BrandDnaService`, `product_dna_service`).
*   **Compliance Gate**: Runs brand consistency, visual fidelity, and format checks (`run_compliance_gate`).
*   **Graph/Specialists**: LangGraph orchestration with specialist agents (e.g., Creative Director, Copywriter, Scene Composer, Video Director). Includes self-correction critique loops and a human-in-the-loop canvas.
*   **Master Directive**: Error handling to prompt users rather than failing on ambiguous inputs.

---

## 2. What Has to Be Added (Missing Features)

The following requirements from the backend specification and the combined architecture are missing and must be added:

### Requirement 1: Compliance Status Writeback
*   Persist `run_compliance_gate` results ("passed", "failed", "error", or "disabled" when QA is off) to `CanvasElementModel.compliance_status`.

### Requirement 2: Per-User RAG Isolation
*   Isolate LlamaIndex collections by `user_id` (e.g., `brand_{user_id}`, `product_{user_id}`) so knowledge facts are private per account. Pass `user_id` through the graph state to specialists.

### Requirement 4: Product Subject Fidelity via Background Removal
*   **Model Update**: Add `photo_storage_ref` to `ProductProfileModel`.
*   **Pipeline**: Add a compositing pipeline in `Scene_Lead` using Replicate's RemBG for background removal and compositing the product PNG onto branded backgrounds.
*   **API**: Add `POST /api/v1/products/{product_id}/photo` endpoint.

### Requirement 7: Logo and Custom Font Compositing
*   **Model Update**: Add `logo_storage_ref` and `font_storage_refs` to `BrandProfileModel`.
*   **Tools**: Create `Logo_Compositor` and `Brand_Asset_Applier` specialist to overlay logos. Update `text_overlay` tool to use custom fonts.
*   **API**: Add endpoints for uploading brand logos and fonts.

### Requirement 8: Brush-Mask Region Editing
*   **API & Service**: Add `POST /api/v1/canvas/elements/{element_id}/masked-edit` to pass painted brush-mask PNGs to `replicate_provider.edit()`. Save results with incremented canvas element versions.

### Requirement 9 & 13: Campaign Style Lock & Aesthetic Consistency
*   **Model Update**: Add `style_ref_storage_ref` and `style_seed` to `SessionModel`.
*   **Pipeline**: Inject these as `reference_storage_ref` and `seed` into `base_image_generator`. Store pHash of style reference for client-side verification.
*   **API**: Add `PUT /api/v1/sessions/{session_id}/style`.

### Requirement 10 & 17: Video Camera Motion & Keyframe Anchoring
*   **Tools**: Add `camera_motion`, `first_frame_storage_ref`, and `last_frame_storage_ref` to `base_video_generator`.
*   **Pipeline**: Default `first_frame` to the product composite image to ensure video opens on the actual product.

### Requirement 12: Product Subject Fidelity via IP-Adapter (img2img)
*   **Pipeline**: Load `ProductProfileModel.photo_storage_ref` as an IP-Adapter/ControlNet reference seed into `base_image_generator` (distinct from style reference).

### Requirement 15: Ad Spec Presets and Platform-Compliant Export
*   **Configuration**: Add an `AD_SPECS` registry with standard formats (Meta, LinkedIn, Google Display, etc.).
*   **Tools**: Add `image_crop_resize` for final dimension matching.
*   **API**: Expose `GET /api/v1/ad-specs` and `POST /api/v1/canvas/elements/{element_id}/export-all-specs`.

### Requirement 16: AI-Enhanced Text Rendering (SVG)
*   **Tools**: Refactor `text_overlay` to support `"svg"` backend using `cairosvg` for drop-shadows and letter-spacing. Fall back to Pillow. Add `cairosvg` to dependencies.

### Combined Architecture: Multi-Product Workflow
*   Update orchestration to support binding **multiple Product Profiles** to a single campaign session. Scope guardrails and critique loops per-product.

---

## 3. Tasks to Be Done

### Phase 1: Type System & API Client (Frontend)
1. [x] Update `CanvasElement` interface (`compliance_status`, `ad_spec_name`, `safe_zone_pct`).
2. [x] Update `Session` interface (`style_ref_storage_ref`, `style_seed`, `style_phash`).
3. [x] Add API helpers for `/masked-edit`, `/export-all-specs`, logo/font/photo uploads, and session style lock.

### Phase 2: Core Platform & Foundation Fixes (Backend Phase 0)
4. [x] Fix Compliance Status Writeback (`session_service.py`, `canvas_repository.py` to persist `passed`, `failed`, `error`, `disabled`).
5. [x] Fix Per-User RAG Isolation (`GraphState` update, `brand_dna_service.py`, `product_dna_service.py`, `brand_kit_lookup.py`).

### Phase 3: Provider Extensions (Backend Phase 1)
6. [x] Extend `ReplicateImageProvider` generate (`style_reference_bytes`, `seed`, `width`, `height`) and edit (`mask_bytes`).
7. [x] Extend `VideoProvider` generate (`camera_motion`, `first_frame_bytes`, `last_frame_bytes`).

### Phase 4: New Tools & Specialists (Backend Phase 2 & 3)
10. [x] Implement `logo_compositor`, `image_crop_resize`.
11. [x] Extend `text_overlay` with `"svg"` backend using `cairosvg`.
12. [x] Extend `base_image_generator` to inject `style_reference_bytes` and `reference_image_bytes` (from product DNA) automatically.
13. [x] Create `brand_asset_applier` specialist (`registry.py`, `brand_asset_applier.md`).

### Phase 5: Pipeline Wiring & Prompt Updates (Backend Phase 4)
14. [x] Wire `Product_Composite` pipeline inside `visual_design_lead` using RemBG and Pillow.
15. [x] Update `camera_director.md` to require `camera_motion` and `first_frame_storage_ref`.
16. [x] Update `illustrator.md` to use SVG text and suppress text-based subject description when `product_photo_storage_ref` is present.

### Phase 6: Data Models & APIs (Backend Phase 5 & 6)
17. [x] Apply schema migrations for `CanvasElementModel`, `SessionModel`, `BrandProfileModel`, `ProductProfileModel`.
18. [x] Add API endpoints (`/products/{id}/photo`, `/brands/{id}/logo`, `/brands/{id}/fonts`, `/sessions/{id}/style`, `/canvas/elements/{id}/masked-edit`, `/ad-specs`, `/export-all-specs`).

### Phase 7: UI Component Builds (Frontend Phase 2-10)
20. [x] Compliance Badge on Canvas Tile.
21. [x] DNA Section Uploads (Brand Logo, Fonts, Product Photo).
22. [x] Style Lock Panel in Studio Header.
23. [x] Ad Spec Picker & Safe-Zone Overlay & Export Modal.
24. [x] HTML5 Canvas Mask Editor.
25. [x] Camera Motion & Keyframe UI for video generation.
26. [x] Multi-step Onboarding Flow for new sessions.

### Phase 8: Infrastructure & Compliance (Backend Phase 7 & 8)
27. [x] Update `compliance_gate.py` to check `text_area_max_pct` against Ad Specs using Pillow.
29. [x] Add dependencies (`cairosvg`, `imagehash`) to `pyproject.toml`.
