"""
revision.py — Changing work that already exists, in words.

Everything until now shaped work *before* it happened. `instructions` steer a
step, a gate stops one mid-flight, `reject_and_retry` sends feedback back. All of
them run the step again from its inputs, and the agent never sees what it
produced last time — so "make the tagline punchier" rewrote the headline too, and
"warmer light" on an image regenerated the scene from the product photo rather
than adjusting the picture on screen.

A revision is the other direction: here is the output, change this about it.

Two shapes, because the two kinds of work fail differently:

* **Text** carries its previous version along with the instruction. The agent is
  shown both and told to change only what was asked. Without the previous
  version it is not a revision, it is a re-roll that happens to have a hint.
* **An image** carries a scene id. The picture itself comes from the assets the
  caller is already sending back, and the point is that the generated image
  becomes the *source* — an edit of what exists, not a fresh scene that happens
  to be described differently.

A revision never skips the checks. It is the one path where a human's words go
straight into a generator, which is exactly when a brand rule is most likely to
be broken by accident.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

MAX_INSTRUCTION = 2000


class Revision(BaseModel):
    """A change to a text stage's previous output."""

    instruction: str = Field(..., min_length=1, max_length=MAX_INSTRUCTION)
    # What to change. Optional only so a caller that lost it still gets an
    # instruction-shaped re-run rather than an error.
    previous: Optional[dict[str, Any]] = None

    class Config:
        extra = "allow"

    def usable(self) -> bool:
        return bool((self.instruction or "").strip())


class SceneRevision(BaseModel):
    """A change to one already-generated scene image."""

    scene_id: str = Field(..., min_length=1, max_length=64)
    instruction: str = Field(..., min_length=1, max_length=MAX_INSTRUCTION)

    class Config:
        extra = "allow"


def coerce_revision(raw: Any) -> Revision | None:
    if isinstance(raw, Revision):
        return raw if raw.usable() else None
    if isinstance(raw, dict):
        try:
            r = Revision(**raw)
            return r if r.usable() else None
        except Exception:  # noqa: BLE001 — a malformed revision must not kill a run
            return None
    return None


def coerce_scene_revisions(raw: Any) -> dict[str, str]:
    """Scene id -> instruction. Later entries for one scene replace earlier ones."""
    out: dict[str, str] = {}
    for item in raw or ():
        if isinstance(item, SceneRevision):
            out[item.scene_id] = item.instruction
            continue
        if not isinstance(item, dict):
            continue
        sid = str(item.get("scene_id") or "").strip()
        text = str(item.get("instruction") or "").strip()
        if sid and text:
            out[sid] = text[:MAX_INSTRUCTION]
    return out


def render_text_revision(revision: Revision | None, *, label: str) -> str:
    """
    The revision block for a text agent's prompt.

    Says what to change and, deliberately, what not to: an agent handed a
    complaint about one field will happily rewrite the rest, and a human who
    asked for a punchier tagline did not ask for a new headline.
    """
    if revision is None or not revision.usable():
        return ""

    import json  # local: this module is otherwise dependency-free

    parts = [
        f"REVISION — this is not a fresh {label}. A human read the version below "
        "and asked for one change.",
    ]
    if revision.previous:
        parts.append(f"Previous {label}:\n{json.dumps(revision.previous, indent=2)[:3000]}")
    else:
        parts.append(
            f"The previous {label} was not supplied, so reproduce it as faithfully as "
            "the inputs allow and apply the change to that."
        )
    parts.append(f"What they asked for:\n{revision.instruction}")
    parts.append(
        "Apply exactly that. Keep every other field as it was, word for word where "
        "nothing about it needed to change. Do not take the request as licence to "
        "rewrite the rest, and do not drop a brand rule to satisfy it — if the two "
        "conflict, follow the brand and say so."
    )
    return "\n\n".join(parts)


def render_image_revision(instruction: str) -> str:
    """
    The prompt for editing an existing image.

    Phrased as an edit of the attached picture rather than a description of a
    scene, because the attached picture is the generated asset, not the product
    photo. A prompt that reads like a fresh brief makes the model start over and
    the human's "just make it darker" comes back as a different shot.
    """
    return (
        "EDIT THE ATTACHED IMAGE. It is an existing approved-in-progress asset, not "
        "a product reference. Change only what is asked and keep the composition, "
        "framing, product and every other detail identical.\n\n"
        f"Change requested:\n{instruction.strip()}"
    )


__all__ = [
    "MAX_INSTRUCTION",
    "Revision",
    "SceneRevision",
    "coerce_revision",
    "coerce_scene_revisions",
    "render_image_revision",
    "render_text_revision",
]
