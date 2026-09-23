# Campaign Studio step contract

Status: **everything described here is built.** Track numbers (A1–A11) are kept as provenance —
they say which change introduced a behaviour, not that it is pending. Where something is *not*
implemented the text says so outright: the `qa_override` gate, the `product` guardrail source
before A11, and generator-side logo support are each called out where they would otherwise be
assumed.

The request and response shape for the six `/campaign-studio/api/agent/*` step endpoints.

This is the interface the canvas builds against. It was written before the implementation,
deliberately, so backend and canvas work could proceed in parallel — and it has been corrected
against the code since, wherever the two disagreed.

---

## The one rule everything follows

**The server stores nothing between calls.** There is no run, no session, no campaign record.
Every step endpoint receives one self-contained request and returns one self-contained response.

Anything the caller will need later — brand details, the guardrail set, approved outputs,
approved images — must be held by the caller and sent again on the next call. A caller that
drops something has lost it.

Two consequences worth stating plainly:

- A person can take a week at a gate. Nothing is holding a connection open and nothing expires.
- The guardrail set **must** be round-tripped. Its derived rules would regenerate identically,
  but its inferred rules and human amendments exist nowhere else and are simply lost. See
  [Guardrail set](#guardrail-set).

---

## Request envelope

Every step accepts this envelope. Individual steps require different subsets; nothing here is
required by all of them.

```jsonc
{
  // ── context ─────────────────────────────────────────────────────────────
  "brand_details": {                     // A4
    "voice_and_tone":          "string|null",
    "visual_identity":         "string|null",
    "positioning_and_pillars": "string|null",
    "audience_segments":       "string|null",
    "category_context":        "string|null",
    "logo":       { "url": "string|null", "base64": "string|null",
                    "mime_type": "image/png" },        // A8 — see Logo verification
    "logo_rules": "string|null"          // min size, clear space, recolouring
  },
  "project_details": {                   // A4
    "goal":                 "string|null",
    "audience":             "string|null",
    "campaign_description": "string|null"
  },
  "product_details": {                   // A13 — what is being sold; see Product details
    "name": "string|null", "category": "string|null", "description": "string|null",
    "attributes": { "any key": "any value" },      // true of it: Fit, Range, Term, Warranty…
    "options":    { "axis": ["the only values that exist"] },   // Size, Plan, Trim, Capacity…
    "currency": "₹", "price": 749, "compare_at_price": 1799,
    "discount_percent": null             // checked against the prices, not trusted
  },
  "brand_kit": {},                       // LIVE — legacy, still accepted, see Compatibility
  "guardrail_set": {},                   // LIVE — echo back verbatim; see Guardrail set

  // ── product input ───────────────────────────────────────────────────────
  "product_image_urls":    ["string"],   // LIVE
  "product_images_base64": [{ "mime_type": "image/jpeg", "data": "string" }],  // LIVE

  // ── carried forward from earlier steps ──────────────────────────────────
  "idea":       "string",                // LIVE
  "concept":    {},                      // LIVE on steps after concept
  "storyboard": [],                      // LIVE on steps after storyboard
  "prior_assets": [                      // LIVE on scene-images — see Reusing approved images
    { "scene_id": "string", "image_base64": "string|null", "image_url": "string|null",
      "mime_type": "image/jpeg" }
  ],

  // ── control ─────────────────────────────────────────────────────────────
  "hitl_gates": ["concept", "storyboard", "direction",
                 "pre_image", "copy", "pre_video", "qa_override",
                 "pre_image:scene_001"],                             // A5 — see Gate scoping
  "approvals": [                                                     // A5
    { "gate": "concept", "subject_id": "string|null",
      "decision": "approve|approve_with_edits|reject_and_retry|abort",
      "payload": {}, "feedback": "string|null" }
  ],
  "escalation_decisions": [                                          // A11 — see Answering one
    { "target": "guardian", "subject": "string|null",
      "decision": "regenerate|accept_anyway|amend_rule",
      "reason": "string", "actor": "string|null",
      "rule": { "id": "string", "rule": "string" } }
  ],
  "asset_count":  3,                     // LIVE
  "aspect_ratio": "1:1",                 // LIVE
  "audience_segment": "string|null",     // A13 — selects segment-scoped rules
  "asset_type":      "string|null",      // A13 — selects asset-type-scoped rules

  // ── checks, all default true ────────────────────────────────────────────
  "run_qa":         true,                // LIVE — scene-images only
  "run_guardian":   true,                // A6 — no-ops when no brand is supplied
  "infer_guardrails": true,              // A7 — infer extra rules on the first call
  "lock_direction": true                 // scene-images only; see the direction gate
}
```

`third_failure` is **not** listed in `hitl_gates` — it is always on and cannot be disabled.

### Which gates Campaign Studio enforces today

| Gate | Fires today | Where |
|---|---|---|
| `concept`, `storyboard`, `copy` | yes | after the step's work, in `agent_steps.py` |
| `pre_image` | yes | inside `generate_scene_image`, ahead of the provider call |
| `pre_video` | yes | inside `VideoRunContext`, ahead of the render |
| `direction` | yes, from the second scene on | inside `generate_scene_image`; see below |
| `qa_override` | no — a third failure escalates instead | see [`escalation`](#escalation) |

Naming a gate that does not fire is accepted and does nothing, so a workflow that grows a node
does not have to invent a name other callers already use.

**When `direction` fires.** The Art Director reads a direction *off* an already-generated
image, so it cannot precede the first one — only scenes 2..N. `/api/agent/scene-images`
generates the first scene, locks a direction from it, then gates before spending on the rest.
One approval covers every remaining scene.

`lock_direction` (default `true`) controls that extra agent turn. It is skipped for a
single-scene storyboard, where there is nothing to be consistent with. Setting it `false`
generates each scene in isolation and makes the `direction` gate unreachable again — that was
this endpoint's behaviour before, and it is why scene 4 had nothing holding it to scene 1.

The gate's payload names `read_from_scene_id`, so the human can look at the image the direction
came from rather than judging the words alone.

**`pre_video` scoping.** A call can now cut several scenes, so `pre_video:<scene_id>` gates one
cut and the bare `pre_video` gates every cut in the film.

### Film length

**Live as of A14.** A provider clip is at most 8 seconds, so `/api/agent/video` used to produce
an 8-second film and nothing longer was expressible — despite `finalize_video` having always
known how to stitch clips together. Length now comes from **how many cuts you make**:

| Field | Meaning |
|---|---|
| `scene_ids` | the scenes to cut, in playback order. One clip each, stitched |
| `selected_scene_id` | still works, and means a one-cut film |
| `target_seconds` | roughly how long it should run — guidance, not a contract |

Each cut picks its own `duration_seconds` from what the provider supports (4/6/8), because a
film of identical-length cuts reads as a slideshow. A 15-second reel is three cuts; a 30-second
hero is four or five.

`target_seconds` is guidance because the storyboard sets the ceiling: five scenes cannot exceed
40 seconds. Asking for more is **reported, not silently truncated** — the Director is told the
target is unreachable, makes the longest film it can, and says so in the summary.

Stitching needs `ffmpeg` on the host. It is in the Docker image; a dev machine without it gets a
clear error naming the fix rather than a failed render.

### Gate scoping

An entry is either a stage or a stage scoped to one subject:

| Entry | Fires on |
|---|---|
| `pre_image` | every image node in the workflow |
| `pre_image:scene_001` | that scene only |

A workflow graph can hold several nodes of the same stage — a lookbook with four angles, a
video chain with three cuts — and a plain stage entry gates all of them. That is the right
default for a careful campaign and the wrong one for a routine variant, so an entry may carry
`:subject_id` to gate a single node instead.

The two forms are the same mechanism at different granularities, which is deliberate: a
separate override map would be a second source of truth that can silently contradict the first.
If both `pre_image` and `pre_image:scene_001` appear, the broad entry already covers that scene
and the scoped one is redundant rather than conflicting.

`subject_id` is the same identifier the gate and any resulting decision carry — `scene_001` for
image and QA gates, absent for whole-stage gates like `concept` that can only happen once.

---

## Response envelope

```jsonc
{
  "ok": true,
  "step": "concept",                     // which endpoint produced this

  "output": {},                          // the stage's product: concept, storyboard, assets, copy…

  "guardrail_set": {},                   // LIVE — always present; echoed or freshly derived

  "verdicts": {                          // A6 — both keys always present, either may be null
    "qa":       { "passed": true, "score": 0, "productFidelity": "pass",
                  "briefConsistency": "pass", "feedback": "string" },
    "guardian": { "target": "concept", "subject_id": null, "passed": true, "feedback": "string",
                  "violations": [{ "rule_id": "brand.voice_and_tone", "field": "string",
                                   "expected": "string", "observed": "string" }] },
    "logo":     { "target": "logo", "subject_id": "scene_001", "passed": true,
                  "feedback": "string", "violations": [] }   // A8 — null unless a logo was supplied
  },

  "gate": null,                          // A5 — non-null means stop and ask a human
  "escalation": null,                    // A1 — non-null means a check gave up
  "overrides": [                         // A11 — checks a human overruled. NOT passes
    { "target": "guardian", "subject": "scene_001", "reason": "string",
      "actor": "string|null", "verdict": {}, "attempts": 3 }
  ],

  "trace": []                            // A3a — see Trace
}
```

### `verdicts`

Two checks, never merged. The Critic judges whether the work is *good* — product fidelity,
brief consistency, visual defects. The Guardian judges whether it matches the *brand*. They fail
for different reasons and are fixed differently, and one `passed` would hide the case most
likely to ship by accident: work that is beautiful and off-brand.

Both keys are always present; either may be `null` when that check did not run.

**Live as of A6.** The Guardian runs on `concept`, `storyboard`, `copy` and each scene image.
`run_guardian` (default `true`) turns it off per call, and it is **skipped automatically when no
brand is supplied** — with no brand there are no rules, so any verdict could only come from rules
the model invented.

| Target | Judged on |
|---|---|
| `concept`, `storyboard`, `copy` | the step's own output, before it returns |
| `image` | each scene, inside the generation loop |
| `direction`, `video` | vocabulary only — nothing points the Guardian at them yet |

**A failing verdict is not advisory.** The work is sent back to the agent that produced it with
the violations attached, up to `MAX_QA_RETRIES` times. On an image this shares the QA retry
budget — both checks run before each decision, so one attempt fixes both faults rather than
spending the budget twice. Work the Guardian never accepts raises an
[`escalation`](#escalation) with `target: "guardian"`, which blocks finalize exactly as a QA
escalation does.

Every violation names the brand field, what the brand asks for, and what the work does.
`"off-brand colours"` is not actionable; `"visual_identity — expected #FFD232, observed a muted
sage green"` tells the next attempt what to change and lets a human judge whether the Guardian
is right.

Where the campaign idea and the brand conflict, **the brand wins** and the agent is required to
note the conflict in its output. An agent must not resolve that privately, and work whose own
text argues the brand does not apply is itself a violation.

### Logo verification

**Live as of A8.** A third verdict, `verdicts.logo`, on the same shape as `guardian`. It runs
per scene image, and **only when `brand_details.logo` carries an actual image** — there is
nothing to compare a render against otherwise, and a second vision call per scene is not free.

The mark is attached to the check alongside the render. That is the whole point: comparing a
render to the *words* "a logo exists" is exactly how a hallucinated wordmark passes, and image
models invent plausible marks routinely. A logo supplied as a `url` is fetched at the edge
before the check runs; if that fetch fails the check is skipped rather than run blind.

Four outcomes:

| Outcome | Verdict |
|---|---|
| No mark on the asset | **passes**, unless the rules require the logo to appear. Rules about clear space, placement or background describe how a logo must look *when present* — they do not demand one |
| A mark that is not the supplied one | fails — invented lettering, distorted or mirrored, wrong symbol, misspelled brand |
| The right mark, breaking a stated rule | fails, naming the rule and where on the asset |
| The right mark, placed legally | passes |

A failure feeds the same retry budget as QA and the brand check, and an uncleared one escalates
with `target: "logo"` — separate from `target: "guardian"`, because misusing a trademark is a
different problem with a different fix, and both can be open on one scene at once.

There is no compositing. Nothing draws the logo onto an asset, and no deterministic image
analysis runs; the check is a vision judgement against the supplied mark. **The logo is not
sent to the image generator either** — `ImageGenerationRequest` carries one reference image and
that slot holds the product photo. A brand whose rules *require* the logo to appear will
therefore fail this check every time and escalate; that is reported honestly rather than
hidden, and the fix is to amend the rule or to add generator-side logo support.

### `gate`

Non-null when a configured gate fired. The step's work up to that point is done; nothing
after it has run.

```jsonc
{
  "gate": "pre_image",
  "subject_id": "scene_001",             // null for whole-stage gates like concept
  "payload": {},                         // exactly what the human is approving
  "options": ["approve", "approve_with_edits", "reject_and_retry", "abort"]
}
```

The caller renders it and collects a decision. Decisions are idempotent on
`(gate, subject_id)` — replaying a request cannot approve twice.

`approve_with_edits` replaces the agent's output with the human's `payload`. That payload is
**re-judged by the guardian before anything downstream consumes it**, because it is the only
path in the system that puts arbitrary text where checked agent output normally goes.

#### What the caller does next

Where the gate sits decides this, and getting it wrong silently discards the human's review.

**Stage gates** (`concept`, `storyboard`, `direction`, `copy`) fire *after* the step's work.
The caller already holds the output.

| Decision | Caller does |
|---|---|
| `approve` | moves to the next step with the result in hand — **does not call this step again** |
| `approve_with_edits` | applies `payload` over that result, then moves on |
| `reject_and_retry` | calls this step again with `feedback` in `instructions`, and **without** the decision in `approvals`, so the new output is gated in its turn |
| `abort` | stops |

Re-calling the step on `approve` would re-run the agent and return *different* output, which
then moves forward having never been reviewed. The human approved what they read, not the
step.

**Handler gates** (`pre_image`, and `direction` once reachable) fire *inside* the step, before a
provider call.
The step returned early and its work is unfinished, so every non-`abort` decision goes into
`approvals` and the same step is called again. `reject_and_retry` reaches the agent as the
human's reason and it writes a new prompt, which meets the gate again — so a rejection costs
another round trip rather than a generation.

#### Echo the payload when you approve

Resuming re-runs the agents, and they are free to draft something other than what the human
read. Send the approved `payload` back on `approve`, not just on `approve_with_edits`:

```jsonc
{ "gate": "pre_image", "subject_id": "scene_001", "decision": "approve",
  "payload": { "prompt": "…the exact prompt that was on screen…" } }
```

The backend prefers that payload over the new draft, so the spend matches the review. Approving
with an empty payload still works and still unblocks — it just approves the stage rather than a
specific text, and the re-run's own wording is what gets generated.

### `escalation`

Non-null when a check rejected the same subject past its retry budget — currently
`MAX_QA_RETRIES` (2), so three failures total.

```jsonc
{
  "subject": "scene_001",                // what was judged — a scene, or a stage like "concept"
  "sceneId": "scene_001",                // null when the subject is not a scene
  "target": "qa",                        // which check gave up: "qa", "guardian" or "logo"
  "attempts": 3,
  "verdict": {},
  "feedback": "string",
  "options": ["regenerate", "accept_anyway", "amend_rule"]
}
```

Escalations are keyed on `(target, subject)`, so QA and the Guardian can both be open on the
same scene — they are different problems with different fixes, and one must not replace the
other.

### Answering one

**Live as of A11.** `options` listed three answers and nothing accepted any of them: a person
was shown three choices and could act on none, so the only way past a failing check was to stop
sending the thing that failed. `escalation_decisions` closes that, keyed on `(target, subject)`
like gate approvals — a decision with no `subject` answers every subject of that target.

```jsonc
{ "target": "guardian",                  // which check gave up
  "subject": "scene_001",                // or null for all of that target's
  "decision": "regenerate|accept_anyway|amend_rule",
  "reason": "string",                    // REQUIRED on accept_anyway
  "actor": "string|null",
  "rule": { "id": "brand.voice_and_tone", "rule": "…" }   // required on amend_rule
}
```

| Decision | What changes |
|---|---|
| `regenerate` | nothing. Requests are independent and retry budgets start fresh, so trying again *is* re-posting. It exists so a caller can record that a person chose it |
| `accept_anyway` | the check is overruled for that subject. Finalize stops refusing, and the override is returned in `overrides` |
| `amend_rule` | the guardrail is replaced and the check re-runs against the new wording. The amendment lands in `guardrail_set`, so it survives the round trip |

`reason` is required on `accept_anyway` and the decision is **dropped without it** — an override
with no stated reason is the one audit entry nobody can act on later.

`overrides` is returned alongside `verdicts` and is deliberately not part of it: **an overridden
failure is not a pass.** Work that shipped over a failed check should be findable as such later,
not identical in the response to work that passed.

An amendment is re-tagged `source: "human"` and keeps its position in the set, so a caller's
rendered list does not reorder and re-deriving cannot quietly restore the original wording. A
human may also add a rule the derivation never produced.

This is different from a gate in one important way: **it blocks finalize.** A campaign cannot
complete while any escalation is open. Render it distinctly from routine gates — a third
failure is not a checkpoint, it is a stop.

> **Live as of A1.** `escalation_raised` is emitted, and `finalize_campaign` refuses while any
> escalation is open. This replaced the previous behaviour, where exhausting retries returned
> *"accept this asset and continue"* and the rejected asset reached the finished campaign.

---

## Component registry

**Live as of A10**, in `agent_core/components.py` and exposed at
`GET /flow-builder/api/components`. It is the list of node types a workflow graph may contain,
and it is data rather than prose in a prompt — a graph editor and the builder read the same
list instead of keeping copies that drift.

Each entry carries an `availability`, because "this node type exists" and "something can run it"
are different claims and the old hand-written list conflated them:

| State | Meaning |
|---|---|
| `available` | this backend runs it; `endpoint` names where |
| `external` | a real node, run by the studio front end rather than here — text boxes, uploads, export steps |
| `planned` | named and specified; nothing runs it. **A graph may not contain one** |

`planned` is the point of the registry. `text_preserve` sat in the vocabulary with detailed
wiring rules and no executor anywhere in this backend, so a graph containing it built cleanly
and then could not run, with nothing pointing at the cause. The deferred capabilities —
`upscale`, `tts`, `video_extend`, `video_combine`, `router`, `scene_composer` — are registered
the same way. Several of those have a live service endpoint and simply no node wired to it;
their `note` says so, so nobody re-discovers the endpoint and assumes the node works.

`POST /flow-builder/api/validate` checks a graph and returns **every** problem, not the first —
an editor fixing a graph wants the whole list. The builder runs the same check on its own
output and retries once with the specific offending nodes fed back; a graph it still cannot fix
comes back with a `problems` array rather than looking valid and failing at execution.

---

## Reusing approved images

**Live as of A9**, on `/api/agent/scene-images`. `prior_assets` names scenes the caller already
holds. Each one is passed through untouched: **not generated, and not re-checked.**

The cost saving is the smaller half. Regenerating a scene produces a *different* image, so a
caller resuming after an approval would silently replace the asset a human signed off with one
nobody has seen. That is the same failure the gates fixed for prompts, one level up.

Re-checking is skipped for the same reason. QA and the Guardian are model calls: running them
again on approved work costs a call apiece and can come back differently, which would escalate
an asset that was already accepted.

A reused asset is returned with `"reused": true`, its own bytes, and `qa`, `guardian` and `logo`
all `null` — it was not judged this call, and reporting a verdict it did not earn would be a
lie. The caller already holds the verdicts from the call that produced it.

Reuse composes with the rest:

- A reused scene can still be the reference the Art Director reads a direction from — it is the
  better reference, being the image a human actually approved.
- When every scene is supplied, nothing is generated, no Art Director runs, and the step returns
  a complete response rather than failing with "produced no images".

## Payload budget

Statelessness means every call resends every image in scope. That is the design working, and it
is also how a request grows until the worker dies on memory or the load balancer cuts it off,
leaving the caller a closed socket and no reason.

Two limits, both `413`:

| Limit | Default | Why |
|---|---|---|
| Per image | 10 MB decoded | matches what `creative_studio` already enforces, so one bad upload fails the same way everywhere |
| Per request | 32 MB decoded, across product images, prior assets and the logo | eight individually legal images still add up |

Both are measured after resolution, so a caller that sent URLs is judged on what was actually
fetched. The error names the offending group and index and says what to drop — an oversized
request is a caller mistake, and a 413 that does not say which image is unactionable.

---

## Guardrail set

Brand details are prose. *"Warm, plain-spoken, never salesy"* is a sentence, not a check: two
agents can both believe they satisfied it, and a human who disagrees has nothing to point at.
The guardrail set turns that prose into discrete rules with identities, so a verdict can name
the rule it breached and a person can change one rule without rewriting the brand.

Built on the first call of a workflow. Returned in every response. **The caller resends it
verbatim on every subsequent call, including into a second workflow.**

```jsonc
{
  "version": 1,
  "rules": [
    { "id": "brand.voice_and_tone",      // stable, says where it came from
      "scope": "campaign|segment|asset_type",
      "applies_to": "string|null",       // the segment or asset type, when scope is narrower
      "rule": "string",                  // one testable requirement, stated as an imperative
      "source": "brand|project|product|inferred|human" }
  ]
}
```

**Live as of A7.** `infer_guardrails` (default `true`) controls the inference pass; like the
Guardian, the whole mechanism no-ops when no brand is supplied.

### How a set is built

| Source | Where it comes from |
|---|---|
| `brand`, `project` | one rule per populated field, derived deterministically |
| `product` | **A11** a vision pass over the photographs (what the thing looks like) and **A13** the catalogue facts (what is true of it) |
| `inferred` | a model pass adding what the fields imply but do not state — synonyms of banned words, an unstated consequence of a palette |
| `human` | a rule someone amended or added, via [`amend_rule`](#answering-one) |

**Product rules** are the only ones that need no brand. Everything downstream regenerates the
product from a text prompt, and the usual failure is drift — the glaze goes glossy, a handle
changes shape, a logo appears that was never on the object. A rule makes that testable instead
of a matter of opinion, and the most valuable one it writes is usually about absence: *"the
product must carry no text, logo or surface pattern"*, because invented text is what generators
add. The pass is skipped when there are no product photographs.

Inference runs **only when the caller supplied no set**. Re-running it on a resend would add
rules to a set a human may already have reviewed, and they would have no way to tell which
rules they had approved.

### Product details

**Live as of A13.** The facts a caption gets wrong. A photograph shows a caramel-orange tee; no
amount of looking at it yields ₹749, ₹1,799 or S–3XL, so until those arrive as data nothing can
contradict an invented one.

Deliberately domain-neutral. An earlier draft had `fit`, `sizes` and `material` as named fields
— a clothing catalogue wearing a costume. A subscription has plans, a car has a range, a service
has a term. Two open maps carry all of it, and they are separate because the rule differs:

| | Means | Becomes |
|---|---|---|
| `attributes` | things that are true | one `product.attr.<name>` rule each — *do not contradict this* |
| `options` | the complete list of values on an axis | one `product.option.<axis>` rule each — *nothing outside this list exists* |

One rule per attribute rather than one listing them all, because a verdict names the rule it
breached and `product.attributes` would tell a human that something among eight facts was wrong
without saying which.

**The discount arithmetic is done at derivation, not at review.** `price` and
`compare_at_price` produce the exact figure once, and the rule carries the finished line —
`"₹749, reduced from ₹1799, 58% OFF"`. Asking a language model to verify 1050/1799 while
reviewing an asset is asking it to do the thing it is worst at, on the number that matters most;
computed here, the check downstream is a string comparison. This is not the arithmetic *tool*
that was ruled out — nothing calls it at runtime.

The stated percentage is **floored**: 58.37% prints as 58%, never 59%. Overstating a saving is
the direction that causes real harm. A supplied `discount_percent` that the prices do not
support is not silently overridden — the computed figure wins and a `product.pricing_conflict`
rule appears so a human sees the input is wrong.

**Not every role sees the price.** Marketing, the Creative Director, the Guardian and the Critic
do. The Art Director and Storyboard Designer see what the product *is* but not what it costs: a
scene designer who knows the price has everything needed to render a number onto an image that
nobody asked for.

### Scoped rules

**Live as of A13.** `scope` and `applies_to` were vocabulary the model carried and nothing
honoured. A rule now applies only to what a call is making:

- `scope: "campaign"` — always applies
- `scope: "segment"`, `applies_to: "professionals"` — only when the request names that
  `audience_segment`
- `scope: "asset_type"`, `applies_to: "teaser"` — only when the request names that `asset_type`

That is what makes *"price and discount only on the hero and the cutdown, not the teaser"* and
*"segment 2 is more restrained, still Bewakoof"* expressible as rules rather than as prose in a
prompt.

Scoping is a **view, never a deletion.** The response returns the full set and the caller
round-trips all of it; narrowing the stored set would delete every other segment's rules the
first time one segment ran. A scoped rule with no `applies_to` targets nothing and is treated as
campaign-wide, because a rule that can never match is a rule that was silently switched off.

### Ids are stable, not positional

A rule's id comes from where it came from — `brand.voice_and_tone`, not `r1`. Positional ids
look tidier and are a trap: add one brand field and every later id shifts, so a human's
amendment silently reattaches to a different rule. A duplicate id is dropped on read for the
same reason, since it would make an amendment ambiguous.

### Why resending matters

Derivation is deterministic, so the derived half regenerates identically and losing it is
survivable. What does not survive is everything else: **inferred rules and human amendments
exist nowhere but the set you were handed.** Drop it and the next call quietly judges the
campaign against a different standard than the one someone approved — and nothing in the
output says so.

### Verdicts point at rules

A Guardian violation carries `rule_id` naming the guardrail breached, copied from the list. An
id that is not in the set is rejected rather than recorded: a violation against a rule nobody
can find is a violation nobody can fix, and `amend_rule` has nothing to amend. `rule_id` may be
omitted, which is what happens when no set was supplied.

---

## Trace

An ordered array of the events a step produced, using the `AgentEvent` shape from
`agent_core/events.py`:

```jsonc
{ "type": "string", "agent": "string", "message": "string", "data": {} }
```

**Live as of A3b** on the step endpoints, in both presentations:

- `POST /api/agent/{step}` — normal JSON, with the step's events collected into `trace`.
- `POST /api/agent/{step}?stream=1` — `text/event-stream`. The same events arrive as they
  happen, and the result is the final event: `{"type": "step_complete", "data": { …the JSON
  response… }}`.

Both come from one collection mechanism, so they never disagree. Pick per call: a caller
watching a person work wants the stream; a caller that only needs the answer takes the JSON.

Streaming is genuinely incremental, not buffered — measured on a concept step, the first event
arrives at 10 ms and the rest at each turn boundary over the following 12 seconds. Without it a
caller waits the full duration with no signal at all.

| Event | Carries |
|---|---|
| `agent_started` | `parent`, `maxTurns` |
| `turn_started` | `turn`, `maxTurns`, `parent` |
| `agent_message` | The model's text, or `Calling tool \`x\`` with `tool`, `argsPreview`, `toolUseId` |
| `tool_result` | `tool`, `toolUseId`, `ok: true`, `terminal`, `durationMs`, `resultPreview` |
| `tool_failed` | `tool`, `toolUseId`, `ok: false`, `error`, `errorType`, `durationMs` |
| `escalation_raised` | `subject`, `sceneId`, `target`, `attempts`, `verdict`, `options` — *A1, A6* |
| `awaiting_human` | `escalations` — *A1* |
| `awaiting_approval` | the [`gate`](#gate) block — *A5* |
| `guardian_verdict` | `target`, `subject_id`, `passed`, `feedback`, `violations` — *A6* |

Stage events unchanged: `asset_generated`, `qa_verdict`, `storyboard_ready`, `copy_ready`,
`direction_ready`, `campaign_complete`, `error`.

**Building the tree.** `data.parent` is the agent that delegated to this one, or `null` at the
root. It is set by the runtime, so it appears on runtime events — `agent_started`,
`turn_started`, tool calls and results. Events emitted by tool handlers themselves
(`storyboard_ready`, `asset_generated`, provider `error`s) carry `agent` but not `parent`;
attribute those by agent name to the node `agent_started` established.

**Correlation.** A tool call and its outcome share a `toolUseId`, so a viewer can pair them and
show a duration. One call always produces exactly one `tool_result` or `tool_failed`.

**`ok` means the tool succeeded, not merely that it did not raise.** A handler returning
`{"error": ...}` — how validation rejections come back, after which the model retries — is
reported as `tool_failed`. This matters in practice: a storyboard submission being rejected and
retried looks identical to a clean run unless the trace distinguishes them.

**Size.** Arguments go through `_preview_args` and results through `_preview_result`, which
strips image payloads structurally and caps the whole preview at 2000 characters. A
`view_product_images` result carrying four base64 images summarises to about 100 characters.

**A working consumer.** `/campaign-trace` renders all of the above — the delegation tree, tool
calls paired with their results and durations, agent reasoning, escalations, and the artifacts
each stage produces. It drives the step endpoints in sequence, carrying `concept` into
`storyboard` and `storyboard` into `scene-images` exactly as this contract describes, so it is
also the reference for how a caller is expected to chain them. Source:
`backend/static/campaign-trace-ui.html`.

---

## Compatibility

| Field | Rule |
|---|---|
| `brand_kit` | Still accepted. When `brand_details` is also present, `brand_details` wins. |
| `imageBase64` | Stays. No asset store, no keys — images travel as base64 as they do today. |
| Response additions | Additive only. `gate`, `escalation`, `guardrail_set`, `trace` are absent or null until their change lands, never a different shape. |

**Request body size.** `nginx.campaign.sample.conf` now sets `client_max_body_size 40m`, above
the app's 32 MB decoded budget because base64 inflates by about a third. nginx has to allow more
than the app does, or it rejects first with a bare 413 and the caller never sees the message
naming what to drop.

⚠️ **The sample is a sample.** The deployed nginx config is not in this repo, and its default is
1 MB — enough to reject a single product photo. Check the deployed value rather than assuming it
matches this file.

---

## Open for sign-off

1. **Identity — still open, but narrowed.** `accept_anyway` now requires a `reason` and accepts
   an optional `actor`, and the override is returned in `overrides`. That is an audit trail of
   *what* and *why*.

   It is not authentication. `actor` is a caller-supplied string this backend does not verify
   and could not — the step endpoints take no credential. Anyone who can call the API can
   override a brand check under any name. Whether that is acceptable depends on who can reach
   the API, which is a deployment question this contract cannot answer.

   The decision still needed: should `accept_anyway` require a verified identity, and if so
   does the credential arrive as a header these endpoints validate, or is the caller (the
   studio backend) trusted to have authenticated the person already?

**Resolved**

- *Streaming.* Both presentations exist — `?stream=1` for SSE, `trace` in the JSON response
  otherwise — so the choice is the caller's per request rather than a platform decision.
- *Gate scoping.* One list, entries optionally scoped as `stage:subject_id`. See
  [Gate scoping](#gate-scoping).
