"""Real OIDC login — wrap the auth provider (dependency policy: never roll your own auth).

IDENTITY1 mints an attenuated capability from a provider attestation; this makes the
STUB exchange real. `oidc.exchange_code` does the OAuth2 authorization-code → token
exchange at the provider's real token endpoint over stdlib `urllib` (zero deps), and the
provider's ACTUAL granted scope drives the clamp — Decima cannot issue authority wider
than the provider attested. Driven entirely OFFLINE here via an injected transport.

This check proves:
  - success: a real exchange mints a session + capability; the ISSUED scope is the
    provider's granted scope ∩ the request (anything the provider did not grant is
    clamped out) — the liability-bearing decision comes from the provider, not the caller;
  - the issued capability authorizes a granted scope and denies a clamped one (ocap);
  - HTTPS-only: a non-HTTPS token endpoint is refused before the secret goes on the wire;
  - fail closed: a declined exchange (invalid_grant) mints NO session and NO capability;
  - dispense-don't-disclose: neither the client secret nor the access token ever appears
    on the Weft (CRED1 applies the secret inside the broker; IDENTITY1 holds the token);
  - revoke: logout fails the issued capability closed.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import oidc, identity
from decima.secrets import SecretsBroker

CLIENT_SECRET = "GOCSPX-super-secret-value"
ACCESS_TOKEN = "ya29.real-access-token-XYZ"
ENDPOINT = "https://accounts.example.com/token"


def _transport(calls, response):
    def t(url, headers, body):
        calls.append({"url": url, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL OIDC LOGIN (wrapped auth provider, offline) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = SecretsBroker(kk)
    idp = identity.IdentityProvider(kk, broker=broker)
    broker.store("google_client_secret", CLIENT_SECRET, service="google")
    handle = broker.issue("google_client_secret", _decima(kk), "oidc token exchange")

    # 1. SUCCESS — provider's real scope drives the clamp. ─────────────────────────────
    calls = []
    ok_resp = (200, {"access_token": ACCESS_TOKEN, "id_token": "eyJ.stub.jwt",
                     "token_type": "Bearer", "sub": "user-123", "scope": "read write"})
    res = oidc.login(kk, "google", token_endpoint=ENDPOINT, code="authcode-1",
                     client_id="client-abc", client_secret_handle=handle, broker=broker,
                     agent_cell=_decima(kk), redirect_uri="https://app.example/cb",
                     grants=["read", "write", "admin"], transport=_transport(calls, ok_resp), idp=idp)
    assert "session" in res, res
    assert res["scope"] == ["read", "write"] and res["clamped"] == ["admin"], res
    assert len(calls) == 1 and "grant_type=authorization_code" in calls[0]["body"], calls
    who = idp.whoami(res["session"])
    assert who["provider"] == "google" and who["subject"] == "user-123" and who["active"], who
    line("  success: real code→token exchange; issued scope = provider's grant ∩ request "
         "(admin clamped — provider decides, not the caller) ✓")

    # 2. The issued capability authorizes a granted scope, denies a clamped one. ───────
    ok_read, _ = idp.authorized(res["session"], "read")
    no_admin, why = idp.authorized(res["session"], "admin")
    assert ok_read and not no_admin, (ok_read, no_admin, why)
    line("  ocap: session authorizes 'read' (granted) and denies 'admin' (clamped) ✓")

    # 3. HTTPS-only — the client secret never goes on a cleartext wire. ────────────────
    http_calls = []
    bad = oidc.login(kk, "google", token_endpoint="http://accounts.example.com/token",
                     code="c", client_id="client-abc", client_secret_handle=handle,
                     broker=broker, agent_cell=_decima(kk), redirect_uri="https://app.example/cb",
                     grants=["read"], transport=_transport(http_calls, ok_resp), idp=idp)
    assert "denied" in bad and "HTTPS" in bad["denied"], bad
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS token endpoint is refused before the secret is sent ✓")

    # 4. FAIL CLOSED — a declined exchange mints NO session / capability. ──────────────
    sessions_before = len([c for c in kk.weave().of_type(identity.IDENTITY)])
    caps_before = len(kk.weave().of_type("capability"))
    declined = oidc.login(kk, "google", token_endpoint=ENDPOINT, code="stolen-code",
                          client_id="client-abc", client_secret_handle=handle, broker=broker,
                          agent_cell=_decima(kk), redirect_uri="https://app.example/cb",
                          grants=["read"],
                          transport=_transport([], (400, {"error": "invalid_grant",
                                                          "error_description": "bad code"})), idp=idp)
    assert "denied" in declined and "declined" in declined["denied"], declined
    assert len([c for c in kk.weave().of_type(identity.IDENTITY)]) == sessions_before
    assert len(kk.weave().of_type("capability")) == caps_before, "no capability on a failed exchange"
    line("  fail closed: declined exchange (invalid_grant) mints NO session or capability ✓")

    # 5. DISPENSE-DON'T-DISCLOSE — secret and token never on the Weft. ────────────────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert CLIENT_SECRET not in payloads, "client secret must never be on the Weft"
    assert ACCESS_TOKEN not in payloads, "access token must never be on the Weft (held by CRED1)"
    line("  no client secret and no access token on the Weft (CRED1 holds both) ✓")

    # 6. REVOKE — logout fails the issued capability closed. ──────────────────────────
    idp.logout(res["session"])
    still, why2 = idp.authorized(res["session"], "read")
    assert not still, "a logged-out session must fail closed"
    line("  revoke: logout → the issued capability fails closed ✓")

    line("  → auth is wrapped, not reinvented: the provider's real token exchange (over "
         "stdlib urllib) decides the scope; Decima mints an attenuated capability, holds "
         "both secrets in CRED1, refuses cleartext, and fails closed.")
