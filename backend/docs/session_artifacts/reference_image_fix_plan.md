# Implementation Plan: Reference Image Ignoring Bug

## 1. Root Cause Analysis
When you provided the mobile phone image and asked for a winter campaign, here is exactly what went wrong in the pipeline:
1. **Orchestrator Routing:** The Orchestrator correctly saw your uploaded image as a "referenced element" and decided to create a dynamic plan starting with the `illustrator` specialist.
2. **Illustrator Confusion:** The `illustrator` specialist received the context that an image was available. However, its prompt contains a rule (`Rule 3: Product Subject Suppression`) that tells it to generate *only* a background if it detects a product photo, assuming a `composition_artist` will composite the product on top later. 
3. **Tool Call Failure:** Because the `illustrator` LLM assumed the image would be "composited later," it deliberately **chose NOT to pass the image's `storage_ref`** to the `base_image_generator` tool. 
4. **Result:** The `base_image_generator` tool only received your text prompt (describing a winter background). Since the `reference_storage_ref` was `None`, the Replicate provider generated a from-scratch image instead of performing an image-to-image generation using your phone ad.

## 2. Proposed Fixes

### Fix A: Update `illustrator.md` Prompt
We need to remove the ambiguity between Rule 3 (suppression) and Rule 3b (image-to-image reference). 
- **Change:** We will rewrite the prompt to explicitly state that if a referenced element is present, the illustrator **MUST ALWAYS** pass its `storage_ref` as the `reference_storage_ref` argument to `base_image_generator` for image-to-image generation, *unless* the orchestrator's specific instruction explicitly tells it to generate a standalone background.

### Fix B: Update Orchestrator Prompt (`orchestrator.py`)
- **Change:** When the user uploads an image for a new campaign, the orchestrator must give the `illustrator` an explicit instruction in the dynamic plan to use it as a reference. For example: `"instruction": "Generate the new campaign image. You MUST use the provided referenced element as an image-to-image reference."`

### Fix C: Ensure Context is Clear
- **Change:** In `graph.py` where the context is built for the dynamic executor, we will explicitly name the reference parameter so the LLM knows exactly what argument to use:
  `Element 1 (storage_ref: xyz, type: image) -> Pass this as 'reference_storage_ref' if using it as a visual base.`

## Next Steps
If you approve this plan, I will immediately update the prompt files (`illustrator.md` and `orchestrator.py`) to enforce this behavior so that your reference images are properly sent to the Replicate image generator.
