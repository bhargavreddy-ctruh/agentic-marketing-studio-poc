# DNA Input Structure

The Brand and Product DNA input fields have been upgraded to capture highly granular details for downstream Agent usage.

## Data Structure

The `Session.brief` now captures the following structured fields to drive LLM synthesis:

```json
{
  "campaignDetails": {
    "campaignIdea": "Enter campaign concept or tagline here",
    "audience": "Target demographic, psychographics, or user persona details",
    "goal": "Primary objectives (e.g., brand awareness, lead generation, sales target)"
  },
  "brandDetails": {
    "voiceAndTone": "Brand personality traits (e.g., professional, playful, empathetic)",
    "visualIdentity": "Color palette, typography, and design aesthetics",
    "logoRules": "Usage guidelines, clear space requirements, and sizing restrictions",
    "logoImage": "https://example.com/assets/brand-logo.png"
  },
  "productDetails": {
    "name": "Product Name",
    "category": "Product Category",
    "productPhotos": [
      "https://example.com/assets/product-photo-1.jpg"
    ],
    "productDescription": "Detailed overview of the product features, benefits, and specifications"
  }
}
```

These inputs are synchronized via `PUT /api/v1/sessions/{session_id}/dna` and are passed to the `GuardrailService` to enforce brand compliance and constraints dynamically across all specialists.


## Rule Granularity (2026-09-25)
- The LLM extraction process within `GuardrailService` now generates an array of distinct, atomic rules instead of a single, monolithic rule, ensuring that Brand colors, tone, and campaign goals are tracked independently by agents.

