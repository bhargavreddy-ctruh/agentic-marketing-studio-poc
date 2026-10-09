from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMProvider, LLMResult, ModelTier

log = get_logger(__name__)


class GeminiProvider(LLMProvider):
    def _get_model_for_tier(self, tier: ModelTier) -> str:
        if tier == ModelTier.TIER_1:
            return settings.gemini_model_tier_1
        if tier == ModelTier.TIER_2:
            return settings.gemini_model_tier_2
        if tier == ModelTier.TIER_3:
            return settings.gemini_model_tier_3
        return settings.gemini_model_tier_1

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        model = self._get_model_for_tier(tier)

        if not settings.gemini_api_key:
            raise ProviderUnavailable("gemini", "GEMINI_API_KEY is missing.")

        client = genai.Client(api_key=settings.gemini_api_key)

        # Map tools to Gemini format
        gemini_tools = None
        if tools:
            funcs = []
            for t in tools:
                if t.get("type") == "function" and "function" in t:
                    funcs.append(t["function"])
            if funcs:
                gemini_tools = [{"function_declarations": funcs}]

        # Map OpenAI messages to Gemini contents
        contents = []
        current_role = None
        current_parts = []

        def flush_current():
            nonlocal current_role, current_parts
            if current_role and current_parts:
                contents.append({"role": current_role, "parts": current_parts})
            current_role = None
            current_parts = []

        for m in messages:
            role = m.get("role", "user")
            parts = []
            
            if role == "system":
                continue
                
            if m.get("content"):
                parts.append({"text": str(m["content"])})
                
            if "tool_calls" in m:
                for tc in m["tool_calls"]:
                    if tc.get("type") == "function":
                        func = tc["function"]
                        args = func.get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        parts.append({
                            "function_call": {"name": func["name"], "args": args},
                            "thought_signature": "skip_thought_signature_validator"
                        })
                        
            if role == "tool":
                gemini_role = "user"
                parts.append({
                    "function_response": {
                        "name": m.get("name", "unknown_function"),
                        "response": {"result": m.get("content")}
                    }
                })
            else:
                gemini_role = "model" if role == "assistant" else "user"

            if current_role == gemini_role:
                current_parts.extend(parts)
            else:
                flush_current()
                current_role = gemini_role
                current_parts = parts

        flush_current()

        try:
            config = types.GenerateContentConfig(
                system_instruction=system,
                tools=gemini_tools,
                max_output_tokens=max_tokens,
                temperature=0.7,
            )

            if on_delta:
                response = await client.aio.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config
                )
                full_text = []
                tool_calls = []
                
                async for chunk in response:
                    if chunk.text:
                        full_text.append(chunk.text)
                        on_delta(chunk.text)
                    
                    if chunk.function_calls:
                        for fc in chunk.function_calls:
                            tool_calls.append({
                                "id": f"call_{fc.name}",
                                "type": "function",
                                "function": {
                                    "name": fc.name,
                                    "arguments": json.dumps(fc.args) if fc.args else "{}"
                                }
                            })
                
                return LLMResult(
                    text="".join(full_text),
                    model=model,
                    tool_calls=tool_calls
                )
            else:
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config
                )
                
                content = response.text or ""
                tool_calls = []
                if response.function_calls:
                    for fc in response.function_calls:
                        tool_calls.append({
                            "id": f"call_{fc.name}",
                            "type": "function",
                            "function": {
                                "name": fc.name,
                                "arguments": json.dumps(fc.args) if fc.args else "{}"
                            }
                        })
                return LLMResult(text=content, model=model, tool_calls=tool_calls)

        except Exception as e:
            log.exception("Gemini API Error")
            raise ProviderUnavailable("gemini", f"{model} failed: {e!s}")
