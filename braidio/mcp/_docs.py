"""Document ingestion for the braidio MCP server: fetch a URI or accept text, then
extract plaintext so an agent can analyze source material into a Script.

Fetching is **SSRF-hardened** because the connector fetches user-supplied URLs
server-side on a shared production box. The guard is ``graze.guarded_fetch``'s (one
implementation for the fleet): http/https only on ports {80, 443}; every hop's
address is resolved once, checked as globally routable, and *pinned* — the socket
goes to exactly that address, so DNS rebinding cannot swap in an internal one; each
redirect hop is re-validated; the whole fetch is bounded by one wall-clock budget
and a byte cap. This module keeps only the braidio-facing contract: ``ValueError``
for every refusal (what the tools turn into a ``ToolError``) and a content type.

Extraction is content-type aware: HTML is stripped to text, PDF uses ``pypdf``
(page-capped), everything else is decoded as UTF-8.
"""

from __future__ import annotations

import io
import ipaddress
import urllib.parse
from html.parser import HTMLParser

import graze

#: Fetch bounds (whole fetch, all hops).
MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
TOTAL_TIMEOUT_S = 25.0  # wall-clock budget for the whole fetch
ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = frozenset({80, 443})
PDF_MAX_PAGES = 300

#: Identifies braidio to the origin server. Kept honest (no browser spoofing) so
#: operators can see, and rate-limit, what is fetching their documents.
USER_AGENT = "braidio-mcp/1.0"


class _TextHTMLParser(HTMLParser):
    """Collect visible text from HTML, skipping <script>/<style>."""

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def _host_is_public(host: str, *, port: int = 443, **policy) -> bool:
    """True only if every address ``host`` resolves to passes graze's public-only policy."""
    is_allowed = policy.get("is_allowed_address", graze.default_public_only)
    resolver = policy.get("resolver", graze.system_resolver)
    try:
        addresses = resolver(host, port)
    except OSError:
        return False
    return bool(addresses) and all(
        is_allowed(ipaddress.ip_address(a)) for a in addresses
    )


def _validate_target(uri: str) -> None:
    """Reject a URI whose scheme/host/port isn't a public http(s) endpoint.

    A pre-flight for callers that hand the URL to something else to fetch
    (``download_audio`` -> yt-dlp); it cannot pin the address, ``fetch_uri`` can.
    """
    parts = urllib.parse.urlparse(uri)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"only http/https URIs are supported, got {parts.scheme!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise ValueError(f"port {port} is not allowed (only 80/443)")
    if not parts.hostname or not _host_is_public(parts.hostname, port=port):
        raise ValueError(f"refusing to fetch a non-public host: {parts.hostname!r}")


def fetch_uri(uri: str, **fetch_options) -> tuple[bytes, str]:
    """Fetch a public http(s) URI (SSRF-hardened). Returns ``(bytes, content_type)``.

    ``fetch_options`` go to ``graze.guarded_open`` (``resolver``, ``connector``,
    ``is_allowed_address``, ...), overriding braidio's bounds; they are the test seam.
    """
    options = dict(
        allow_schemes=ALLOWED_SCHEMES,
        allow_ports=ALLOWED_PORTS,
        max_redirects=MAX_REDIRECTS,
        timeout=TOTAL_TIMEOUT_S,
        max_bytes=MAX_BYTES,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with graze.guarded_open(uri, **{**options, **fetch_options}) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            data = resp.read()
    except graze.UnsafeTarget as err:
        raise ValueError(f"refusing to fetch {uri!r}: {err}") from err
    except graze.HTTPStatusError as err:
        raise ValueError(f"fetch failed: HTTP {err.status}") from err
    except graze.GuardedFetchError as err:
        raise ValueError(f"fetch failed: {err}") from err
    return data, ctype.lower() or "application/octet-stream"


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ValueError("PDF ingestion needs pypdf (pip install pypdf)") from exc
    reader = PdfReader(io.BytesIO(data))
    pages = list(reader.pages)[:PDF_MAX_PAGES]  # cap work on an adversarial PDF
    return "\n\n".join((page.extract_text() or "") for page in pages).strip()


def extract_text(data: bytes, content_type: str) -> str:
    """Extract plaintext from ``data`` given its ``content_type``."""
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return _pdf_text(data)
    text = data.decode("utf-8", "replace")
    if "html" in ct or "xml" in ct:
        parser = _TextHTMLParser()
        parser.feed(text)
        return parser.text()
    return text
