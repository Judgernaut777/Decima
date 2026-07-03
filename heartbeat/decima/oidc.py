"""Real OIDC token exchange — wrap the auth provider, never roll your own auth.

Dependency policy: recreate the design in pure stdlib, but WRAP the real engine for
high-liability domains. Authentication is the textbook case — you must never
reimplement an identity provider. IDENTITY1 already turns a provider attestation into
an attenuated capability on the Weft (mint principal → clamp scope to what the provider
attested → hold the token in CRED1 → record a session); what was a STUB is the exchange
itself (caller-supplied token + scope). This makes that real.

`exchange_code` performs the OAuth2/OIDC **authorization-code → token** exchange against
the provider's real token endpoint over stdlib `urllib` (zero pip deps, transport seam
so the offline oracle runs it with no network). The provider's response carries the
**actual granted scope** — so Decima's issued capability is clamped to what the provider
REALLY attested, not to a value the caller made up. `login` composes the exchange with
IDENTITY1's capability minting.

GUARDRAILS (mirroring the Stripe rail):
  - **HTTPS-only** — refuses to send the client secret to a non-`https://` endpoint
    (the auth analogue of Stripe's test-mode guard: never leak the secret in cleartext).
  - **client secret via CRED1** — applied INSIDE the broker (`use_secret`), never
    returned, never logged, never on the Weft; the access token likewise goes straight
    into CRED1 via IDENTITY1.
  - **fail closed** — a failed/declined exchange mints NO session and NO capability.

Composes public identity / secrets APIs. No core edit.
"""
import json
from urllib.parse import urlencode

from decima import identity as _identity


class OIDCError(Exception):
    """A token-exchange failure — no session may be minted (fail closed)."""


def _urllib_transport(url: str, headers: dict, body: str):
    """(Phase 2 · GO LIVE) FAIL-CLOSED default — the bare stdlib socket default is
    GONE: the armed wire guard (decima/wire.py) refuses ungated egress anyway, so
    `transport=None` on the live path now refuses HERE, first, with the sanctioned
    path named. Build the wire-gated transport via
    `live_wire.gated_transport(k, agent_cell, cap_id)`
    (a granted, Morta-approved egress capability) and inject it as `transport=`.
    Injected fake transports (the offline oracle, every test-mode path) never
    resolve to this default and are unaffected."""
    from decima import live_wire
    raise live_wire.NoGatedTransport(
        "oidc", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def exchange_code(token_endpoint: str, *, code: str, client_id: str, client_secret: str,
                  redirect_uri: str, transport=None) -> dict:
    """Exchange an authorization `code` for tokens at the provider's token endpoint.
    Returns the parsed token response (must contain `access_token`). Raises `OIDCError`
    on a non-HTTPS endpoint, an unreachable endpoint, or a declined exchange."""
    transport = transport or _urllib_transport
    if not str(token_endpoint).startswith("https://"):
        # Never put the client secret on the wire in cleartext.
        raise OIDCError("refusing to send the client secret to a non-HTTPS token endpoint")
    body = urlencode({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri, "client_id": client_id,
        "client_secret": client_secret,
    })
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    try:
        status, resp = transport(token_endpoint, headers, body)
    except Exception as e:
        raise OIDCError(f"token endpoint unreachable: {e}")
    if not isinstance(resp, dict):
        raise OIDCError(f"unparseable token response (status {status})")
    if status == 200 and resp.get("access_token"):
        return resp
    err = resp.get("error_description") or resp.get("error") or f"http {status}"
    raise OIDCError(f"token exchange declined: {err}")


def login(k, provider: str, *, token_endpoint: str, code: str, client_id: str,
          client_secret_handle: str, broker, agent_cell, redirect_uri: str,
          grants, transport=None, idp=None) -> dict:
    """Real OIDC authorization-code login. Exchanges `code` at the provider's real token
    endpoint (the client secret applied inside CRED1, never disclosed), then mints an
    attenuated Decima capability from the provider's ACTUAL granted scope (IDENTITY1) —
    a request wider than the provider attested is clamped out.

    Returns {session, subject, scope, clamped, provider_scope} on success, or
    {denied: reason} on a failed exchange / denied credential (no session minted)."""
    idp = idp or _identity._provider_for(k)
    try:
        r = broker.use_secret(
            agent_cell, client_secret_handle,
            lambda secret: exchange_code(token_endpoint, code=code, client_id=client_id,
                                         client_secret=secret, redirect_uri=redirect_uri,
                                         transport=transport))
    except OIDCError as e:
        return {"denied": f"oidc: {e}"}                # fail closed — no session minted
    if "denied" in r:
        return {"denied": r["denied"]}                # credential handle revoked/unauthorized
    resp = r["ok"]
    subject = resp.get("sub") or resp.get("subject")
    if not subject:
        return {"denied": "oidc: token response carried no subject (sub)"}
    provider_scope = str(resp.get("scope", "")).split()   # the provider's REAL granted scope
    access_token = resp["access_token"]
    session = idp.login(provider, subject, grants=list(grants), token=access_token,
                        provider_scope=provider_scope)
    who = idp.whoami(session)
    return {"session": session, "subject": subject, "scope": who["scope"],
            "clamped": who["clamped"], "provider_scope": provider_scope}
