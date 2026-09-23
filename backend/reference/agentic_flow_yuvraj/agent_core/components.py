"""
components.py — What a workflow graph is allowed to contain, and what can run it.

The node vocabulary used to live as prose inside the Flow Builder's prompt, which
made two things impossible. Nothing could enumerate it, so a UI offering nodes had
to hardcode its own copy and drift from this one. And nothing could check it: the
prompt says "never invent node types outside the allowed list", which is a hope,
not a constraint — an invented type came back as a valid-looking graph and failed
later with nothing pointing at the cause.

So the vocabulary is data, and the prompt is generated from it.

The part that matters most is `availability`, because "this node type exists" and
"something can run it" are different claims and the list had been conflating them.
`text_preserve` sits in the vocabulary today with detailed wiring rules and no
executor anywhere in this backend. A graph containing it builds cleanly and then
cannot run. Saying so in the registry is the difference between a caller learning
that at build time and learning it in production.

Three states, because a boolean cannot say the useful thing:

* `available` — this backend runs it, and `endpoint` says where.
* `external`  — a real node, run by someone else. Text boxes, uploads and export
                steps belong to the studio front end. Marking these unavailable
                would be wrong; marking them available would promise execution
                this backend does not provide.
* `planned`   — named, specified, and nothing runs it. A graph may not contain
                one. Some have a service endpoint already and simply no node
                wiring; `note` says which.
"""
from __future__ import annotations

from typing import Iterable, Optional

from pydantic import BaseModel, Field

AVAILABLE = "available"
EXTERNAL = "external"
PLANNED = "planned"

ALL_AVAILABILITY = (AVAILABLE, EXTERNAL, PLANNED)

# Broad grouping, for a UI that wants to show these in sections.
CAT_INPUT = "input"
CAT_GENERATE = "generate"
CAT_AGENT = "agent"
CAT_CHECK = "check"
CAT_PUBLISH = "publish"


class Component(BaseModel):
    """One node type a workflow graph may contain."""

    id: str
    label: str
    category: str
    availability: str = PLANNED
    endpoint: Optional[str] = None
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    # Params the executor actually reads, name -> what it is for. Anything else a
    # builder invents is dropped silently, which is how a graph came back with
    # `duration: "8s"` on a node whose length is set by `targetSeconds`.
    params: dict[str, str] = Field(default_factory=dict)
    # Kinds that must be produced upstream before this node can run. Without
    # these a builder wires marketing_copy with nothing feeding it, the graph
    # validates, and the run fails on the first call.
    requires: tuple[str, ...] = ()
    note: str = ""

    class Config:
        extra = "allow"

    def runnable(self) -> bool:
        """Whether a graph containing this node can actually be executed here."""
        return self.availability == AVAILABLE

    def emittable(self) -> bool:
        """Whether a graph is allowed to contain it at all."""
        return self.availability in (AVAILABLE, EXTERNAL)


GATES_DOC = "stage names that stop for a human, e.g. ['concept'] or ['pre_image:scene_1']"
# Shape is a channel decision, so the planner has to be able to make it. Left
# undeclared, every asset came out square whatever it was for — a Reel, a
# landing-page hero and an email banner alike.
RATIO_IMAGE_DOC = (
    "frame shape for this node: 1:1 or 4:5 feed, 9:16 story/Reel, 16:9 web hero, "
    "2:1 email banner. Defaults to 1:1"
)
RATIO_VIDEO_DOC = "frame shape: 9:16 for Reels and stories, 16:9 for web. Defaults to 9:16"
# Off unless someone asks. A generator left to itself sets a tagline nobody
# wrote, a price that contradicts the offer, or a watermark in a script it
# cannot spell — and a neatly set wrong price is worse than a garbled one,
# because it looks finished.
TEXT_DOC = (
    "true ONLY if the request explicitly wants words rendered INTO the picture — a price on "
    "the shot, a badge, a tagline in frame. Defaults to false, which forbids all lettering. "
    "A caption or a price the request wants WRITTEN belongs to marketing_copy, not here"
)
SEG_DOC = "which audience this node is for; selects segment-scoped guardrails"
ASSET_DOC = "what kind of asset this is (teaser, hero, cutdown, banner); selects asset-scoped guardrails"


def _c(**kw) -> Component:
    return Component(**kw)


# ── The registry ─────────────────────────────────────────────────────────────
#
# Ids are the strings a graph uses verbatim. Changing one is a breaking change to
# every saved graph, so they match what the Flow Builder already emits rather
# than being tidied up here.

REGISTRY: tuple[Component, ...] = (
    # Inputs and data — owned by the studio front end.
    _c(id="text", label="Text", category=CAT_INPUT, availability=EXTERNAL,
       produces=("text",), note="A prompt or brief typed by the user."),
    _c(id="upload", label="Upload", category=CAT_INPUT, availability=EXTERNAL,
       produces=("image",), note="Product photos supplied by the user."),
    _c(id="asset", label="Asset", category=CAT_INPUT, availability=EXTERNAL,
       produces=("image",), note="An image already in the user's library."),
    _c(id="brand_kit", label="Brand kit", category=CAT_INPUT, availability=EXTERNAL,
       produces=("brand",), note="Brand context. Takes no inputs."),
    _c(id="product", label="Product", category=CAT_INPUT, availability=EXTERNAL,
       produces=("product",), note="A catalogue product. Takes no inputs."),

    # Generators this backend runs.
    _c(id="image_generator", label="Image generator", category=CAT_GENERATE,
       availability=AVAILABLE, endpoint="POST /creative-studio/api/image/generate",
       consumes=("text", "image", "brand"), produces=("image",),
       params={"prompt": "what to generate"},
       note="One-off generation with NO brand checks, product rules or QA. For campaign "
            "work use scene_image_generator instead."),
    _c(id="video_generator", label="Video generator", category=CAT_GENERATE,
       availability=AVAILABLE, endpoint="POST /creative-studio/api/video/generate",
       consumes=("text", "image"), produces=("video",),
       params={"prompt": "what to generate"},
       note="One clip, max 8s, NO brand checks and no stitching. For campaign films use "
            "video_director instead."),
    _c(id="versa_3d", label="3D (Versa)", category=CAT_GENERATE,
       availability=AVAILABLE, endpoint="POST /versa-ai/api/generate",
       consumes=("image",), produces=("model_3d",),
       note="Never chain brand_kit or product into this."),
    _c(id="assistant", label="Assistant", category=CAT_AGENT,
       availability=AVAILABLE, endpoint="POST /assist/api/chat",
       consumes=("text",), produces=("text",)),

    # Campaign Studio agents — one node per step endpoint.
    _c(id="creative_director_concept", label="Creative Director — concept", category=CAT_AGENT,
       availability=AVAILABLE, endpoint="POST /campaign-studio/api/agent/concept",
       consumes=("text", "image", "brand"), produces=("concept",),
       params={"assetCount": "how many creatives the campaign needs",
               "instructions": "extra direction in plain language",
               "gates": GATES_DOC, "audienceSegment": SEG_DOC, "assetType": ASSET_DOC}),
    _c(id="storyboard_designer", label="Storyboard Designer", category=CAT_AGENT,
       availability=AVAILABLE, endpoint="POST /campaign-studio/api/agent/storyboard",
       consumes=("concept", "image", "brand"), produces=("storyboard",), requires=("concept",),
       params={"assetCount": "how many scenes", "instructions": "extra direction",
               "gates": GATES_DOC, "audienceSegment": SEG_DOC, "assetType": ASSET_DOC}),
    _c(id="scene_image_generator", label="Scene image generator", category=CAT_GENERATE,
       availability=AVAILABLE, endpoint="POST /campaign-studio/api/agent/scene-images",
       consumes=("storyboard", "image", "brand"), produces=("image",), requires=("storyboard",),
       params={"sceneIds": "which scenes THIS node generates, as scene_1, scene_2, ... in "
                           "storyboard order — those ids are assigned by position when the "
                           "storyboard is written, so no other form resolves. Empty means "
                           "every scene in the storyboard it receives. Set this only when two "
                           "of these nodes share one storyboard, so each owns a disjoint "
                           "subset instead of both generating every scene in it",
               "aspectRatio": RATIO_IMAGE_DOC, "gates": GATES_DOC,
               "instructions": "extra direction in plain language, applied to every scene "
                               "this node generates",
               "textInImage": TEXT_DOC,
               "audienceSegment": SEG_DOC, "assetType": ASSET_DOC}),
    _c(id="qa_check", label="QA check", category=CAT_CHECK,
       availability=AVAILABLE, endpoint="POST /campaign-studio/api/agent/qa",
       consumes=("image", "storyboard"), produces=("verdict",), requires=("image",),
       params={"sceneId": "which scene to check, as scene_1, scene_2, ... in storyboard "
                          "order; defaults to the first",
               "audienceSegment": SEG_DOC, "assetType": ASSET_DOC},
       note="scene_image_generator already runs QA on every scene. Add this node only "
            "when a scene needs checking on its own."),
    _c(id="marketing_copy", label="Marketing copy", category=CAT_AGENT,
       availability=AVAILABLE, endpoint="POST /campaign-studio/api/agent/marketing",
       consumes=("concept", "storyboard", "brand"), produces=("copy",),
       requires=("concept", "storyboard"),
       params={"instructions": "extra direction", "gates": GATES_DOC,
               "audienceSegment": SEG_DOC, "assetType": ASSET_DOC}),
    _c(id="video_director", label="Video Director", category=CAT_AGENT,
       availability=AVAILABLE, endpoint="POST /campaign-studio/api/agent/video",
       consumes=("storyboard", "image", "copy"), produces=("video",), requires=("storyboard",),
       params={"sceneIds": "scenes to cut, in playback order, as scene_1, scene_2, ... — "
                           "storyboard scenes are numbered by position, so no other form "
                           "resolves. More cuts is a longer film",
               "targetSeconds": "roughly how long the film should run",
               "aspectRatio": RATIO_VIDEO_DOC,
               "instructions": "extra direction", "gates": GATES_DOC,
               "audienceSegment": SEG_DOC, "assetType": ASSET_DOC},
       note="Builds a film from several cuts. A single clip cannot exceed 8s, so length "
            "comes from how many scenes you list."),

    # Publishing — owned by the studio front end.
    _c(id="list", label="List", category=CAT_PUBLISH, availability=EXTERNAL),
    _c(id="compare", label="Compare", category=CAT_PUBLISH, availability=EXTERNAL),
    _c(id="export_publish", label="Export / publish", category=CAT_PUBLISH,
       availability=EXTERNAL),

    # Named, specified, not runnable. A graph may not contain these.
    _c(id="text_preserve", label="Text preserve", category=CAT_GENERATE,
       availability=PLANNED, consumes=("image", "model_3d"), produces=("model_3d",),
       note="No executor exists anywhere in this backend."),
    _c(id="upscale", label="Upscale", category=CAT_GENERATE, availability=PLANNED,
       endpoint="POST /creative-studio/api/upscale",
       consumes=("image",), produces=("image",),
       note="The service endpoint is live; no workflow node is wired to it yet."),
    _c(id="tts", label="Text to speech", category=CAT_GENERATE, availability=PLANNED,
       endpoint="POST /creative-studio/api/speak",
       consumes=("text",), produces=("audio",),
       note="The service endpoint is live; no workflow node is wired to it yet."),
    _c(id="video_extend", label="Extend video", category=CAT_GENERATE, availability=PLANNED,
       endpoint="POST /creative-studio/api/video/extend",
       consumes=("video",), produces=("video",),
       note="The service endpoint is live; no workflow node is wired to it yet."),
    _c(id="video_combine", label="Combine video", category=CAT_GENERATE, availability=PLANNED,
       endpoint="POST /creative-studio/api/video/combine",
       consumes=("video",), produces=("video",),
       note="The service endpoint is live; no workflow node is wired to it yet."),
    _c(id="router", label="Router", category=CAT_CHECK, availability=PLANNED,
       consumes=("text",), produces=("text",),
       note="Conditional branching. Not designed yet."),
    _c(id="scene_composer", label="Scene composer", category=CAT_GENERATE, availability=PLANNED,
       consumes=("image",), produces=("image",),
       note="Not designed yet."),
)

_BY_ID: dict[str, Component] = {c.id: c for c in REGISTRY}


def get(component_id: str) -> Component | None:
    return _BY_ID.get(str(component_id or "").strip())


def all_components() -> tuple[Component, ...]:
    return REGISTRY


def ids(availability: str | None = None) -> list[str]:
    return [c.id for c in REGISTRY if availability is None or c.availability == availability]


def emittable_ids() -> list[str]:
    """Types a graph may legally contain."""
    return [c.id for c in REGISTRY if c.emittable()]


class GraphProblem(BaseModel):
    node_id: str
    type: str
    problem: str
    detail: str


def validate_graph(graph: dict | None) -> list[GraphProblem]:
    """
    Every reason a graph cannot run, rather than the first one.

    Reported instead of raised: a caller fixing a graph wants the whole list, and
    the Flow Builder uses it to retry with the problems fed back rather than
    failing the request outright.
    """
    problems: list[GraphProblem] = []
    nodes = (graph or {}).get("nodes")
    if not isinstance(nodes, list):
        return problems

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"#{index}")
        node_type = str(node.get("type") or "").strip()

        component = get(node_type)
        if component is None:
            problems.append(
                GraphProblem(
                    node_id=node_id,
                    type=node_type,
                    problem="unknown",
                    detail=(
                        f"{node_type!r} is not a node type. Use one of: "
                        f"{', '.join(emittable_ids())}."
                    ),
                )
            )
        elif not component.emittable():
            problems.append(
                GraphProblem(
                    node_id=node_id,
                    type=node_type,
                    problem="unavailable",
                    detail=(
                        f"{node_type!r} is planned but not runnable"
                        + (f" — {component.note}" if component.note else "")
                        + ". Remove it or replace it with an available node."
                    ),
                )
            )

    problems.extend(_wiring_problems(graph, nodes))
    return problems


def _wiring_problems(graph: dict, nodes: list) -> list[GraphProblem]:
    """
    Nodes whose required inputs nothing upstream produces.

    Checking types alone called a graph runnable when `marketing_copy` had
    nothing feeding it — it validated, then failed on the first call with "needs
    concept from an upstream node". A graph is not runnable because its nodes
    exist; it is runnable because they are connected.
    """
    by_id = {str(n.get("id")): n for n in nodes if isinstance(n, dict) and n.get("id")}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    parents: dict[str, list[str]] = {}
    for e in edges:
        parents.setdefault(str(e.get("target")), []).append(str(e.get("source")))

    def upstream_kinds(node_id: str, seen: set[str] | None = None) -> set[str]:
        seen = seen if seen is not None else set()
        kinds: set[str] = set()
        for pid in parents.get(node_id, []):
            if pid in seen or pid not in by_id:
                continue
            seen.add(pid)
            comp = get(str(by_id[pid].get("type") or ""))
            if comp:
                kinds.update(comp.produces)
            kinds.update(upstream_kinds(pid, seen))
        return kinds

    out: list[GraphProblem] = []
    for node_id, node in by_id.items():
        comp = get(str(node.get("type") or ""))
        if comp is None or not comp.requires:
            continue
        available = upstream_kinds(node_id)
        missing = [k for k in comp.requires if k not in available]
        if missing:
            makers = {
                k: [c.id for c in REGISTRY if k in c.produces and c.emittable()]
                for k in missing
            }
            out.append(
                GraphProblem(
                    node_id=node_id,
                    type=comp.id,
                    problem="unwired",
                    detail=(
                        f"{comp.id} needs {', '.join(missing)} from upstream and nothing "
                        "connected to it produces that. Add and connect one of: "
                        + "; ".join(f"{k} <- {', '.join(v)}" for k, v in makers.items())
                    ),
                )
            )
    return out


def render_for_prompt() -> str:
    """
    The allowed-types block, generated rather than hand-maintained.

    Planned types are listed as forbidden instead of omitted. A model that has
    seen `text_preserve` in training will otherwise reach for it, and a name it
    has been told not to use is refused more reliably than a name it was never
    shown.
    """
    lines: list[str] = ["Valid node types — use these ids exactly:"]
    for category in (CAT_INPUT, CAT_GENERATE, CAT_AGENT, CAT_CHECK, CAT_PUBLISH):
        listed = [c for c in REGISTRY if c.category == category and c.emittable()]
        if not listed:
            continue
        lines.append("")
        lines.append(f"{category}:")
        for c in listed:
            bits = [f"  {c.id}"]
            if c.requires:
                bits.append(f"needs {', '.join(c.requires)} from upstream")
            if c.params:
                bits.append("params: " + ", ".join(f"{k} ({v})" for k, v in c.params.items()))
            lines.append(" — ".join(bits))
            if c.note:
                lines.append(f"      {c.note}")

    blocked = [c.id for c in REGISTRY if not c.emittable()]
    if blocked:
        lines.append("")
        lines.append(
            "NOT available — never put these in a graph, even if the request asks for "
            "the capability: " + ", ".join(blocked) + ". If a request needs one, build "
            "what you can and say in the description which part could not be built."
        )
    return "\n".join(lines)


__all__ = [
    "ALL_AVAILABILITY",
    "AVAILABLE",
    "EXTERNAL",
    "PLANNED",
    "CAT_AGENT",
    "CAT_CHECK",
    "CAT_GENERATE",
    "CAT_INPUT",
    "CAT_PUBLISH",
    "Component",
    "GraphProblem",
    "REGISTRY",
    "all_components",
    "emittable_ids",
    "get",
    "ids",
    "render_for_prompt",
    "validate_graph",
]
