"""
Export Asset Tool
Allows an agent to package an existing canvas element/storage_ref into platform-specific ad specs.
This wraps the logic previously only available in the HTTP route `/export-all-specs`.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.local_storage import load_asset
from ...core.deliverables import DELIVERABLES
from .base import Tool, ToolResult
from .registry import register_tool
from .image_crop_resize import ImageCropResizeTool

AD_SPECS = {
    k: {
        "name": v.label,
        "width": v.width,
        "height": v.height,
        "aspect_ratio": v.aspect_ratio,
        "safe_zone_pct": v.safe_zone_pct,
    }
    for k, v in DELIVERABLES.items()
    if v.width and v.height
}

@register_tool("export_asset")
class ExportAssetTool(Tool):
    name = "export_asset"
    description = (
        "Exports an image asset into multiple platform-specific ad specs (e.g., 9:16, 1:1, 16:9). "
        "Returns a dictionary mapping ad spec IDs to their new storage_refs."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "storage_ref": {
                "type": "string",
                "description": "storage_ref of the image asset to export.",
            }
        },
        "required": ["storage_ref"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        storage_ref = str(args.get("storage_ref", "")).strip()

        if not storage_ref:
            return ToolResult(ok=False, data={}, error="storage_ref is required")

        loaded = await load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"Asset not found for {storage_ref}")

        crop_tool = ImageCropResizeTool()
        
        results = {}
        for spec_id, spec in AD_SPECS.items():
            res = await crop_tool.run(
                {
                    "storage_ref": storage_ref,
                    "target_width": spec["width"],
                    "target_height": spec["height"],
                    "crop_mode": "cover",
                }
            )
            if res.ok:
                results[spec_id] = {
                    "spec_name": spec["name"],
                    "width": spec["width"],
                    "height": spec["height"],
                    "storage_ref": res.data["storage_ref"],
                }
                
        if not results:
            return ToolResult(ok=False, data={}, error="Failed to export into any ad specs")

        return ToolResult(ok=True, data={"exports": results, "source_ref": storage_ref})
