"""IDENTITY1 — identity / SSO as capability issuance (CAPABILITY_MAP B1).

Proves: an SSO login MINTS a scoped capability on the Weft (attenuated to exactly
the provider grant; a request to WIDEN past the provider scope is clamped down);
the issued capability works while live; logout/revoke kills it AND the broker
handle → it fails closed; the external bearer token never appears in any Cell.

Runs on its OWN fresh Kernel (it mints an IdP principal + user agents and forges
the issued capabilities). Contract: run(k, line). Fail loud.
"""
import json
import os
import tempfile

from decima import identity
from decima.kernel import Kernel


def _token_on_weft(k, raw: str) -> bool:
    """Does the external bearer token appear ANYWHERE in the signed log? It must not."""
    for _seq, payload in k.weft.db.execute("SELECT seq, payload FROM events"):
        if raw in payload:
            return True
    return False


def run(_k, line):
    line("\n== IDENTITY / SSO (external grant → attenuated capability minted on the Weft) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    TOKEN = "oidc-bearer-google-ada@example.com-S3CR3T_DEADBEEF"

    # ---- login: provider attests scope; we request EXACTLY it ---------------
    sid = identity.login(k, "google", "ada@example.com",
                         grants=["calendar.read", "email.read"], token=TOKEN)
    who = identity.whoami(k, sid)
    assert who["provider"] == "google" and who["subject"] == "ada@example.com"
    assert who["active"] and not who["capability_revoked"]
    assert set(who["scope"]) == {"calendar.read", "email.read"}, who["scope"]

    # The issued authority is a real capability Cell on the Weft, bound to a principal.
    cap = k.weave().get(who["capability"])
    assert cap is not None and cap.type == "capability"
    assert cap.content["grantee"] == who["principal"], "cap not bound to the user principal"
    assert cap.content["parent"], "issued cap is not attenuated from a provider grant"
    line(f"  login(google, ada) → principal {who['principal'][:8]} minted; "
         f"capability {who['capability'][:8]} on the Weft, scope={sorted(who['scope'])}")

    # The external token is NEVER on the Weft and NEVER in whoami.
    assert not _token_on_weft(k, TOKEN), "external bearer token leaked onto the Weft"
    assert TOKEN not in json.dumps(who), "token leaked through whoami"
    line(f"  external bearer token held by CRED1 broker (handle {k.weave().get(sid).content['handle'][:8]}); "
         f"raw token on Weft: {_token_on_weft(k, TOKEN)}; in whoami: {TOKEN in json.dumps(who)}")

    # ---- using it works while the session is live ---------------------------
    ok, why = identity.authorized(k, sid, "calendar.read")
    assert ok, f"live session should authorize an in-scope action: {why}"
    no, why2 = identity.authorized(k, sid, "calendar.write")
    assert not no, "out-of-scope action must be denied even while live"
    line(f"  live session authorizes 'calendar.read' ✓ ; out-of-scope "
         f"'calendar.write' denied ({why2}) ✓")

    # ---- a request to WIDEN past the provider scope is CLAMPED DOWN ---------
    # The provider attests only {calendar.read}; the app greedily asks for admin too.
    sid2 = identity.login(k, "github", "bob",
                          grants=["repo.read", "repo.admin", "org.delete"],
                          provider_scope=["repo.read"], token="oidc-bearer-github-bob-QQQQ")
    who2 = identity.whoami(k, sid2)
    assert set(who2["scope"]) == {"repo.read"}, ("widen not clamped", who2["scope"])
    assert set(who2["clamped"]) == {"org.delete", "repo.admin"}, who2["clamped"]
    wok, _ = identity.authorized(k, sid2, "repo.read")
    wno, _ = identity.authorized(k, sid2, "repo.admin")
    assert wok and not wno, "clamped scope must not authorize the widened grant"
    line(f"  asked repo.read+repo.admin+org.delete, provider attested only repo.read → "
         f"issued scope={sorted(who2['scope'])}, clamped={sorted(who2['clamped'])} (never widened) ✓")

    # ---- logout / revoke: the cap AND the broker handle fail closed ---------
    handle_id = k.weave().get(sid).content["handle"]
    identity.logout(k, sid)
    who_after = identity.whoami(k, sid)
    assert not who_after["active"] and who_after["capability_revoked"], "session not killed"
    rok, rwhy = identity.authorized(k, sid, "calendar.read")
    assert not rok and "revoked" in rwhy.lower(), ("cap did not fail closed", rwhy)
    # The broker handle to the external token is revoked too (fails closed).
    decima_agent = k.weave().get(k.weave().get(sid).content["agent"])
    hres = identity._provider_for(k).broker.use(decima_agent, handle_id, {"op": "refresh"})
    assert "denied" in hres and "revoked" in hres["denied"].lower(), ("handle not failed closed", hres)
    line(f"  logout(ada) → capability DENIED ({rwhy}); broker handle DENIED "
         f"({hres['denied']}) → both fail closed ✓")

    # ---- final: across the whole run the external token never hit the Weft ---
    assert not _token_on_weft(k, TOKEN)
    line("  → SSO login = an explicit, attenuated capability minted on the Weft "
         "(never ambient); the external token never appeared in any Cell ✓")
