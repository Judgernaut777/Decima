"""Session auth, CSRF, and the high-risk reauth hook (Phase 8).

The API is a loopback daemon with authenticated BROWSER sessions. Authentication is
a two-step local pairing, not a remote login:

  1. a client presents the local ``pairing_secret`` (printed by the host / read from a
     local file) to ``login`` and receives a session — a secure, HTTP-only cookie plus
     a CSRF token;
  2. thereafter the cookie identifies the session, and every state-changing request
     must also echo the CSRF token in an ``X-CSRF-Token`` header (double-submit): a
     cross-site form post carries the cookie but cannot read the token, so it is
     rejected.

A high-risk approval (clearing a Morta gate) additionally requires a fresh REAUTH: the
client must re-present the pairing secret in an ``X-Reauth`` header for that one call.
This is the reauth hook — it does not weaken the kernel gate (the effect still runs the
authorization/approval path), it just proves a human is live at the approval moment.

Session tokens are random (``secrets``) and live only in memory; they are NEVER written
to the Weft, so invariant 6 (no unseeded random in RECORDED content) is untouched — the
recorded content is the deterministic Cells the command service asserts.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

COOKIE_NAME = "decima_session"


@dataclass
class Session:
    """A live browser session. ``token`` is the cookie value; ``csrf`` guards mutating
    requests; ``principal`` is the authenticated operator. ``seq`` is a logical, per-
    store counter (never wall-clock) used only for stable ordering in diagnostics.
    ``created_at``/``last_seen`` are stamps from the store's injected logical ``now`` used
    for TTL/idle-expiry; like ``seq`` they are process-local and never recorded content."""

    token: str
    csrf: str
    principal: str
    seq: int
    created_at: float = 0.0
    last_seen: float = 0.0
    data: dict = field(default_factory=dict)


class AuthError(Exception):
    """A fail-closed auth refusal carrying a stable ``reason_code`` and HTTP status."""

    def __init__(self, reason_code: str, http_status: int, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.http_status = http_status


UNAUTHENTICATED = "UNAUTHENTICATED"
CSRF_FAILED = "CSRF_FAILED"
REAUTH_REQUIRED = "REAUTH_REQUIRED"
BAD_PAIRING = "BAD_PAIRING"
LOGIN_THROTTLED = "LOGIN_THROTTLED"


class SessionStore:
    """In-memory session registry + the pairing/CSRF/reauth policy.

    Holds the loopback ``pairing_secret`` and mints/looks-up/drops sessions. Every
    comparison against the secret or a CSRF token uses ``hmac.compare_digest`` (constant
    time). The store is disposable process state, not canonical: losing it logs every
    browser out but destroys nothing on the Weft.

    It is BOUNDED: sessions carry an absolute TTL and an idle-expiry (both measured on the
    injected logical ``now``), the live set is capped (oldest evicted), and repeated failed
    ``login`` attempts trip a lockout. None of this timing touches recorded content."""

    def __init__(
        self,
        pairing_secret: str,
        *,
        secure_cookie: bool = True,
        now: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 12 * 3600,
        idle_seconds: float = 30 * 60,
        max_sessions: int = 64,
        max_login_failures: int = 5,
        lockout_seconds: float = 60.0,
    ) -> None:
        self._pairing = pairing_secret
        self._sessions: dict[str, Session] = {}
        self._seq = 0
        self.secure_cookie = secure_cookie
        # Logical clock seam: a monotonic ``now`` (seconds). Tests inject a fake clock
        # for determinism; it is used ONLY for in-memory session expiry and login
        # lockout timing, never for anything written to the Weft.
        self._now = now
        self._ttl_seconds = ttl_seconds
        self._idle_seconds = idle_seconds
        self._max_sessions = max_sessions
        self._max_login_failures = max_login_failures
        self._lockout_seconds = lockout_seconds
        self._failures = 0
        self._locked_until = 0.0

    # -- pairing / login ---------------------------------------------------
    def login(self, principal: str, pairing_secret: str | None) -> Session:
        """Exchange the local pairing secret for a session. Fails closed on a wrong
        secret (no session is created) and throttles brute force: after
        ``max_login_failures`` consecutive misses the store rejects every ``login``
        (even a correct secret) for ``lockout_seconds``."""
        now = self._now()
        if self._locked_until > now:
            raise AuthError(LOGIN_THROTTLED, 429, "too many failed login attempts")
        if not self._secret_ok(pairing_secret):
            self._failures += 1
            if self._failures >= self._max_login_failures:
                self._locked_until = now + self._lockout_seconds
                self._failures = 0
            raise AuthError(BAD_PAIRING, 401, "invalid pairing secret")
        self._failures = 0
        self._locked_until = 0.0
        self._prune(now)
        self._seq += 1
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf=secrets.token_urlsafe(32),
            principal=principal,
            seq=self._seq,
            created_at=now,
            last_seen=now,
        )
        self._sessions[session.token] = session
        self._evict_over_cap()
        return session

    def logout(self, token: str | None) -> None:
        if token is not None:
            self._sessions.pop(token, None)

    def get(self, token: str | None) -> Session | None:
        """Look up a live session, lazily expiring (and dropping) one past its TTL or
        idle window. A live lookup slides the idle window forward."""
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        now = self._now()
        if self._expired(session, now):
            del self._sessions[token]
            return None
        session.last_seen = now
        return session

    def _secret_ok(self, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(str(presented), self._pairing)

    def _expired(self, session: Session, now: float) -> bool:
        """A session is dead once it outlives the absolute TTL or the idle window."""
        return (now - session.created_at) >= self._ttl_seconds or (
            now - session.last_seen
        ) >= self._idle_seconds

    def _prune(self, now: float) -> None:
        """Drop every already-expired session (called before minting a new one)."""
        for token in [t for t, s in self._sessions.items() if self._expired(s, now)]:
            del self._sessions[token]

    def _evict_over_cap(self) -> None:
        """Enforce the session cap by evicting the lowest-``seq`` (oldest) sessions."""
        while len(self._sessions) > self._max_sessions:
            oldest = min(self._sessions.values(), key=lambda s: s.seq)
            del self._sessions[oldest.token]

    # -- per-request gates -------------------------------------------------
    def require_session(self, token: str | None) -> Session:
        """The authenticated session for a cookie token, or fail closed (401)."""
        session = self.get(token)
        if session is None:
            raise AuthError(UNAUTHENTICATED, 401, "no valid session")
        return session

    def check_csrf(self, session: Session, presented: str | None) -> None:
        """Double-submit CSRF: the header token must equal the session's CSRF token.
        Fails closed (403) on a missing or mismatched token."""
        if not presented or not hmac.compare_digest(str(presented), session.csrf):
            raise AuthError(CSRF_FAILED, 403, "missing or invalid CSRF token")

    def check_reauth(self, presented: str | None) -> None:
        """The reauth hook: a high-risk approval requires the pairing secret re-presented
        in this call (``X-Reauth``). Fails closed (401) otherwise — a stolen session
        cookie alone cannot clear a Morta gate."""
        if not self._secret_ok(presented):
            raise AuthError(REAUTH_REQUIRED, 401, "reauthentication required")

    # -- cookie helpers ----------------------------------------------------
    def cookie_header(self, session: Session) -> str:
        """A hardened Set-Cookie for the session: HttpOnly (no JS access), SameSite=
        Strict (no cross-site send), Path=/, and Secure when configured."""
        parts = [
            f"{COOKIE_NAME}={session.token}",
            "HttpOnly",
            "SameSite=Strict",
            "Path=/",
        ]
        if self.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    @staticmethod
    def clear_cookie_header() -> str:
        return f"{COOKIE_NAME}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"


def parse_cookie(cookie_header: str | None) -> dict[str, str]:
    """Parse a raw ``Cookie:`` header into a name→value dict (stdlib only)."""
    out: dict[str, str] = {}
    if not cookie_header:
        return out
    for part in cookie_header.split(";"):
        if "=" in part:
            name, _, value = part.strip().partition("=")
            out[name.strip()] = value.strip()
    return out
