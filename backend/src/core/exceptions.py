"""
Typed exception taxonomy — Rules.md section 4: if an error doesn't fit an existing type, add one
here rather than reaching for a generic exception. Vendor exceptions never escape a provider file;
they get caught and re-raised as one of these.
"""
from __future__ import annotations


class AppError(Exception):
    """Base for every typed error this app raises. Never raise this directly — raise a subtype."""

    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ProviderUnavailable(AppError):
    """A provider couldn't be reached, or is missing the credentials it needs to run."""

    def __init__(self, provider_name: str, reason: str = "not configured"):
        super().__init__(f"Provider '{provider_name}' is unavailable: {reason}", status_code=503)
        self.provider_name = provider_name


class ToolNotFound(AppError):
    """A specialist asked for a tool name that isn't in the TOOL_REGISTRY."""

    def __init__(self, tool_name: str):
        super().__init__(f"Tool '{tool_name}' is not registered", status_code=500)
        self.tool_name = tool_name


class SpecialistNotFound(AppError):
    """A Lead or the Orchestrator asked for a specialist name that isn't in the registry."""

    def __init__(self, specialist_name: str):
        super().__init__(f"Specialist '{specialist_name}' is not registered", status_code=500)
        self.specialist_name = specialist_name


class SpecialistFailed(AppError):
    """A specialist ran but could not produce a usable result after its retry budget."""

    def __init__(self, specialist_name: str, reason: str):
        super().__init__(f"Specialist '{specialist_name}' failed: {reason}", status_code=502)
        self.specialist_name = specialist_name


class SpecialistNeedsClarification(AppError):
    """Real, live-found gap (2026-09-30, explicit user ask: "don't just assume, make agents ask
    questions when there's genuine doubt"): a specialist facing genuine ambiguity (which of several
    linked products? use the real logo or a stylized one?) previously had no way to surface a real
    question distinct from a crash — the only recognized structured signal was `{"error": ...}`,
    routed through `SpecialistFailed`, indistinguishable from a real provider outage. This is a
    deliberate SIBLING to `SpecialistFailed`, not a subclass — every `except SpecialistFailed:` site
    needs its own explicit new branch to propagate this instead of silently swallowing it into
    generic failure handling, rather than this being accidentally caught by an existing broad
    catch. Carries the specialist's own real, specific question and (optionally) real pickable
    options — surfaced to the user via the same `next_prompt`/`awaiting_approval` machinery
    already proven for ideation's own pauses, never the generic "Ran into an issue — retry/cancel"
    wrapper `SpecialistFailed` produces."""

    def __init__(
        self, specialist_name: str, question: str,
        options: list[dict] | None = None, allow_free_text: bool = True,
    ):
        super().__init__(f"Specialist '{specialist_name}' needs clarification: {question}", status_code=422)
        self.specialist_name = specialist_name
        self.question = question
        self.options = options
        self.allow_free_text = allow_free_text


class ComplianceCheckFailed(AppError):
    """A compliance checker rejected an asset and no escalation resolved it."""

    def __init__(self, checker_name: str, reason: str):
        super().__init__(f"Compliance check '{checker_name}' failed: {reason}", status_code=422)
        self.checker_name = checker_name


class NotFoundError(AppError):
    """A requested resource (session, canvas element, brand, product) doesn't exist."""

    def __init__(self, resource: str, identifier: str):
        super().__init__(f"{resource} '{identifier}' not found", status_code=404)


class ValidationFailed(AppError):
    """Input passed schema validation but failed a business rule."""

    def __init__(self, message: str):
        super().__init__(message, status_code=400)


class Unauthorized(AppError):
    """No valid session cookie was presented — the caller isn't authenticated at all."""

    def __init__(self, message: str = "Not authenticated"):
        super().__init__(message, status_code=401)


class Forbidden(AppError):
    """The caller is authenticated, but doesn't own the resource they're trying to act on."""

    def __init__(self, message: str = "Not allowed to access this resource"):
        super().__init__(message, status_code=403)
