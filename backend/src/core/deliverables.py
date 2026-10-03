"""
Deliverable Spec Registry
Single source of truth for all deterministic formats, aspect ratios, and their required slots.
"""
import re
from dataclasses import dataclass, field


@dataclass
class DeliverableSpec:
    key: str
    label: str
    aspect_ratio: str
    width: int | None = None
    height: int | None = None
    required_slots: list[str] = field(default_factory=list)
    optional_slots: list[str] = field(default_factory=list)
    safe_zone_pct: float = 0.05
    is_ambiguous: bool = False

DELIVERABLES = {
    "youtube_thumbnail": DeliverableSpec(
        key="youtube_thumbnail",
        label="YouTube Thumbnail (16:9)",
        aspect_ratio="16:9",
        width=1280,
        height=720,
        required_slots=["subject_image"],
        optional_slots=["host_photo", "channel_logo", "title_text"],
    ),
    "instagram_square": DeliverableSpec(
        key="instagram_square",
        label="Instagram Feed (1:1)",
        aspect_ratio="1:1",
        width=1080,
        height=1080,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "meta_portrait": DeliverableSpec(
        key="meta_portrait",
        label="Meta Feed Portrait (4:5)",
        aspect_ratio="4:5",
        width=1080,
        height=1350,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "instagram_story": DeliverableSpec(
        key="instagram_story",
        label="Instagram Story / Reels (9:16)",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
        safe_zone_pct=0.15,
    ),
    "landscape_banner": DeliverableSpec(
        key="landscape_banner",
        label="Google Display / Youtube (16:9)",
        aspect_ratio="16:9",
        width=1920,
        height=1080,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "linkedin_post": DeliverableSpec(
        key="linkedin_post",
        label="LinkedIn Post (1.91:1)",
        aspect_ratio="1.91:1",
        width=1200,
        height=628,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "poster": DeliverableSpec(
        key="poster",
        label="Poster (2:3)",
        aspect_ratio="2:3",
        required_slots=["subject_image"],
        optional_slots=["title_text"],
    ),
    "instagram_post_ambiguous": DeliverableSpec(
        key="instagram_post_ambiguous",
        label="Instagram Post (Format Needed)",
        aspect_ratio="1:1", # fallback
        is_ambiguous=True,
    )
}

_REGEX_MAPPINGS = [
    (r"\b(9:16|1080x1920)\b", "instagram_story"),
    (r"\b(story|stories|reel|reels|vertical)\b", "instagram_story"),
    
    (r"\b(16:9|1280x720|1920x1080|1080p)\b", "youtube_thumbnail"),
    (r"\b(youtube thumbnail|thumbnail)\b", "youtube_thumbnail"),
    (r"\b(banner|landscape)\b", "landscape_banner"),
    
    (r"\b(2:3)\b", "poster"),
    (r"\b(poster)\b", "poster"),
    
    (r"\b(1:1|1080x1080)\b", "instagram_square"),
    (r"\b(square)\b", "instagram_square"),
    
    (r"\b(4:5|1080x1350)\b", "meta_portrait"),
    (r"\b(portrait)\b", "meta_portrait"),
    
    (r"\b(1.91:1|1200x628)\b", "linkedin_post"),
    (r"\b(linkedin)\b", "linkedin_post"),
    
    (r"\b(instagram post|insta post|ig post)\b", "instagram_post_ambiguous"),
]

def detect_deliverable_key(text: str) -> str | None:
    """Returns the deliverable key if detected, else None."""
    if not text:
        return None
    lower_text = text.lower()
    for pattern, key in _REGEX_MAPPINGS:
        if re.search(pattern, lower_text):
            return key
    return None

def detect_deliverable(text: str) -> DeliverableSpec | None:
    key = detect_deliverable_key(text)
    return DELIVERABLES.get(key) if key else None

def get_deliverable(key: str) -> DeliverableSpec | None:
    return DELIVERABLES.get(key)
