"""Usage metering + access control for the braidio MCP server.

The server runs with a **shared** ElevenLabs key, so the usage ledger is the only
record of who spent what — which makes identity and ledger integrity load-bearing.
Both live here as one FastMCP **middleware** (not per-tool decorators — a decorator
you forget on a paid tool means untracked spend; the middleware wraps *every* call,
which is what py2mcp's ``middleware=`` hook, i2mint/py2mcp#7, exists for).

Design (all **fail-closed**):

- **Identity is resolved ONCE**, here, from the verified OAuth token (its ``email``
  claim, else ``sub``). There is **no ambient fallback**: a served request without a
  token is rejected. A ``local_user`` may be set for local/stdio dev — an *explicit*
  opt-in, never inferred. The resolved email is put in a context var and the tools
  read it via :func:`current_email` (they never re-derive it), so authorization,
  metering, and per-user workspace all key off the same identity.
- **Authorization** — a caller not in ``allowed`` is rejected before any tool runs;
  a served server with no ``allowed`` and no ``allow_any_authenticated`` rejects
  everyone (deny by default).
- **Usage** — a write-ahead ledger entry is recorded *before* the call (if that
  write fails the call is **refused**, so money is never spent un-recordably), then
  updated with the outcome + ``cost_usd``. The ledger store is a ``MutableMapping``
  injected by the connector (a ``dol`` file store in prod; a ``dict`` in tests).
"""

from __future__ import annotations

import itertools
import time
from collections.abc import MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from braidio.mcp.credentials import redact_caller_key, scrub_caller_key
from braidio._paths import safe_component

#: Set by :class:`MeteringMiddleware` for the duration of a tool call; read by tools.
_CURRENT_EMAIL: ContextVar[Optional[str]] = ContextVar(
    "braidio_mcp_email", default=None
)


def token_email() -> Optional[str]:
    """The verified caller's email from the OAuth token (``email`` claim, else ``sub``).

    Lowercased, or ``None`` when there is no request/token context — deliberately
    **no fallback**, so a caller can be failed closed rather than silently handed a
    shared identity. (enlace_auth mints ``sub = email``; a generic IdP's ``email``
    claim is preferred when present.)
    """
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
    except Exception:  # noqa: BLE001 — no active request/token context
        return None
    if token is None:
        return None
    claims = getattr(token, "claims", None) or {}
    ident = claims.get("email") or claims.get("sub")
    return str(ident).lower() if ident else None


def current_email() -> str:
    """The caller's identity for the in-flight tool call (SSOT for per-user placement).

    Resolves from braidio's own ``MeteringMiddleware`` context var when present (stdio
    dev), else **directly from the verified OAuth token** — so tools work under ANY host
    middleware, including the shared ``enlace_metering.MeteringMiddleware`` the hosted
    connector now installs (thorwhalen/reelee#232), which keys its own context var. No
    fallback beyond the token: an unauthenticated call is failed closed.
    """
    email = _CURRENT_EMAIL.get() or token_email()
    if not email:
        raise ToolError("no caller identity (authentication required)")
    return email


@dataclass(frozen=True)
class UsageLedger:
    """Append-only usage/cost ledger over an injected ``MutableMapping``.

    One JSON entry per call, keyed ``{email}/{YYYY-MM}/{id}.json`` — a layout that
    answers "what did X spend this month" with a cheap subtree scan. The email
    component is traversal-checked (matching the workspace) so a hostile identity
    can't escape its ledger prefix. The store is DI'd so the ledger can grow up to
    blob storage without changing callers.
    """

    store: MutableMapping

    def record(self, entry: dict) -> str:
        email = safe_component(entry["email"], label="ledger email")
        key = f"{email}/{entry['month']}/{entry['id']}.json"
        self.store[key] = entry
        return key


def partial_receipt(exc: BaseException):
    """The ``weave_receipt`` a failed weave carries, through any wrapping.

    The host sees the tool's exception wrapped (fastmcp raises a ``ToolError``
    from it), so the chain of causes is walked; ``None`` when no link carries one.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        receipt = getattr(exc, "weave_receipt", None)
        if receipt is not None:
            return receipt
        exc = exc.__cause__ or exc.__context__
    return None


class MeteringMiddleware(Middleware):
    """FastMCP middleware: resolve + authorize the caller, then meter every call.

    ``allowed`` is an email allow-set; ``local_user`` enables local/dev mode (an
    explicit identity when there's no OAuth); ``allow_any_authenticated`` permits any
    token-authenticated caller when no ``allowed`` set is given. With none of these,
    a served server denies every call.
    """

    def __init__(
        self,
        ledger: UsageLedger,
        *,
        allowed: Optional[set[str]] = None,
        local_user: Optional[str] = None,
        allow_any_authenticated: bool = False,
    ):
        self.ledger = ledger
        self.allowed = {e.lower() for e in allowed} if allowed is not None else None
        self.local_user = local_user.lower() if local_user else None
        self.allow_any = allow_any_authenticated
        self._seq = itertools.count()

    def _authorize(self) -> str:
        """Resolve the caller (fail-closed) and check authorization. Returns the email."""
        email = token_email()
        if email is None:
            if self.local_user is None:
                raise ToolError("authentication required")
            email = self.local_user
        try:
            email = safe_component(email, label="caller identity")
        except ValueError as exc:
            raise ToolError(f"invalid caller identity: {exc}") from exc
        if self.allowed is not None:
            if email not in self.allowed:
                raise ToolError(
                    f"{email!r} is not authorized to use this braidio server"
                )
        elif not (self.local_user or self.allow_any):
            raise ToolError(
                "server has no allowlist and allow_any_authenticated is off — "
                "refusing (configure `allowed` or pass allow_any_authenticated=True)"
            )
        return email

    async def on_call_tool(self, context, call_next):
        email = self._authorize()
        token = _CURRENT_EMAIL.set(email)
        try:
            return await self._meter(context, call_next, email)
        finally:
            _CURRENT_EMAIL.reset(token)

    async def _meter(self, context, call_next, email: str):
        tool = context.message.name
        t0 = time.time_ns()
        now = datetime.now(timezone.utc)
        entry = {
            "id": f"{t0}-{next(self._seq)}-{tool}",
            "email": email,
            "month": now.strftime("%Y-%m"),
            "ts": now.isoformat(),
            "tool": tool,
            "status": "started",
        }
        # Write-ahead: refuse to run if we can't even record that it started — a
        # costed call must never spend on the shared key with no recoverable trace.
        try:
            self.ledger.record(entry)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                "usage ledger unavailable; refusing to run to avoid untracked spend"
            ) from exc
        scrubbed: BaseException | None = None
        try:
            result = await call_next(context)
            out = getattr(result, "structured_content", None)
            if isinstance(out, dict):
                for k in (
                    "cost_usd",
                    "cost_usd_actual",
                    "cache_hit_savings_usd",
                    "characters",
                    "cost_basis",
                ):
                    entry[k] = out.get(k)
                entry["artifact_ref"] = out.get("url") or out.get("path")
            entry["status"] = "done"
            entry["elapsed_s"] = (time.time_ns() - t0) / 1e9
            return result
        except Exception as exc:  # noqa: BLE001 — record the failure, then re-raise
            entry["status"] = "error"
            # a costed call can spend and THEN fail (a weave synthesizes, then
            # the mix fails): record what it spent, or the retry's reuse hides it
            partial = partial_receipt(exc)
            if partial is not None:
                entry["cost_usd_actual"] = partial.cost_usd_actual
                entry["cost_is_lower_bound"] = partial.has_unknown_costs
            # A provider echoes the key back on a 401, and this row is
            # persistent per user — redact while the request (and so the
            # caller's key) is still in flight (braidio#58 review).
            entry["error"] = redact_caller_key(repr(exc))
            entry["elapsed_s"] = (time.time_ns() - t0) / 1e9
            scrubbed = scrub_caller_key(exc)
            if scrubbed is exc:
                raise
        finally:
            # Best-effort final update; the write-ahead 'started' row is the trace.
            try:
                self.ledger.record(entry)
            except Exception:  # noqa: BLE001
                pass
        # Raised outside the handler so the dirty original is not its context;
        # fastmcp renders this text into the tool error the model sees.
        raise scrubbed
