"""Interfaces to the outside world. src/entry.py implements them with Cloudflare's runtime;
the tests implement them with fakes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class HttpResponse:
    status: int
    text: str
    location: str | None = None
    set_cookies: list[str] = field(default_factory=list)


class Http(Protocol):
    async def request(self, method: str, url: str, headers: dict[str, str], body: str | None) -> HttpResponse:
        """Must NOT follow redirects (the Moodle client does that itself to keep cookies)."""
        ...


class KV(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
