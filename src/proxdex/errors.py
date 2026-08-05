class ProxdexError(Exception):
    """Base error for proxdex."""


class LibraryError(ProxdexError):
    """No library found, or the library layout is invalid."""


class ConfigError(ProxdexError):
    """``proxdex.toml`` holds a value that isn't one of the allowed options."""


class FileError(ProxdexError):
    """Per-item error: reported, item skipped, batch continues."""


class NoProviderError(ProxdexError):
    """This game has no metadata provider, so there is nothing to ask.

    A **custom game** (``<root>/games/<id>.json``) is one whose pictures you supply,
    so ``fetch``, ``search``, ``browse`` and the card data sheet have no API to
    reach. Its own error rather than a :class:`FileError`, because it is not a card
    that could not be found: nothing about the request will work on a retry, so the
    right report names ``import`` instead of the host that was not asked.
    """
