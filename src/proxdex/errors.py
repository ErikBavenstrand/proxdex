class ProxdexError(Exception):
    """Base error for proxdex."""


class LibraryError(ProxdexError):
    """No library found, or the library layout is invalid."""


class FileError(ProxdexError):
    """Per-item error: reported, item skipped, batch continues."""
