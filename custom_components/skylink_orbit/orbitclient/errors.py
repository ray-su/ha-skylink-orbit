"""Sanitized client failures; never include response bodies or credentials."""


class OrbitError(Exception):
    """Base client failure."""


class CannotConnect(OrbitError):
    """Service unreachable or an operation timed out."""


class AuthenticationError(OrbitError):
    """Explicit HTTP or MQTT authentication rejection."""


class LoginRejected(OrbitError):
    """Vendor rejected login with an undocumented result code."""


class ProtocolError(OrbitError):
    """Unexpected response schema."""


class CommandRejected(OrbitError):
    """Command cannot be safely submitted in the current session."""
