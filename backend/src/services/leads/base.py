"""
LeadSpec — a Lead is just an ordered list of specialist names, transcribed directly from
Architecture.md section 1a. Changing a Lead's recipe means editing that one list, never touching
the Orchestrator (Rules.md section 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LeadSpec:
    name: str
    specialist_sequence: tuple[str, ...]  # run in this order; parallel groups noted in docstring
    full_job_trigger: str


@dataclass
class LeadResult:
    """
    A typed result crossing the Lead-executor -> orchestration-graph boundary (Rules.md section 2:
    no bare dict crossing a layer boundary) — the previous shape was a plain dict consumed via
    `.get(...)`, undocumented as an accepted exception unlike GraphState's LangGraph-mandated
    TypedDict. `to_dict()` exists solely to cross into GraphState at the graph-assembly boundary,
    which IS the documented, LangGraph-required exception.
    """

    storage_ref: str
    produced_by_specialist: str
    element_type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage_ref": self.storage_ref,
            "produced_by_specialist": self.produced_by_specialist,
            "element_type": self.element_type,
            "metadata": self.metadata,
        }


@dataclass
class NarrativePlan:
    """Narrative Lead's output — a planning artifact, not a canvas asset, so it has no
    storage_ref/element_type (unlike LeadResult). Consumed by Motion Lead to enrich Camera
    Director's prompt (Architecture.md section 2.1: Narrative Lead -> Scene Lead -> Motion Lead).

    to_dict/from_dict exist so a plan can be staged in a session's JSON `brief` across turns —
    needed for real per-stage approval gates (Memory.md, Phase 4: "approve" mode pauses between
    Leads, so the already-produced plan must survive until the next turn resumes it)."""

    shots: tuple[str, ...]
    overall_story: str
    script_line: str | None
    pacing_target: str

    def to_dict(self) -> dict:
        return {
            "shots": list(self.shots), "overall_story": self.overall_story,
            "script_line": self.script_line, "pacing_target": self.pacing_target,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NarrativePlan":
        return cls(
            shots=tuple(data["shots"]), overall_story=data["overall_story"],
            script_line=data.get("script_line"), pacing_target=data["pacing_target"],
        )


@dataclass
class ScenePlan:
    """Scene Lead's output — a planning artifact for one shot, consumed by Motion Lead the same
    way as NarrativePlan. `scene_image_storage_ref` is the real starting-frame image Environment
    Designer generated (and Prop Stylist/Lighting Designer may have edited) — Motion Lead animates
    this directly rather than generating its own starting frame (Memory.md, Phase 2 correction).

    to_dict/from_dict — same reason as NarrativePlan's: survive a real approval-gate pause."""

    environment_description: str
    prop_description: str | None
    lighting_description: str
    scene_image_storage_ref: str

    def to_dict(self) -> dict:
        return {
            "environment_description": self.environment_description,
            "prop_description": self.prop_description,
            "lighting_description": self.lighting_description,
            "scene_image_storage_ref": self.scene_image_storage_ref,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScenePlan":
        return cls(
            environment_description=data["environment_description"],
            prop_description=data.get("prop_description"),
            lighting_description=data["lighting_description"],
            scene_image_storage_ref=data["scene_image_storage_ref"],
        )
