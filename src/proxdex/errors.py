class ProxdexError(Exception):
    """Base error for proxdex."""


class LibraryError(ProxdexError):
    """No library found, or the library layout is invalid."""


class ConfigError(ProxdexError):
    """``proxdex.toml`` holds a value that isn't one of the allowed options."""


class FileError(ProxdexError):
    """Per-item error: reported, item skipped, batch continues."""
