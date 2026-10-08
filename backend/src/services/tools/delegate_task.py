"""
Delegate Task Tool — Agent-to-Agent collaboration.
Allows Lead Agents to dynamically call sub-agents and pass them instructions and context.
Includes strict guardrails: cycle detection (anti-ping-pong), depth limit (max 3), and retry controls.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, ClassVar

from .base import Tool, ToolResult
from .registry import register_tool

# Track the call stack to prevent infinite recursion and cycles.
_delegation_stack: ContextVar[list[str]] = ContextVar("_delegation_stack")
MAX_DELEGATION_DEPTH = 3

@register_tool("delegate_task")
class DelegateTaskTool(Tool):
    name = "delegate_task"
    description = (
        "Agent-to-agent collaboration. Delegate a sub-task to another specialist. "
        "Use this when you need a different agent (like a script_writer, sound_designer, or compliance_lead) "
        "to produce an asset, verify work, or retrieve information before you can finish your own job."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "target_specialist_name": {
                "type": "string",
                "description": "The exact name of the specialist to delegate to (e.g., 'script_writer', 'sound_designer', 'shot_planner').",
            },
            "task_instruction": {
                "type": "string",
                "description": "The specific instruction for the target agent.",
            },
            "job_id": {
                "type": "string",
                "description": "An optional unique identifier for this delegation to ensure idempotency and prevent duplicate executions.",
            }
        },
        "required": ["target_specialist_name", "task_instruction"],
    }

    # Store finished jobs in memory per worker to ensure idempotency.
    # In a full production environment across multiple workers, this could be backed by Redis.
    _completed_jobs: ClassVar[dict[str, dict[str, Any]]] = {}

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        target_name = args.get("target_specialist_name", "").strip()
        instruction = args.get("task_instruction", "").strip()
        job_id = args.get("job_id", "").strip()

        if not target_name or not instruction:
            return ToolResult(ok=False, data={}, error="target_specialist_name and task_instruction are required")

        # Idempotency Check: if this job_id was already completed successfully, return the cached result.
        if job_id and job_id in self._completed_jobs:
            return ToolResult(ok=True, data=self._completed_jobs[job_id])
        stack = _delegation_stack.get([])

        # Guardrail 1: Cycle Detection
        if target_name in stack:
            return ToolResult(
                ok=False, 
                data={}, 
                error=f"Cycle detected: {target_name} is already in the active call stack {stack}."
            )

        # Guardrail 2: Max Depth Limit
        if len(stack) >= MAX_DELEGATION_DEPTH:
            return ToolResult(
                ok=False, 
                data={}, 
                error=f"Max delegation depth ({MAX_DELEGATION_DEPTH}) reached. Cannot delegate to {target_name}."
            )

        # Add target to the stack for this execution branch
        new_stack = stack + [target_name]
        token = _delegation_stack.set(new_stack)

        try:
            # Deferred import to prevent circular dependency since run_specialist_agentic uses tools.
            from ..specialists.runner import _STEP_INSTRUCTION_MARKER, run_specialist_agentic
            
            ctx = context or {}
            
            # Extract the parent brief we injected into tool_context to maintain full project grounding.
            brief = ctx.get("_parent_brief", ctx)
            
            # Combine the generic preamble with the specific instruction using the expected marker
            combined_context = f"{_STEP_INSTRUCTION_MARKER}{instruction}"

            # Run the agent! Max iterations is set to 2 (Guardrail 2: Max Tries per Agent)
            result = await run_specialist_agentic(
                specialist_name=target_name,
                context=combined_context,
                max_iterations=2,
                brief=brief
            )
            
            # If the agent succeeded, get its resulting JSON data
            output_data = result.data
            
            # Cache the result if job_id was provided
            if job_id:
                self._completed_jobs[job_id] = output_data

            return ToolResult(ok=True, data=output_data)

        except Exception as e:
            # Safely catch SpecialistFailed and other errors to return cleanly to the delegator,
            # allowing the parent agent to handle the failure (e.g., retry or fallback).
            return ToolResult(ok=False, data={}, error=f"Delegation to {target_name} failed: {e!s}")
        finally:
            # Always reset the stack context when leaving
            _delegation_stack.reset(token)
