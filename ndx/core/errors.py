class NDXError(Exception):
    """Base class for all NDX errors."""

class ConfigError(NDXError):
    pass

class PermissionDenied(NDXError):
    pass

class ValidationError(NDXError):
    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = list(problems or [])

class ToolUnavailable(NDXError):
    """An external tool/binary required by a feature is not available."""
