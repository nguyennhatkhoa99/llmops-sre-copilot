"""Fetch a web page safely for the radar.

- Only ``https`` (``http`` if ``allow_http``), only hosts on ``allowed_domains``.
- SSRF guard: the host must resolve to public addresses only, checked again on
  every redirect (redirects are followed by hand, at most ``max_redirects``).
- Size cap: the body is streamed and cut at ``max_bytes``.
- HTML is reduced to visible text (scripts and styles dropped).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Sequence
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from beyondpilot.core.contracts import Page
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig

Resolver = Callable[[str], Sequence[str]]


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def host_allowed(host: str, allowed: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (a.lower() for a in allowed))


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        allowed_domains: Sequence[str],
        resilience: ResilienceConfig,
        max_bytes: int = 2_000_000,
        max_redirects: int = 3,
        allow_http: bool = False,
        user_agent: str = "BeyondPilot-Radar/0.1",
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.allowed_domains = list(allowed_domains)
        self.resilience, self.max_bytes = resilience, max_bytes
        self.max_redirects, self.allow_http = max_redirects, allow_http
        self.client = client or httpx.AsyncClient(
            follow_redirects=False, headers={"User-Agent": user_agent}
        )
        self.resolver = resolver or _resolve

    async def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        schemes = {"https", "http"} if self.allow_http else {"https"}
        if parts.scheme not in schemes or not parts.hostname:
            raise AppError(ErrorCode.INVALID_INPUT, f"URL not allowed: {url}")
        if not host_allowed(parts.hostname, self.allowed_domains):
            raise AppError(
                ErrorCode.INVALID_INPUT, f"domain not on the allow list: {parts.hostname}"
            )
        try:
            addresses = await asyncio.to_thread(self.resolver, parts.hostname)
        except OSError as exc:
            raise AppError(ErrorCode.UPSTREAM_ERROR, f"cannot resolve {parts.hostname}") from exc
        for addr in addresses:
            ip = ipaddress.ip_address(addr)
            if not ip.is_global:
                raise AppError(
                    ErrorCode.INVALID_INPUT, f"{parts.hostname} resolves to a private address"
                )

    async def _get(self, url: str) -> tuple[int, str, str, str | None]:
        async with self.client.stream("GET", url) as response:
            location = response.headers.get("location")
            if response.is_redirect:
                return response.status_code, "", "", location
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self.max_bytes:
                    break
            if response.status_code in (429, 500, 502, 503, 504):
                response.raise_for_status()
            ctype = response.headers.get("content-type", "")
            text = bytes(body[: self.max_bytes]).decode(response.encoding or "utf-8", "replace")
            return response.status_code, ctype, text, None

    async def fetch(self, url: str) -> Page:
        current = url
        for _ in range(self.max_redirects + 1):
            await self._check_url(current)
            status, ctype, body, location = await call_with_retry(
                self._get, current, config=self.resilience, op="fetcher.http"
            )
            if location is None:
                text = html_to_text(body) if "html" in ctype or not ctype else body
                return Page(current, status, text)
            current = urljoin(current, location)
        raise AppError(ErrorCode.UPSTREAM_ERROR, f"too many redirects from {url}")
