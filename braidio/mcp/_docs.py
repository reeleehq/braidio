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


def _host_is_public(host: str, port: int) -> bool:
    """True only if every address ``host`` resolves to passes graze's public-only policy."""
    try:
        addresses = graze.system_resolver(host, port)
    except OSError:
        return False
    return bool(addresses) and all(
        graze.default_public_only(ipaddress.ip_address(a)) for a in addresses
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
    if not parts.hostname or not _host_is_public(parts.hostname, port):
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
        raise ValueError(str(err)) from err
    except graze.HTTPStatusError as err:
        raise ValueError(f"fetch failed: HTTP {err.status}") from err
    except graze.GuardedFetchError as err:
        raise ValueError(f"fetch failed: {err}") from err
    return data, ctype.lower() or "application/octet-stream"


#: What a library asset may never be, whatever its declared kind: a web page
#: (a share link that is not public answers HTTP 200 with a sign-in page) or an
#: archive (what a folder share link downloads as). Sniffed, not read off the
#: Content-Type, which a share host gets wrong in both directions.
NOT_MEDIA_FAMILIES = frozenset({"html", "archive"})


def direct_media_url(uri: str) -> str:
    """The URL whose body is the file a pasted ``uri`` names.

    A Dropbox ``dl=0`` or Google Drive ``/view`` link is a web page about the
    file; :func:`graze.share_links.resolve_share_url` turns it into the
    download (a plain URL passes through). A folder link is refused: it is
    many files, and an asset is one. ``ValueError`` names why.
    """
    from graze.share_links import (
        ShareLinkKind,
        ShareLinkResolutionError,
        resolve_share_url,
    )

    try:
        resolved = resolve_share_url(uri)
    except ShareLinkResolutionError as err:
        raise ValueError(str(err)) from err
    if resolved.kind in (ShareLinkKind.ARCHIVE, ShareLinkKind.FOLDER):
        raise ValueError(
            f"{uri} is a {resolved.provider} folder link; share the file itself "
            "(one asset is one file)"
        )
    if not resolved.direct_url:
        raise ValueError(
            f"{resolved.provider} link cannot be downloaded directly: {resolved.reason}"
        )
    return resolved.direct_url


def refuse_non_media(data: bytes, *, uri: str | None = None) -> None:
    """``ValueError`` when ``data`` is a web page or an archive (:data:`NOT_MEDIA_FAMILIES`).

    graze's message is passed on as is: it names the usual cause (a share link
    that is not public) and the fix (anyone-with-the-link).
    """
    from graze.content_kind import (
        SNIFF_BYTES,
        ContentKindMismatch,
        assert_content_kind,
        sniff_content_family,
    )

    head = data[:SNIFF_BYTES]
    if sniff_content_family(head) in NOT_MEDIA_FAMILIES:
        try:
            assert_content_kind(head, expect_kind="media", url=uri)
        except ContentKindMismatch as err:
            raise ValueError(str(err)) from err


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
