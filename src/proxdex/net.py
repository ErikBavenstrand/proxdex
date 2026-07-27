"""Shared HTTP for the card providers: rate limiting, retries, cache, health.

The APIs proxdex depends on are flaky (scrydex answers 500 often enough that a
one-shot GET is not good enough), so every request goes through :func:`get`:

* **rate-limited per host** — Scryfall asks for 50-100 ms between calls;
* **retried** with exponential backoff on 429/5xx and transport errors,
  honouring ``Retry-After`` when the server sends one;
* **cached on disk** for metadata (JSON) responses, which also gives a *stale*
  copy to serve when every attempt failed — a degraded API still lists cards;
* **recorded**, per host, in a small state file, so both the CLI and the web UI
  can say "scrydex is degraded" instead of just failing oddly. The UI shells
  out to the CLI for mutations, so this has to survive across processes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import requests

_UA: Final = "proxdex (+https://github.com/ErikBavenstrand/proxdex)"
_SESSION: Final = requests.Session()
_SESSION.headers.update({"User-Agent": _UA, "Accept": "application/json"})

#: Scryfall asks for 50-100ms between requests; be a good citizen.
MIN_INTERVAL: Final = 0.1
#: Statuses worth another attempt: rate limit, and the 5xx family (incl. the
#: Cloudflare-only codes these APIs sit behind).
RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 524})
MAX_ATTEMPTS: Final = 4
#: attempt N sleeps BACKOFF * 2**(N-1) seconds — 0.5, 1, 2 between 4 attempts.
BACKOFF: Final = 0.5
#: never wait longer than this on a Retry-After
MAX_BACKOFF: Final = 8.0
TIMEOUT: Final = 30.0
#: a card's metadata never changes; a day-old name/set/image URL is still right
CACHE_TTL: Final = 24 * 3600.0
#: a *search* can gain cards (new set, new print), so keep that hit short-lived
SEARCH_TTL: Final = 900.0
#: how long a recorded incident keeps a host flagged as degraded
HEALTH_TTL: Final = 300.0

_last_call: dict[str, float] = {}


class Health(StrEnum):
    """How an API host is behaving, most recently."""

    OK = "ok"
    DEGRADED = "degraded"  # answered, but only after retries (or from cache)
    DOWN = "down"  # every attempt failed


@dataclass(frozen=True, slots=True)
class _HostState:
    health: Health
    detail: str
    at: float


#: how each host behaved in *this* process (the CLI's own notice)
_run: dict[str, _HostState] = {}


@dataclass(frozen=True, slots=True)
class HostHealth:
    host: str
    health: Health
    detail: str
    age: float

    @property
    def message(self) -> str:
        return f"{self.host} is {self.health.value} — {self.detail}"


@dataclass(slots=True)
class Reply:
    """A response body plus its status — cacheable, unlike a socket."""

    status: int
    body: bytes
    stale: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.body)


class NetworkError(Exception):
    """Every attempt at a request failed (transport, or a retryable status)."""


# ------------------------------------------------------------------- public ---
def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str = "",
    cache: bool = False,
    ttl: float = CACHE_TTL,
) -> Reply:
    """GET ``url``, rate-limited and retried; ``cache`` stores/serves the body.

    A non-retryable answer (including 404) is returned as-is — callers turn it
    into their own message. Raises :class:`NetworkError` only when nothing
    usable came back, cache included.
    """
    host = _host(url)
    key = _key(url, params) if cache else None
    if key and (hit := _cache_read(key, ttl)) is not None:
        return hit

    detail = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _throttle(host)
        resp: requests.Response | None = None
        try:
            resp = _SESSION.get(
                url,
                params=params,
                headers={"Accept": accept} if accept else None,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            detail = type(exc).__name__
        else:
            if resp.status_code not in RETRY_STATUSES:
                if attempt > 1:
                    _record(host, Health.DEGRADED, f"recovered after {attempt} tries")
                else:
                    _record(host, Health.OK, "")
                reply = Reply(resp.status_code, resp.content)
                if key and reply.ok:
                    _cache_write(key, reply)
                return reply
            detail = f"HTTP {resp.status_code}"
        if attempt < MAX_ATTEMPTS:
            time.sleep(_backoff(attempt, resp))

    _record(host, Health.DOWN, f"{detail} after {MAX_ATTEMPTS} tries")
    if key and (stale := _cache_read(key, ttl=None)) is not None:
        return Reply(stale.status, stale.body, stale=True)
    raise NetworkError(f"{host}: {detail} after {MAX_ATTEMPTS} tries")


def health() -> list[HostHealth]:
    """Hosts with a recent incident, worst first — empty when all is well.

    Read from the shared state file, so the UI sees what a CLI subprocess hit.
    """
    return _sorted(_health_read())


def incidents() -> list[HostHealth]:
    """The misbehaving hosts *this process* actually talked to — what a single
    CLI command should report, without dragging in an older command's trouble."""
    return _sorted(_run)


def _sorted(states: dict[str, _HostState]) -> list[HostHealth]:
    order = {Health.DOWN: 0, Health.DEGRADED: 1, Health.OK: 2}
    now = time.time()
    out = [
        HostHealth(host, state.health, state.detail, now - state.at)
        for host, state in states.items()
        if state.health is not Health.OK and now - state.at < HEALTH_TTL
    ]
    return sorted(out, key=lambda h: (order[h.health], h.age))


def cache_dir() -> Path:
    """Where cached bodies and host health live (``$PROXDEX_CACHE`` overrides).

    Each platform's own cache location: ``%LOCALAPPDATA%`` on Windows (a POSIX
    ``~/.cache`` there works, but it is not where a Windows user or their disk
    cleanup would ever look), ``$XDG_CACHE_HOME`` or ``~/.cache`` elsewhere.
    Relocating this is safe by definition — it is a cache, and
    ``proxdex where --clear-cache`` empties it.
    """
    if env := os.environ.get("PROXDEX_CACHE"):
        return Path(env).expanduser()
    if sys.platform == "win32" and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local) / "proxdex" / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "proxdex"


def clear_cache() -> int:
    """Drop every cached response; returns how many files went."""
    gone = 0
    for path in cache_dir().glob("http/*.json"):
        with contextlib.suppress(OSError):
            path.unlink()
            gone += 1
    return gone


# ------------------------------------------------------------------ helpers ---
def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def _throttle(host: str) -> None:
    wait = MIN_INTERVAL - (time.monotonic() - _last_call.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def _backoff(attempt: int, resp: requests.Response | None) -> float:
    """Exponential backoff, or the server's ``Retry-After`` when it asked."""
    delay = BACKOFF * 2 ** (attempt - 1)
    if resp is not None:
        raw = resp.headers.get("Retry-After", "").strip()
        if raw.isdigit():
            delay = max(delay, float(raw))
    return min(delay, MAX_BACKOFF)


def _key(url: str, params: dict[str, Any] | None) -> str:
    payload = json.dumps([url, sorted((params or {}).items(), key=str)], default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _cache_read(key: str, ttl: float | None) -> Reply | None:
    """The cached reply, or ``None``; ``ttl=None`` accepts it at any age."""
    path = cache_dir() / "http" / f"{key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if ttl is not None and time.time() - float(raw["at"]) > ttl:
            return None
        return Reply(int(raw["status"]), str(raw["body"]).encode())
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cache_write(key: str, reply: Reply) -> None:
    """Best-effort: a cache that cannot be written must never fail a command."""
    try:
        body = reply.body.decode()
    except UnicodeDecodeError:  # binary (an image) — not worth caching
        return
    _write(
        cache_dir() / "http" / f"{key}.json",
        {"at": time.time(), "status": reply.status, "body": body},
    )


def _health_path() -> Path:
    return cache_dir() / "health.json"


def _health_read() -> dict[str, _HostState]:
    try:
        raw = json.loads(_health_path().read_text(encoding="utf-8"))
        return {
            str(host): _HostState(
                Health(entry["health"]), str(entry["detail"]), float(entry["at"])
            )
            for host, entry in raw.items()
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _record(host: str, health_: Health, detail: str) -> None:
    """Note how ``host`` just behaved, for the CLI/UI degradation notice."""
    states = _health_read()
    now = time.time()
    _run[host] = _HostState(health_, detail, now)
    if health_ is Health.OK and host not in states:
        return  # nothing to clear; don't write a file just to say "fine"
    states[host] = _HostState(health_, detail, now)
    _write(
        _health_path(),
        {
            host: {"health": s.health.value, "detail": s.detail, "at": s.at}
            for host, s in states.items()
            if now - s.at < HEALTH_TTL
        },
    )


def _write(path: Path, payload: dict[str, Any]) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
