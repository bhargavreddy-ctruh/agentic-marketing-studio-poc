"""
Pre-flight requirements check for ideation.
Detects missing assets, ambiguous formats, and vague asks deterministically before invoking the LLM graph.
"""
import re

from ...core.deliverables import detect_deliverable, detect_deliverable_keys
from ...schemas.sessions.responses import IdeationOption, IdeationPrompt

_VAGUE_REFINEMENT_RE = re.compile(
    r"^(make it|can you make it|just make it)\s+(pop|better|cooler|interesting|nicer|awesome|good|great|different)\.?$", 
    re.IGNORECASE
)

def is_vague_refinement(text: str) -> bool:
    if not text:
        return False
    return bool(_VAGUE_REFINEMENT_RE.search(text.strip()))


def run_requirements_check(
    user_message: str,
    brief: dict,
    existing_elements: list,
    referenced_elements: list,
) -> IdeationPrompt | None:
    # 1. Vague Refinement Check
    if is_vague_refinement(user_message):
        return IdeationPrompt(
            message="That's a bit broad! Pick a specific direction to take this, or let me surprise you:",
            options=[
                IdeationOption(id="Bolder lighting + color grade", label="Bolder lighting & colors", description="Increase contrast and color saturation"),
                IdeationOption(id="Dynamic background + light streaks", label="Dynamic background", description="Add light streaks and motion"),
                IdeationOption(id="Add stylized graphics/badges", label="Add graphics/badges", description="Include stylish visual elements"),
                IdeationOption(id="surprise me", label="Surprise me", description="Pick a creative direction automatically"),
            ],
            allow_free_text=True
        )

    # 2. Missing Explicit Reference Check
    missing_asset_phrases = re.compile(r"\b(my image|my photo|my face|my logo|my product|this image|that image|apply my image)\b", re.IGNORECASE)
    mentions_asset = bool(missing_asset_phrases.search(user_message))
    
    has_image_ref = any(e.element_type == "image" for e in referenced_elements)
    has_any_image_on_canvas = any(e.element_type == "image" for e in existing_elements)
    
    if mentions_asset and not has_image_ref:
        options = []
        if has_any_image_on_canvas:
            options.append(IdeationOption(id="pick_from_canvas", label="Pick from canvas", description="Select an existing image from the canvas"))
        options.append(IdeationOption(id="upload_now", label="Upload one now", description="Upload an image to use as reference"))
        options.append(IdeationOption(id="generate_it", label="Let AI generate it", description="I will generate an appropriate asset"))
        
        return IdeationPrompt(
            message="You mentioned using an image or asset, but I'm not sure which one you mean. Please select one:",
            options=options,
            allow_free_text=True
        )

    # 2.5 Text-Only Exemption
    # If the user is explicitly asking to write copy/text, bypass the visual deliverable checks.
    # This prevents text requests containing platform names (e.g., "write an instagram caption") 
    # from falsely triggering the image-formatting disambiguation loop.
    _is_copy_request = bool(re.search(r"\b(write|draft)\b.*\b(caption|description|copy|headline|script)\b", user_message, re.IGNORECASE))
    if _is_copy_request:
        return None

    # 2.6 Campaign / Multi-Deliverable Exemption
    # If the user asks for a campaign, package, or multiple platforms/deliverables,
    # do NOT trap them in a single-format disambiguation modal or demand a subject image.
    # The Orchestrator will autonomously assemble multi-platform assets and marketing copy.
    lower_msg = user_message.lower()
    brief_ctx = f"{brief.get('primary_user_request', '')} {brief.get('idea', '')}".lower()
    combined_ctx = f"{lower_msg} {brief_ctx}"
    
    is_campaign = any(
        w in combined_ctx
        for w in (
            "campaign", "package", "multi-platform", "social package", "promo package", 
            "sale package", "sale", "promo", "promotion", "launch", "advertisement", "ad set"
        )
    )
    named_deliverables = detect_deliverable_keys(user_message)
    
    # Check if multiple platforms/channels are mentioned
    platform_keywords = ("instagram", "insta", "twitter", "tweet", "youtube", "tiktok", "facebook", "linkedin")
    platforms_mentioned = sum(1 for p in platform_keywords if p in lower_msg)
    is_multi_platform = len(named_deliverables) > 1 or platforms_mentioned > 1 or ("twitter" in combined_ctx and "instagram" in combined_ctx)
    
    if is_campaign or is_multi_platform:
        return None

    # 3. Deliverable Spec Check
    spec = detect_deliverable(user_message)
    if not spec:
        spec = detect_deliverable(brief.get("deliverable", ""))
        
    if spec:
        # Ambiguous Format Check (e.g. "instagram post")
        # Do not prompt if the user's message already states the format/aspect ratio explicitly
        has_explicit_format = bool(re.search(r"\b(1:1|4:5|9:16|16:9|square|portrait|landscape|banner|story|reel)\b", lower_msg))
        if spec.is_ambiguous and not has_explicit_format:
            return IdeationPrompt(
                message=f"For an {spec.label.split(' (')[0]}, which format do you prefer?",
                options=[
                    IdeationOption(id="instagram_square", label="Square (1:1)", description="Standard feed post"),
                    IdeationOption(id="meta_portrait", label="Portrait (4:5)", description="Taller post, takes up more screen space"),
                ],
                allow_free_text=True
            )

        # Required Slots Check
        if "subject_image" in spec.required_slots and not has_image_ref and not brief.get("product_photo_storage_ref"):
                options = []
                if has_any_image_on_canvas:
                    options.append(IdeationOption(id="pick_from_canvas", label="Pick from canvas", description="Select an existing image from the canvas"))
                options.append(IdeationOption(id="upload_now", label="Upload one now", description="Upload an image to use as reference"))
                options.append(IdeationOption(id="generate_it", label="Let AI generate it", description="I will generate an appropriate asset"))
                
                return IdeationPrompt(
                    message=f"To create a {spec.label}, I need a main subject image to work with. What should we use?",
                    options=options,
                    allow_free_text=True
                )

    return None
