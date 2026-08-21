"""Network-boundary URL validation with SSRF and DNS-rebinding defenses."""

from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import re
import socket
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit


class URLVerificationError(RuntimeError):
    pass


class UnsafeURLError(URLVerificationError):
    pass


class URLFetchError(URLVerificationError):
    pass


@dataclass(frozen=True, slots=True)
class URLPolicy:
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    allowed_content_types: frozenset[str] = frozenset(
        {
            "text/html",
            "text/plain",
            "application/pdf",
            "application/json",
            "application/xml",
            "text/xml",
        }
    )
    max_redirects: int = 5
    max_response_bytes: int = 10 * 1024 * 1024
    timeout_seconds: float = 10.0
    user_agent: str = "PaperCraftAI-SourceVerifier/1.0"


@dataclass(frozen=True, slots=True)
class SafeTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes = b""
    peer_ip: str | None = None


class HTTPTransport(Protocol):
    def request(
        self,
        url: str,
        *,
        approved_ips: Sequence[str],
        timeout: float,
        max_bytes: int,
        headers: Mapping[str, str],
    ) -> HTTPResponse: ...


Resolver = Callable[..., Sequence[str]]


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_global


def _default_resolver(hostname: str, port: int) -> tuple[str, ...]:
    addresses = {
        str(result[4][0])
        for result in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    }
    return tuple(sorted(addresses))


def _resolve(resolver: Resolver, hostname: str, port: int) -> tuple[str, ...]:
    try:
        values = resolver(hostname, port)
    except TypeError:
        values = resolver(hostname)
    return tuple(dict.fromkeys(str(value) for value in values))


def normalize_url(url: str) -> str:
    """Normalize a web URL without resolving or fetching it."""

    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL is empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise UnsafeURLError("Control characters in URLs are forbidden")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as error:
        raise UnsafeURLError(f"Malformed URL: {error}") from error
    scheme = parts.scheme.casefold()
    if not scheme or not parts.hostname:
        raise UnsafeURLError("URL must contain a scheme and hostname")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("Credentials in URLs are forbidden")
    try:
        hostname = parts.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise UnsafeURLError("Hostname cannot be encoded as IDNA") from error
    if "%" in hostname or "\\" in hostname:
        raise UnsafeURLError("Encoded or backslash hostnames are forbidden")
    is_ipv6 = ":" in hostname
    rendered_host = f"[{hostname}]" if is_ipv6 else hostname
    default_port = 443 if scheme == "https" else 80
    netloc = rendered_host if port in (None, default_port) else f"{rendered_host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, connect_ip: str, port: int, timeout: float) -> None:
        self._ssl_context = ssl.create_default_context()
        super().__init__(hostname, port=port, timeout=timeout, context=self._ssl_context)
        self._connect_ip = connect_ip

    def connect(self) -> None:
        raw_socket = socket.create_connection((self._connect_ip, self.port), self.timeout)
        self.sock = self._ssl_context.wrap_socket(raw_socket, server_hostname=self.host)


class PinnedHTTPTransport:
    """Transport that connects to an already-approved IP, not a second DNS result."""

    def request(
        self,
        url: str,
        *,
        approved_ips: Sequence[str],
        timeout: float,
        max_bytes: int,
        headers: Mapping[str, str],
    ) -> HTTPResponse:
        if not approved_ips:
            raise URLFetchError("No approved address supplied to transport")
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        selected_ip = approved_ips[0]
        connection: http.client.HTTPConnection
        if parts.scheme == "https":
            connection = _PinnedHTTPSConnection(hostname, selected_ip, port, timeout)
        else:
            connection = http.client.HTTPConnection(selected_ip, port=port, timeout=timeout)
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        request_headers = dict(headers)
        default_port = 443 if parts.scheme == "https" else 80
        rendered_host = f"[{hostname}]" if ":" in hostname else hostname
        request_headers["Host"] = rendered_host if port == default_port else f"{rendered_host}:{port}"
        try:
            connection.request("GET", path, headers=request_headers)
            response = connection.getresponse()
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise URLFetchError(f"Response exceeds {max_bytes} bytes")
            response_headers = {key.casefold(): value for key, value in response.getheaders()}
            return HTTPResponse(response.status, response_headers, body, selected_ip)
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise URLFetchError(f"Request failed: {error}") from error
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class URLVerificationResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    content_length: int
    content_sha256: str
    verified: bool
    redirects: tuple[str, ...] = ()
    checked_ips: tuple[str, ...] = ()
    title: str | None = None
    warnings: tuple[str, ...] = ()


class URLVerifier:
    def __init__(
        self,
        *,
        policy: URLPolicy | None = None,
        resolver: Resolver | None = None,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.policy = policy or URLPolicy()
        self.resolver = resolver or _default_resolver
        self.transport = transport or PinnedHTTPTransport()

    def check_safety(self, url: str) -> SafeTarget:
        normalized = normalize_url(url)
        parts = urlsplit(normalized)
        if parts.scheme not in self.policy.allowed_schemes:
            raise UnsafeURLError(f"URL scheme {parts.scheme!r} is not allowed")
        if parts.username is not None or parts.password is not None:
            raise UnsafeURLError("Credentials in URLs are forbidden")
        hostname = parts.hostname or ""
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".localhost", ".local", ".internal")):
            raise UnsafeURLError("Local hostnames are forbidden")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in self.policy.allowed_ports:
            raise UnsafeURLError(f"Port {port} is not allowed")

        try:
            literal = ipaddress.ip_address(hostname.split("%", 1)[0])
        except ValueError:
            addresses = _resolve(self.resolver, hostname, port)
        else:
            addresses = (str(literal),)
        if not addresses:
            raise UnsafeURLError("Hostname did not resolve")
        forbidden = [address for address in addresses if not _public_ip(address)]
        if forbidden:
            raise UnsafeURLError(f"Hostname resolves to a non-public address: {forbidden[0]}")
        return SafeTarget(normalized, hostname, port, tuple(sorted(set(addresses))))

    def is_safe_url(self, url: str) -> bool:
        try:
            self.check_safety(url)
        except URLVerificationError:
            return False
        return True

    def verify(self, url: str) -> URLVerificationResult:
        requested = normalize_url(url)
        current = requested
        redirects: list[str] = []
        checked_ips: list[str] = []
        for redirect_number in range(self.policy.max_redirects + 1):
            # Resolve immediately before each request. The transport then pins
            # the connection to one of these exact addresses.
            target = self.check_safety(current)
            checked_ips.extend(target.addresses)
            response = self.transport.request(
                target.url,
                approved_ips=target.addresses,
                timeout=self.policy.timeout_seconds,
                max_bytes=self.policy.max_response_bytes,
                headers={"User-Agent": self.policy.user_agent, "Accept": "text/html,application/pdf,text/plain;q=0.8,*/*;q=0.1"},
            )
            if response.peer_ip is None or response.peer_ip not in target.addresses:
                raise UnsafeURLError("Connected peer is not one of the approved DNS addresses")
            headers = {key.casefold(): value for key, value in response.headers.items()}
            if response.status_code in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    raise URLFetchError("Redirect response has no Location header")
                if redirect_number >= self.policy.max_redirects:
                    raise URLFetchError("Too many redirects")
                current = normalize_url(urljoin(target.url, location))
                # Safety is deliberately checked at the start of the next hop.
                redirects.append(current)
                continue

            content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold() or None
            warnings: list[str] = []
            if content_type and content_type not in self.policy.allowed_content_types:
                warnings.append(f"unexpected-content-type:{content_type}")
            title = self._extract_title(response.body, content_type)
            verified = 200 <= response.status_code < 400 and not warnings
            return URLVerificationResult(
                requested_url=requested,
                final_url=target.url,
                status_code=response.status_code,
                content_type=content_type,
                content_length=len(response.body),
                content_sha256=hashlib.sha256(response.body).hexdigest(),
                verified=verified,
                redirects=tuple(redirects),
                checked_ips=tuple(dict.fromkeys(checked_ips)),
                title=title,
                warnings=tuple(warnings),
            )
        raise URLFetchError("Redirect loop")

    @staticmethod
    def _extract_title(body: bytes, content_type: str | None) -> str | None:
        if content_type != "text/html" or not body:
            return None
        head = body[:256_000].decode("utf-8", errors="replace")
        match = re.search(r"<title\b[^>]*>(.*?)</title\s*>", head, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        title = html.unescape(
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()
        )
        return title[:500] or None


def verify_url(
    url: str,
    *,
    policy: URLPolicy | None = None,
    resolver: Resolver | None = None,
    transport: HTTPTransport | None = None,
) -> URLVerificationResult:
    return URLVerifier(policy=policy, resolver=resolver, transport=transport).verify(url)


__all__ = [
    "HTTPResponse",
    "HTTPTransport",
    "PinnedHTTPTransport",
    "SafeTarget",
    "URLFetchError",
    "URLPolicy",
    "URLVerificationError",
    "URLVerificationResult",
    "URLVerifier",
    "UnsafeURLError",
    "normalize_url",
    "verify_url",
]
