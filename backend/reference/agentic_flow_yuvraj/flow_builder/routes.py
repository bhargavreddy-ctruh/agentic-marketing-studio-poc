"""Flow Builder — prompt → Agent Designer / Creative Studio workflow JSON."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent_core import get_llm_client, llm_configured, llm_health_status, require_llm_key, LLMError
from ..agent_core import components
from ..agent_core.brand_context import (
    BrandDetails,
    ProductDetails,
    ProjectDetails,
    context_block,
)
from ..creative_studio.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/flow-builder", tags=["Flow Builder"])

_SCHEMA_BODY = """
Graph schema:
{
  "schemaVersion": 1,
  "nodes": [{"id": "string", "type": "node_type", "params": {}, "position": {"x": 0, "y": 0}}],
  "edges": [{"id": "string", "source": "nodeId", "target": "nodeId"}]
}

The user writes like a client, not an engineer. They say "get me a nicer product shot" or
"make a quick reel for the college crowd", never "concept then storyboard then scene images".
Translating that into the right nodes is your whole job — do not expect them to name any.

WHICH PATH — decide this first, it matters more than anything else below:

- Marketing or campaign work for a brand — a product shot, a social post, an ad, a reel, a
  lookbook, a launch — ALWAYS goes through the campaign chain:
      text + upload → creative_director_concept → storyboard_designer → scene_image_generator
  and marketing_copy / video_director hanging off it as the request needs.
  That chain is what applies brand rules, product facts, QA and the brand guardian. Anything
  the user wants to sell something with belongs here.

- image_generator and video_generator are BARE generators with none of those checks. Use them
  only for a one-off with no brand behind it — a quick illustration, a test render. If a brand
  or a product is in play and you reach for these, the work ships unchecked. Do not.

Reading a request:
- "a shot", "a hero image", "a post", "creative", "static" → the campaign chain, ending at
  scene_image_generator. Add marketing_copy when the request mentions caption, copy, price,
  discount or anything written.
- "a reel", "a video", "an ad film", "cutdowns" → the campaign chain ending at video_director,
  with params.targetSeconds set from any length the user implies ("quick reel" ≈ 15,
  "teaser" ≈ 6, "proper launch video" ≈ 30).
- Two audiences, two crowds, "one for X and one for Y" → one shared concept and storyboard,
  then a SEPARATE branch per audience, each with params.audienceSegment naming that audience.
- Several deliverables of different kinds ("teaser, launch video, some cuts") → one branch per
  deliverable, each with params.assetType naming it (teaser, hero, cutdown, banner).
- A number of shots, scenes or assets → params.assetCount.
- Scene ids are not yours to invent, and you cannot know them: the Storyboard Designer runs
  later, on a different call. Its scenes are numbered from their order — scene_1, scene_2, up to
  scene_N for an N-scene storyboard — and that is the ONLY form params.sceneIds and params.sceneId
  may take. A descriptive id like "scene_1_hero" names a scene that will never exist, and the node
  asking for it fails. Say which scene by its number and put the intent in the node's own id.
- Whenever more than one scene_image_generator receives the SAME storyboard — a hero branch and
  a social branch off one 2-scene storyboard, say — give each one params.sceneIds naming the
  scene(s) it owns: the hero branch gets ["scene_1"], the social branch ["scene_2"].
  scene_image_generator generates every scene it is handed; leaving sceneIds empty on more than
  one branch off the same storyboard means each of them regenerates every scene in it, silently
  doubling (or worse) the real cost of a graph that meant to split the work, not duplicate it.
  With only ONE scene_image_generator on a storyboard, leave sceneIds off entirely — it already
  generates every scene, and naming a subset there just drops the rest of the storyboard.
- A video_director's targetSeconds sets a FLOOR on the storyboard's assetCount, not the other
  way around: one clip is at most 8 seconds, so ceil(targetSeconds / 8) is the fewest scenes
  that storyboard needs regardless of what the user did or did not say about shot count — a 15s
  target needs at least 2 scenes, a 30s target needs at least 4. A storyboard with fewer leaves
  the target unreachable (the Director will say so and make the longest film it can, which is
  not what was asked for) and, on a single scene, throws away the very thing that makes a cut
  open on the right picture and flow into the next one: with only one scene there is no "next"
  to flow into. When nothing else in the request implies a count, default the storyboard feeding
  a video_director to this floor rather than to 1.

- Words rendered INTO a picture are off by default and stay off unless the request actually asks
  for them in the frame. "Mentions the price and the discount" is a CAPTION — that is
  marketing_copy's job, and it is checked against the real figures. Set
  scene_image_generator params.textInImage only for a request that plainly wants type in the
  shot itself: "put the offer on the image", "a price badge on the creative", "the tagline
  across the top". When in doubt leave it off: an asset with no type can have a caption added,
  and one with the wrong price burnt into it cannot be fixed at all.

Only ever set params a node actually lists above. A param the node does not declare is dropped
without warning, so an invented one silently does nothing.

Rules:
- Use unique ids.
- Space nodes horizontally (~320px apart); stack parallel nodes vertically (~160px).
- For 3D / Versa: Text → Image Generator → versa_3d (never chain brand_kit or product into versa_3d).
- For brand text on 3D: build Text → Image Generator → versa_3d and say in the description that preserving the brand text needs a node that does not exist yet. Do not add one.
- brand_kit and product have NO inputs — connect them in parallel to generators, never text → brand_kit.
- Creative Studio campaign chain: idea(text) + upload → creative_director_concept → storyboard_designer (one per shot with params.shotIndex) → scene_image_generator; also marketing_copy and video_director. The chain ENDS at scene_image_generator — do not append qa_check to it.
- A number of shots/scenes/assets sets params.assetCount on creative_director_concept and on the storyboard_designer. Use ONE storyboard_designer with that count and ONE scene_image_generator — the scene generator makes every scene in the storyboard.
- EVERY node must be connected: upload must feed concept, each storyboard, and each scene_image_generator — these three read the raw photo directly. scene_image_generator must receive its storyboard. marketing_copy receives concept + storyboard. video_director is NOT one of upload's direct consumers: it receives copy + storyboard always, plus an edge from EVERY scene_image_generator that generates a scene named in its own params.sceneIds — that edge, not the raw photo, is a cut's real source, so a cut opens on its own scene's actual picture instead of falling back to the product photo, which is the worse frame and the one that happens by default when the edge is missing. Give video_director its own direct edge from upload ONLY when the graph has no scene_image_generator to receive images from instead — with one present, an added upload edge is not extra safety, it is a second path to a frame the first one already supplies, and draws a graph that overstates how directly video_director touches the raw photo. video_director does NOT need qa_check either: a QA verdict is not something it reads, and wiring one in connects nothing. A graph with any disconnected node is INVALID.
- NEVER add qa_check after a scene_image_generator. That node already runs the Critic, the brand
  guardian and the logo check on every scene it generates, and returns all three verdicts with the
  image. A qa_check behind it re-runs the same vision calls on the same picture and produces a
  SECOND set of verdicts that can disagree with the first — which is worse than the cost, because
  then nothing on screen says which verdict is the campaign's. qa_check exists only for an image
  that has NOT been through that: one from the bare image_generator, or an asset the user supplied.
  If every image in the graph comes from a scene_image_generator, the graph needs no qa_check at all.
- Put the user campaign idea into the text node's params.text ONLY when creating a new graph — never overwrite existing text params with an edit instruction.
- Never invent node types outside the allowed list.
Return ONLY valid JSON with keys: name, description, graph.
"""

# The allowed-types block is generated from the component registry rather than
# maintained here. Two lists drift: the hand-written one still offered
# `text_preserve`, which nothing in this backend can execute, so a graph
# containing it built cleanly and then failed to run with nothing pointing at why.
NODE_SCHEMA_HINT = components.render_for_prompt() + "\n" + _SCHEMA_BODY

EDIT_MODE_HINT = """
You are editing an EXISTING workflow graph.
- Modify the graph according to the user request ONLY.
- Preserve existing node ids, params, and positions unless the request requires changing them.
- Do NOT replace the whole graph with a new scaffold.
- Do NOT put the edit instruction into a text node's params.text.
- Add / remove / rewire nodes only as needed to fulfill the request.
- Return the FULL updated graph (all nodes and edges), not a patch.
"""


class FlowBuilderRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    brand_kit: Optional[Dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    # What is being sold. The planner needs it as much as the agents do: the
    # number of variants, the length of a film and the ratio each channel wants
    # all follow from the product and the campaign, not from the wording of the
    # prompt. Without it the planner was inferring a shape it had no facts for.
    product_details: Optional[ProductDetails] = None
    current_graph: Optional[Dict[str, Any]] = None


def _extract_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from LLM text (fences, embedded objects, trailing junk)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model response")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    candidates: List[str] = [text]
    # Largest {...} block in the response
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        candidates.append(brace.group(0))

    last_err: Optional[Exception] = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            # Truncation repair: close open braces/brackets and retry once
            repaired = candidate.rstrip().rstrip(",")
            open_braces = repaired.count("{") - repaired.count("}")
            open_brackets = repaired.count("[") - repaired.count("]")
            if open_braces > 0 or open_brackets > 0:
                repaired += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
                try:
                    parsed = json.loads(repaired)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception as exc2:  # noqa: BLE001
                    last_err = exc2
    raise ValueError(f"Could not parse JSON from model response: {last_err}")


def _normalize_flow_payload(data: Dict[str, Any], *, prompt: str, edit_mode: bool) -> Dict[str, Any]:
    """Accept either {name,description,graph} or a bare graph object."""
    if isinstance(data.get("graph"), dict):
        out = dict(data)
        graph = out["graph"]
    elif isinstance(data.get("nodes"), list):
        # Model returned the graph at the top level
        out = {
            "name": data.get("name") or (prompt[:60] or "Assist-built Agent"),
            "description": data.get("description")
            or f"{'Edited' if edit_mode else 'Built'} from Assist prompt: {prompt[:200]}",
            "graph": {
                "schemaVersion": data.get("schemaVersion") or 1,
                "nodes": data.get("nodes") or [],
                "edges": data.get("edges") or [],
            },
        }
        graph = out["graph"]
    else:
        raise ValueError("Missing graph")

    out.setdefault("name", prompt[:60] or "Assist-built Agent")
    out.setdefault(
        "description",
        f"{'Edited' if edit_mode else 'Built'} from Assist prompt: {prompt[:200]}",
    )
    if not isinstance(graph, dict):
        raise ValueError("Missing graph")
    graph.setdefault("schemaVersion", 1)
    graph.setdefault("nodes", [])
    graph.setdefault("edges", [])
    out["graph"] = graph
    return out


def _has_usable_graph(graph: Optional[Dict[str, Any]]) -> bool:
    if not graph or not isinstance(graph, dict):
        return False
    nodes = graph.get("nodes")
    return isinstance(nodes, list) and len(nodes) > 0


# The instruction already says "a reel" means video_director — in plain
# language, not as an if/then a model can miss. It got missed anyway: asked for
# "a nicer product reel", the graph that came back was two still-image branches
# and nothing else. The prompt strongly resembles the still-image example one
# line above it in this same file ("a nicer product shot"), and evidently that
# resemblance outweighed the one word that was actually different.
#
# This is a fact a regex is good at and a model call is not: does the request
# name a video format at all. Checked mechanically, the same way an invented
# node type is — not to force a video node into every graph that says the word,
# but to make the model look again with the specific gap named, rather than
# trust that it read its own instructions correctly the first time.
_VIDEO_WORDS = re.compile(r"\b(reels?|videos?|films?|cutdowns?|clips?)\b", re.IGNORECASE)


def _wants_video(prompt: str) -> bool:
    return bool(_VIDEO_WORDS.search(prompt))


def _has_video_node(graph: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(graph, dict):
        return False
    return any(
        isinstance(n, dict) and n.get("type") == "video_director"
        for n in (graph.get("nodes") or [])
    )


# These three read the raw product photo directly to do their job — concept
# looks at it, the storyboard is written against it, and the scene generator
# renders from it. The system prompt already says, as a hard rule, that upload
# must feed every one of them and a graph without that wiring is invalid.
# Checked here for the same reason _wants_video is: not because the rule was
# unclear, but because a rule can be stated and still not survive one specific
# call. Existence only, not full wiring — the observed failure was not "wired
# to the wrong node", it was "the node was never built at all", the more
# severe half of the same rule and the cheaper half to check with certainty.
#
# Deliberately not added to any node's `requires` in the registry instead:
# that tuple drives validate_graph for every caller, including a human
# hand-building a graph in the console who may have a real reason to omit an
# explicit upload node — product_images_base64 reaches every step through the
# request body regardless of this wiring, so the graph still runs either way.
# What is wrong here is specific to the planner: it is the one caller with an
# explicit, self-stated rule to be held to.
_UPLOAD_CONSUMERS = {
    "creative_director_concept", "storyboard_designer", "scene_image_generator",
}


def _missing_upload_node(graph: Optional[Dict[str, Any]]) -> bool:
    """
    True when something here reads the raw product photo and nothing supplies it.

    video_director is deliberately not in the set above. Its frames come from
    whichever scene_image_generator feeds it — _frames_for prefers a scene's
    own generated image and only falls back to the product photo for a scene
    that has none — so counting it as a direct consumer of upload pushed the
    planner into drawing a second edge to a frame the first path already
    supplied, and a graph that overstates how directly the film touches the
    raw photo. It becomes the consumer only when there is no scene generator
    anywhere for it to receive images from instead, which is the one shape
    where that fallback is the whole story rather than a fallback.
    """
    if not isinstance(graph, dict):
        return False
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    types = {n.get("type") for n in nodes}
    if "upload" in types:
        return False
    if types & _UPLOAD_CONSUMERS:
        return True
    return "video_director" in types and "scene_image_generator" not in types


def _strip_redundant_qa(graph: Optional[Dict[str, Any]]) -> List[str]:
    """
    Drop qa_check nodes judging an image that has already been judged.

    scene_image_generator runs the Critic, the brand guardian and the logo
    check on every scene it generates and returns all three verdicts with the
    image. A qa_check wired behind one re-runs the same vision calls on the
    same picture. Reported live: qa_hero and qa_social, ~75s between them, and
    a second brand verdict that FAILED on a scene whose own guardian had
    passed it — two answers to one question with nothing on screen saying
    which one is the campaign's. That is the real cost; the wasted calls are
    the cheap half.

    Removed rather than asked about again, unlike the video and upload checks.
    Those are additive and a judgment call, so the model gets to disagree and
    say why. This is neither: whether the image already went through QA is a
    fact about the graph, and nothing downstream reads a verdict — no node in
    the registry requires one — so deleting the node cannot break a wiring
    that validated. Cheaper too: no extra model call to fix what a regex-free
    ancestor walk already knows.

    Narrow on purpose. A qa_check fed by the bare image_generator, or by an
    asset the user supplied, is the case the node exists for and is left
    alone. A qa_check with no image upstream at all is a different fault and
    validate_graph already reports it.
    """
    if not isinstance(graph, dict):
        return []
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]

    parents: Dict[str, List[str]] = {}
    for edge in edges:
        parents.setdefault(str(edge.get("target")), []).append(str(edge.get("source")))

    def has_scene_generator_upstream(node_id: str, seen: Optional[set] = None) -> bool:
        seen = seen if seen is not None else set()
        for pid in parents.get(node_id, []):
            if pid in seen or pid not in by_id:
                continue
            seen.add(pid)
            if by_id[pid].get("type") == "scene_image_generator":
                return True
            if has_scene_generator_upstream(pid, seen):
                return True
        return False

    doomed = [
        str(n.get("id"))
        for n in nodes
        if n.get("type") == "qa_check" and has_scene_generator_upstream(str(n.get("id")))
    ]
    if not doomed:
        return []

    dead = set(doomed)
    graph["nodes"] = [n for n in (graph.get("nodes") or [])
                      if not (isinstance(n, dict) and str(n.get("id")) in dead)]
    graph["edges"] = [
        e for e in (graph.get("edges") or [])
        if not (isinstance(e, dict)
                and (str(e.get("source")) in dead or str(e.get("target")) in dead))
    ]
    return doomed


async def build_flow_from_prompt(
    *,
    prompt: str,
    brand_kit: Optional[Dict[str, Any]] = None,
    current_graph: Optional[Dict[str, Any]] = None,
    brand_details: Any = None,
    project_details: Any = None,
    product_details: Any = None,
) -> Dict[str, Any]:
    edit_mode = _has_usable_graph(current_graph)

    if not llm_configured():
        raise LLMError(
            "LLM is not configured, so no workflow can be built. Set the API key and retry."
        )

    try:
        require_llm_key()
        client = get_llm_client()
        if edit_mode:
            system = (
                "You are Commverse Flow Builder in EDIT mode. "
                "Modify the provided workflow graph per the user request.\n"
                + NODE_SCHEMA_HINT
                + "\n"
                + EDIT_MODE_HINT
                + "\nRespond with ONLY valid JSON: {\"name\":\"...\",\"description\":\"...\",\"graph\":{...}}."
            )
            user = (
                f"Edit request:\n{prompt}\n\n"
                f"Current workflow graph (JSON):\n{json.dumps(current_graph)[:12000]}"
            )
        else:
            system = (
                "You are Commverse Flow Builder. Convert a creative campaign request "
                "into an Agent Designer workflow JSON.\n" + NODE_SCHEMA_HINT
            )
            user = f"Campaign request:\n{prompt}"
        # The flow builder plans arbitrary graphs, so it gets the full context
        # rather than a role slice — see _RELEVANCE in brand_context.
        brand_block = context_block(
            brand=brand_details,
            project=project_details,
            product=product_details,
            legacy_kit=brand_kit,
            fallback="",
            kit_limit=1500,
        )
        if brand_block:
            # Called "what you are planning for" rather than "optional context":
            # the channels, the audience and the product are what decide the
            # shape of the graph, and labelling them optional invited the model
            # to plan from the prompt's wording and ignore them.
            user += f"\n\nWhat you are planning for:\n{brand_block}"

        async def _call(messages: list[dict[str, Any]], max_tokens: int) -> Dict[str, Any]:
            response = await client.chat(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
            )
            content = response.text if hasattr(response, "text") else str(response)
            raw = _extract_json(content)
            data = _normalize_flow_payload(raw, prompt=prompt, edit_mode=edit_mode)
            if edit_mode and not _has_usable_graph(data["graph"]):
                raise ValueError("Edit mode returned an empty graph")
            return data

        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        try:
            data = await _call(messages, 32000)
        except Exception as first_exc:
            if not edit_mode:
                raise first_exc
            # One repair retry for truncated / malformed edit responses
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was invalid JSON or missing graph. "
                        "Return ONLY complete JSON with keys name, description, and graph "
                        "(full updated nodes+edges). Do not truncate."
                    ),
                }
            )
            data = await _call(messages, 32000)

        # "Never invent node types" was only ever a request. Check it, and give
        # the model the specific nodes to fix rather than asking again generally
        # — a repeated instruction it already ignored produces the same graph.
        problems = components.validate_graph(data.get("graph"))
        if problems:
            log.info(
                "flow builder produced %d unusable node(s): %s",
                len(problems),
                ", ".join(f"{p.node_id}={p.type}" for p in problems),
            )
            messages.append({"role": "assistant", "content": json.dumps(data)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That graph contains nodes that cannot run:\n"
                        + "\n".join(f"- node {p.node_id}: {p.detail}" for p in problems)
                        + "\nReturn the FULL corrected graph with those nodes removed or "
                        "replaced. Keep everything else exactly as it was."
                    ),
                }
            )
            data = await _call(messages, 32000)

            # Still broken: hand back what was asked for and say what is wrong,
            # rather than a graph that looks valid and fails at execution.
            remaining = components.validate_graph(data.get("graph"))
            if remaining:
                data["problems"] = [p.model_dump() for p in remaining]
                log.warning(
                    "flow builder could not produce a runnable graph: %s",
                    ", ".join(p.detail for p in remaining),
                )

        # A missing video node is not invalid the way an invented type is — the
        # graph runs fine, it is just possibly not what was asked for. So this
        # is phrased as a check, not a command: the model can decide a video
        # genuinely is not warranted and say so, rather than being told to add
        # one whether or not that is right. What it cannot do is skip the
        # question — "a reel" sitting unaddressed is exactly what happened
        # before this existed.
        if _wants_video(prompt) and not _has_video_node(data.get("graph")):
            log.info("flow builder: prompt names a video format, graph has no video_director")
            messages.append({"role": "assistant", "content": json.dumps(data)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Look again at the request: it names a video format (reel, video, "
                        "film, cutdown, or clip), and this graph has no video_director node. "
                        "If that is really what should be built, return the FULL corrected "
                        "graph with a video_director added and wired to storyboard_designer. "
                        "If a video genuinely does not belong here, return the graph exactly "
                        "as it was and say why in the description."
                    ),
                }
            )
            revised = await _call(messages, 32000)
            revised_problems = components.validate_graph(revised.get("graph"))
            if not revised_problems:
                data = revised
            # A revision that reintroduces an invented node type is worse than
            # the gap it was trying to close; keep the graph that at least runs.

        # Unlike the video check above, this one is not a judgment call — the
        # rule already says, unconditionally, that a graph missing this wiring
        # is invalid, so the correction is phrased as an instruction rather
        # than a question the model could reasonably decline.
        if _missing_upload_node(data.get("graph")):
            log.info("flow builder: campaign chain present with no upload node feeding it")
            messages.append({"role": "assistant", "content": json.dumps(data)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "This graph reads the raw product photo somewhere and has no upload "
                        "node anywhere in it. Return the FULL corrected graph with an upload "
                        "node added and wired to the concept, each storyboard, and each "
                        "scene_image_generator — the three that read the photo directly. Do "
                        "NOT wire it to video_director as well when a scene_image_generator "
                        "feeds that node: its frames come from those generated scene images, "
                        "and a direct upload edge alongside them says the film reads the raw "
                        "photo more directly than it does. Wire upload to video_director only "
                        "if there is no scene_image_generator in the graph at all."
                    ),
                }
            )
            revised = await _call(messages, 32000)
            revised_problems = components.validate_graph(revised.get("graph"))
            if not revised_problems and not _missing_upload_node(revised.get("graph")):
                data = revised

        # Last, so it also catches one the repair passes above reintroduced —
        # they hand the model the whole graph back and it is free to add a node
        # nobody asked it to touch.
        dropped = _strip_redundant_qa(data.get("graph"))
        if dropped:
            log.info("flow builder: dropped redundant qa_check node(s): %s", ", ".join(dropped))
            # Reported, not silent. A node the caller might have expected to see
            # is gone, and "where did qa_hero go" is a worse question than the
            # duplicate verdicts it was removed to prevent.
            data["removed_nodes"] = [
                {"node_id": nid, "type": "qa_check",
                 "reason": "scene_image_generator already runs QA, the brand guardian and the "
                           "logo check on every scene it generates"}
                for nid in dropped
            ]

        return data
    except LLMError:
        raise
    except Exception as exc:
        verb = "edit" if edit_mode else "build"
        # Deliberately an error rather than a simpler graph. Anything this could
        # fall back to would skip the brand, product and QA checks, and handing
        # that over as the answer is how unchecked work reaches a campaign.
        raise LLMError(f"Failed to {verb} the workflow: {exc}") from exc


@router.get("/")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "flow-builder",
        "llmConfigured": llm_configured(),
        "llm": llm_health_status(),
    }


@router.get(
    "/api/components",
    summary="Node types a workflow graph may contain",
)
async def list_components(include_planned: bool = True) -> Dict[str, Any]:
    """
    The component registry.

    Exposed so a graph editor populates its palette from the same list the
    builder validates against. A UI keeping its own copy is how a node type ends
    up offered that nothing can run.
    """
    listed = [
        c for c in components.all_components()
        if include_planned or c.emittable()
    ]
    return {
        "success": True,
        "data": {
            "components": [c.model_dump() for c in listed],
            "emittable": components.emittable_ids(),
            "availability": {
                "available": "this backend runs it; `endpoint` says where",
                "external": "a real node, run by the studio front end rather than here",
                "planned": "named and specified; nothing runs it. A graph may not contain one",
            },
        },
    }


@router.post(
    "/api/validate",
    summary="Check a graph against the component registry",
)
async def validate_flow(body: Dict[str, Any]) -> Dict[str, Any]:
    """Every reason a graph cannot run, so an editor can show them before a run."""
    graph = body.get("graph") if isinstance(body.get("graph"), dict) else body
    problems = components.validate_graph(graph)
    return {
        "success": True,
        "data": {
            "runnable": not problems,
            "problems": [p.model_dump() for p in problems],
        },
    }


@router.post("/api/build")
async def build_flow(body: FlowBuilderRequest) -> Dict[str, Any]:
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    try:
        data = await build_flow_from_prompt(
            prompt=body.prompt.strip(),
            brand_kit=body.brand_kit,
            current_graph=body.current_graph,
            brand_details=body.brand_details,
            project_details=body.project_details,
            product_details=body.product_details,
        )
        return {"success": True, "data": data}
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
