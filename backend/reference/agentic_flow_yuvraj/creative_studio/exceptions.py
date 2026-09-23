"""
exceptions.py -- Custom exception hierarchy for Ctruh AI Creative Studio.

All exceptions carry an HTTP status code so the error-mapping helper in main.py
can convert them to the correct HTTPException without any conditional logic at
the call site.

Hierarchy:
    CreativeStudioError (base, 500)
        GeminiError         (502 -- Gemini API unreachable or returned an error)
        GeminiRateLimitError (429 -- Gemini quota exceeded)
        GeminiTimeoutError  (504 -- Gemini did not respond in time)
        VideoTimeoutError   (504 -- Veo long-running operation timed out)
        ImageTooLargeError  (413 -- uploaded image exceeds the size limit)
        UnsupportedMediaError (415 -- image MIME type not accepted)
        InvalidInputError   (400 -- bad request from caller)
"""


class CreativeStudioError(Exception):
    """Base class for all AI Creative Studio errors. Carries an HTTP status code."""
    status_code: int = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class GeminiError(CreativeStudioError):
    """
    Raised when the Gemini API returns a non-2xx response or the response
    body cannot be parsed into the expected shape.
    """
    status_code = 502


class GeminiRateLimitError(CreativeStudioError):
    """
    Raised when Gemini returns HTTP 429 (quota exceeded).
    Callers should surface this to the client so they know to retry.
    """
    status_code = 429


class GeminiTimeoutError(CreativeStudioError):
    """
    Raised when the Gemini HTTP request times out before returning a response.
    """
    status_code = 504


class VideoTimeoutError(CreativeStudioError):
    """
    Raised when the Veo long-running operation does not complete within the
    maximum polling window (VIDEO_POLL_MAX_ATTEMPTS * VIDEO_POLL_INTERVAL_SECONDS).
    """
    status_code = 504


class ImageTooLargeError(CreativeStudioError):
    """
    Raised when the caller-supplied base64 image exceeds MAX_IMAGE_SIZE_BYTES
    after decoding.
    """
    status_code = 413


class UnsupportedMediaError(CreativeStudioError):
    """
    Raised when the caller-supplied MIME type is not in SUPPORTED_IMAGE_MIME_TYPES.
    """
    status_code = 415


class InvalidInputError(CreativeStudioError):
    """
    Raised for malformed or logically invalid request data that passes Pydantic
    validation but fails domain-level constraints (e.g. empty prompt after stripping).
    """
    status_code = 400
