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

Both URI tools resolve a pasted share link first: :func:`resolve_file_link`
returns the URL whose body is the file, and whether that URL is a *rewrite* of
the one given (a Dropbox ``dl=0`` or Drive ``/view`` link turned into its
download). Then each guards the bytes for what it expects:

- ``upload_asset``: :func:`refuse_page_or_archive` keeps a web page or an
  archive out of the asset library, always.
- ``ingest_document``: an HTML body is refused only when the link was
  rewritten, because then it is the host's sign-in or preview page; an
  un-rewritten URL's HTML may be the document itself (a plain web page, a
  Google Docs ``/pub`` or ``/export?format=html``). An archive is always
  refused (:func:`refuse_archive_document`): ``docx``/``epub``/``odt`` are ZIPs
  and :func:`extract_text` cannot read one.
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


#: What a fetched file is refused as: a web page (a share link that is not
#: public answers HTTP 200 with a sign-in page) or an archive (what a folder
#: share link downloads as). Sniffed, not read off the Content-Type, which a
#: share host gets wrong in both directions.
PAGE_OR_ARCHIVE_FAMILIES = frozenset({"html", "archive"})


def resolve_file_link(uri: str) -> tuple[str, bool]:
    """``(direct_url, rewritten)`` for a pasted ``uri``.

    ``direct_url`` is the URL whose body is the file: a Dropbox ``dl=0`` or
    Google Drive ``/view`` link is a web page about the file, and
    :func:`graze.share_links.resolve_share_url` turns it into the download (a
    plain URL, or one that already names a download or export, passes through).
    ``rewritten`` says whether it did: only then is an HTML body known to be the
    host's page rather than the file. A folder link is refused: it is many
    files, and what is asked for is one. ``ValueError`` names why.

    >>> resolve_file_link('https://drive.google.com/file/d/1AbC/view')
    ('https://drive.google.com/uc?export=download&id=1AbC', True)
    >>> resolve_file_link('https://docs.google.com/document/d/1X/pub')
    ('https://docs.google.com/document/d/1X/pub', False)
    """
    from graze.share_links import ShareLinkKind, resolve_share_url

    resolved = resolve_share_url(uri)  # pure: it answers, it does not raise
    if resolved.kind in (ShareLinkKind.ARCHIVE, ShareLinkKind.FOLDER):
        raise ValueError(
            f"{uri} is a {resolved.provider} folder link: link the file itself, "
            "or give its direct download URL"
        )
    if not resolved.direct_url:
        raise ValueError(
            f"{resolved.provider} link cannot be downloaded directly: {resolved.reason}"
        )
    # ``resolved.url`` is the input as resolved (whitespace-stripped)
    return resolved.direct_url, resolved.direct_url != resolved.url


def refuse_page_or_archive(
    data: bytes, *, uri: str | None = None, kind: str = "media"
) -> None:
    """``ValueError`` when ``data`` is a web page or an archive
    (:data:`PAGE_OR_ARCHIVE_FAMILIES`) where a ``kind`` file was expected.

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
    if sniff_content_family(head) in PAGE_OR_ARCHIVE_FAMILIES:
        try:
            assert_content_kind(head, expect_kind=kind, url=uri)
        except ContentKindMismatch as err:
            raise ValueError(str(err)) from err


def refuse_archive_document(data: bytes) -> None:
    """``ValueError`` when ``data`` is an archive where a document was expected.

    ``docx``, ``epub`` and ``odt`` are ZIPs, and :func:`extract_text` decodes
    bytes as text, so an archive is never a readable document, whatever link
    it came from.
    """
    from graze.content_kind import SNIFF_BYTES, sniff_content_family

    if sniff_content_family(data[:SNIFF_BYTES]) == "archive":
        raise ValueError(
            "this is an archive (zip/gzip), not a readable document: archives and "
            "ZIP-based formats (docx, epub, odt) are not supported; export it as "
            "PDF or plain text"
        )


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
