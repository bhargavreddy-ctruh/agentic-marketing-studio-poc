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
