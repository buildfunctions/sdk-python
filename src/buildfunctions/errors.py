"""Buildfunctions SDK Error Classes."""

from __future__ import annotations

from typing import Any

from buildfunctions.types import ErrorCode


class BuildfunctionsError(Exception):
    """Base error for all Buildfunctions SDK errors."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = "UNKNOWN_ERROR",
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details


class AuthenticationError(BuildfunctionsError):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Invalid or missing API key") -> None:
        super().__init__(message, "UNAUTHORIZED", 401)


class NotFoundError(BuildfunctionsError):
    """Raised when a resource is not found."""

    def __init__(self, resource: str = "Resource") -> None:
        super().__init__(f"{resource} not found", "NOT_FOUND", 404)


class ValidationError(BuildfunctionsError):
    """Raised when input validation fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "VALIDATION_ERROR", 400, details)


class CapacityError(BuildfunctionsError):
    """Raised when the service is at maximum capacity."""

    def __init__(self, message: str = "Service at maximum capacity. Please try again later.") -> None:
        super().__init__(message, "MAX_CAPACITY", 503)


def _error_code_from_status(status_code: int) -> ErrorCode:
    """Map HTTP status code to error code."""
    match status_code:
        case 401:
            return "UNAUTHORIZED"
        case 404:
            return "NOT_FOUND"
        case 400:
            return "INVALID_REQUEST"
        case 503:
            return "MAX_CAPACITY"
        case 409:
            return "SIZE_LIMIT_EXCEEDED"
        case _:
            return "UNKNOWN_ERROR"


def _map_error_code(code: str | None, status_code: int) -> ErrorCode:
    """Map error code string to ErrorCode, falling back to status code mapping."""
    valid_codes: set[str] = {
        "UNAUTHORIZED",
        "NOT_FOUND",
        "INVALID_REQUEST",
        "MAX_CAPACITY",
        "SIZE_LIMIT_EXCEEDED",
        "VALIDATION_ERROR",
    }
    if code and code in valid_codes:
        return code  # type: ignore[return-value]
    return _error_code_from_status(status_code)


def error_from_response(status_code: int, body: dict[str, Any]) -> BuildfunctionsError:
    """Create an error from an API response."""
    message = body.get("error", "An unknown error occurred")
    code = _map_error_code(body.get("code"), status_code)
    return BuildfunctionsError(message, code, status_code)
