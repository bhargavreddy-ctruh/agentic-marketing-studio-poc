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
        label="Square Post (1:1)",
        aspect_ratio="1:1",
        width=1080,
        height=1080,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "meta_portrait": DeliverableSpec(
        key="meta_portrait",
        label="Portrait Post (4:5)",
        aspect_ratio="4:5",
        width=1080,
        height=1350,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "instagram_story": DeliverableSpec(
        key="instagram_story",
        label="Vertical Story / Reel (9:16)",
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
    "twitter_post": DeliverableSpec(
        key="twitter_post",
        label="Twitter / X Post (16:9)",
        aspect_ratio="16:9",
        width=1200,
        height=675,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "twitter_header": DeliverableSpec(
        key="twitter_header",
        label="Twitter / X Header (3:1)",
        aspect_ratio="3:1",
        width=1500,
        height=500,
        required_slots=["subject_image"],
        optional_slots=["title_text", "logo"],
    ),
    "instagram_post_ambiguous": DeliverableSpec(
        key="instagram_square",
        label="Square Post",
        aspect_ratio="1:1",
        is_ambiguous=False,
    )
}

# Real, live-found bug (2026-10-07, Monster Energy "1:1 vs got 16:9" complaint): this list is
# scanned first-match-wins (`detect_deliverable_key` below). It used to check the broad `16:9`/
# `banner`/`landscape` patterns BEFORE the `1:1`/`instagram_square`/`instagram_post_ambiguous`
# patterns, so a text containing both an explicit "16:9" token (for some unrelated part of a
# multi-asset request) and "Instagram"/"1:1" resolved to 16:9 regardless of actual intent.
# Reordered so every EXPLICIT numeric ratio/pixel-dimension token is checked first (most specific,
# least ambiguous), then the vaguer keyword-only patterns — a specific "1:1" should never lose to
# a vague "banner"/"landscape" match elsewhere in the same scanned text.
_REGEX_MAPPINGS = [
    # Explicit ratio/dimension tokens — most specific, checked first.
    (r"\b(9:16|1080x1920)\b", "instagram_story"),
    (r"\b(16:9|1280x720|1920x1080|1080p)\b", "youtube_thumbnail"),
    (r"\b(2:3)\b", "poster"),
    (r"\b(1:1|1080x1080)\b", "instagram_square"),
    (r"\b(4:5|1080x1350)\b", "meta_portrait"),
    (r"\b(1.91:1|1200x628)\b", "linkedin_post"),

    # Vaguer keyword-only patterns — checked only once no explicit ratio/dimension matched.
    # Tightened to avoid false positives on single nouns that can be subjects (e.g., "banner", "poster", "landscape", "square", "portrait", "story")
    # Instagram defaults directly to 1:1 square without halting the user for format disambiguation
    (r"\b(instagram post|insta post|ig post|instagram image|instagram)\b", "instagram_square"),
    (r"\b(instagram story|insta story|ig story|instagram reel|insta reel|ig reel|facebook story|fb story)\b", "instagram_story"),
    (r"\b(youtube thumbnail|yt thumbnail)\b", "youtube_thumbnail"),
    (r"\b(youtube video|yt video|landscape video|web banner|website banner)\b", "landscape_banner"),
    (r"\b(twitter post|twitter image|twitter banner|tweet|twitter|x post|x banner)\b", "twitter_post"),
    (r"\b(twitter header|x header)\b", "twitter_header"),
    (r"\b(movie poster|campaign poster)\b", "poster"),
    (r"\b(meta portrait|facebook portrait|fb portrait)\b", "meta_portrait"),
    (r"\b(linkedin post|linkedin)\b", "linkedin_post"),
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

def detect_deliverable_keys(text: str) -> list[str]:
    """Every DISTINCT deliverable key the message plausibly names, in first-match order — unlike
    `detect_deliverable_key` (which returns only the first match, correct for the common
    single-deliverable case), this is used to detect when a message names 2+ DIFFERENT
    deliverables in one turn (2026-10-06, live-found bug: "make a youtube thumbnail... and also
    generate an instagram 9:16 image post" matched `instagram_story`'s pattern before
    `youtube_thumbnail`'s, so the single global `brief["deliverable"]` this function's sibling
    returns collided both image steps onto one wrong aspect ratio). Callers use this to detect the
    multi-deliverable case and deliberately NOT set a single turn-global spec when it fires."""
    if not text:
        return []
    lower_text = text.lower()
    seen: list[str] = []
    for pattern, key in _REGEX_MAPPINGS:
        if key not in seen and re.search(pattern, lower_text):
            seen.append(key)
    return seen


def detect_deliverable(text: str) -> DeliverableSpec | None:
    key = detect_deliverable_key(text)
    return DELIVERABLES.get(key) if key else None

def get_deliverable(key: str) -> DeliverableSpec | None:
    return DELIVERABLES.get(key)
