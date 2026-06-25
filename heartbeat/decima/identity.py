"""IDENTITY1 — identity / SSO as capability issuance (CAPABILITY_MAP B1).

The OS thesis: an external login is NOT a special ambient session bit. It is a
**capability mint on the Weft**. When a provider (Google, GitHub, an OIDC IdP)
attests "this subject is who they say, and here is what they may do", Decima turns
that attestation into an *explicit, attenuated* capability — a Cell (authority is
data, Law 2) — bound to a freshly-minted user principal, scoped to *exactly* what
the provider granted and never one selector broader.

The shape:

  • login(k, provider, subject, *, grants) — a stub OIDC/SSO exchange. It
      - mints the user's principal and an agent Cell bound to it;
      - hands the **external bearer token to the CRED1 secrets broker** — a scoped
        handle is issued, the raw token never lands on the Weft or in a return;
      - mints a `provider grant` capability carrying the provider's FULL authorized
        scope, then **attenuates it down to `grants`** (`attenuate` +
        `attenuation_valid`). A request for authority WIDER than the provider
        attested is clamped to the provider's scope — never widened;
      - records a `session` (identity) Cell linking principal ↔ provider-identity,
        carrying the issued capability id and the broker handle id (never the token).

  • whoami(k, session) — the bound identity (provider/subject/principal) plus the
      issued capability (effect, target, caveats). No secret.

  • logout(k, session) / revoke(k, session) — RETRACT the issued capability AND the
      broker handle. Authority then **fails closed**: `authorize` denies the cap
      ('capability revoked'), and the handle (and anything attenuated from it) too.

Composes ONLY public capability / secrets / kernel / weft APIs. Touches no core
file. ints not floats; every step audited as Cells on the Weft.
"""
from decima.capability import (capability_content, attenuate as _attenuate,
                               attenuation_valid, authorize)
from decima.secrets import SecretsBroker
from decima.model import assert_content
from decima.weft import RETRACT
from decima.hashing import content_id, nfc

# Cell types this module authors on the Weft.
IDENTITY = "identity"            # the session/identity Cell: principal ↔ provider-identity
SSO_LOGIN = "sso_login"         # an audit receipt for a login / logout event

# The effect class an SSO-issued capability carries. It is an ordinary effect, so
# it flows through the SAME ocap machinery (authorize / attenuate) as everything
# else — identity is not a privileged side-channel.
SSO_EFFECT = "sso.scope"


class IdentityProvider:
    """A stub OIDC/SSO broker: it turns a provider attestation into an attenuated
    capability minted on the Weft, holds the external token in the CRED1 broker, and
    records the principal ↔ provider-identity binding as a Cell. One per realm."""

    def __init__(self, k, broker: SecretsBroker | None = None):
        self.k = k
        # The IdP is its own principal: it signs the user-agent assertion, the
        # provider-grant + attenuated capability, the identity Cell, and the audits.
        self.principal = k.keyring.mint("identity-provider", "broker")
        # Reuse / own a CRED1 broker so the external token is held opaquely.
        self.broker = broker or SecretsBroker(k)

    # -- login: attest → mint principal → attenuate scope → bind on the Weft ---
    def login(self, provider: str, subject: str, *, grants: list[str],
              token: str, provider_scope: list[str] | None = None) -> str:
        """Stub OIDC/SSO auth for `subject` at `provider`, requesting `grants`.

        `provider_scope` is what the provider ACTUALLY authorized (defaults to
        `grants` — i.e. the provider granted exactly what was asked). The issued
        capability is attenuated to `grants ∩ provider_scope`: a request for a
        scope the provider did not attest is clamped OUT, never widened.

        `token` is the external bearer/ID token — it goes straight into the CRED1
        broker and is never returned, never written to the Weft.

        Returns the id of the `session`/`identity` Cell.
        """
        provider, subject = nfc(provider), nfc(subject)
        requested = [nfc(g) for g in grants]
        attested = [nfc(s) for s in (provider_scope if provider_scope is not None
                                     else requested)]

        # 1) Mint the user's principal and bind an agent Cell to it (its own key).
        user = self.k.keyring.mint(f"{provider}:{subject}", "human")
        agent_id = content_id({"identity_agent": provider, "sub": subject})
        assert_content(self.k.weft, self.principal.id, agent_id, "agent", {
            "principal": user.id,
            "objective": f"act as {subject} via {provider}",
            "envelope": [],
            "sandbox": False,
        })

        # 2) Hand the external token to the CRED1 broker — a handle, never the raw
        #    value. The token never touches the Weft.
        cred_name = f"oidc:{provider}:{subject}"
        self.broker.store(cred_name, token, alias=subject, service=provider)
        user_agent = self.k.weave().get(agent_id)
        handle_id = self.broker.issue(cred_name, user_agent,
                                      purpose=f"refresh {provider} session")

        # 3) Mint the PROVIDER GRANT carrying the provider's full attested scope,
        #    then ATTENUATE it down to exactly `grants`. The attenuation path can
        #    only narrow — a request to widen past `attested` is structurally
        #    clamped to the provider's scope (attenuation_valid rejects a widen).
        effective = [g for g in requested if g in attested]   # clamp: ⊆ attested
        clamped = sorted(set(requested) - set(attested))      # what was asked but denied
        if not effective:
            # Asked for nothing the provider attested → fall back to the provider's
            # own scope (still never broader than the provider attested).
            effective = list(attested)

        # The provider grants its full attested scope TO the IdP (the IdP is the
        # relying party that holds the provider's authority); the IdP then
        # attenuates a downhill grant to the USER. So the chain reads
        # provider→IdP→user, each hop granter == parent.grantee (granter-held).
        grant = capability_content(
            name=f"sso:{provider}:{subject}", effect=SSO_EFFECT,
            target="*", caveats={"scope": sorted(attested), "provider": provider,
                                 "subject": subject},
            grantee=self.principal.id, granter=self.principal.id)
        grant_id = content_id({"sso_grant": provider, "sub": subject})
        assert_content(self.k.weft, self.principal.id, grant_id, "capability", grant)

        issued = _attenuate(grant, {"scope": sorted(effective)}, grant_id,
                            grantee=user.id, granter=self.principal.id)
        ok, why = attenuation_valid(issued, grant)
        assert ok, f"issued scope is not downhill of the provider grant: {why}"
        cap_id = content_id({"sso_cap": provider, "sub": subject, "scope": tuple(sorted(effective))})
        assert_content(self.k.weft, self.principal.id, cap_id, "capability", issued)
        self._grant_to(agent_id, cap_id)

        # 4) The session / identity Cell: principal ↔ provider-identity, carrying the
        #    issued capability + broker handle (NEVER the token).
        sid = content_id({"identity_session": provider, "sub": subject,
                          "n": self.k.weft.lamport})
        assert_content(self.k.weft, self.principal.id, sid, IDENTITY, {
            "provider": provider, "subject": subject, "principal": user.id,
            "agent": agent_id, "capability": cap_id, "handle": handle_id,
            "scope": sorted(effective), "clamped": clamped,
            "credential": cred_name, "active": True, "disclosed": False,
        })
        self._audit(sid, "login", ok=True,
                    detail=f"scope {sorted(effective)}"
                           + (f"; clamped {clamped}" if clamped else ""))
        return sid

    def _grant_to(self, agent_id: str, cap_id: str) -> None:
        """Add the issued capability to the user-agent's envelope (re-assert it)."""
        ag = self.k.weave().get(agent_id)
        env = list(ag.content.get("envelope", []))
        if cap_id not in env:
            env.append(cap_id)
        assert_content(self.k.weft, self.principal.id, agent_id, "agent",
                       {**ag.content, "envelope": env})

    # -- whoami: the identity + the issued capability -------------------------
    def whoami(self, session_id: str) -> dict:
        """The bound identity plus the issued capability (effect/target/caveats).
        No secret, no token — those live only in the broker."""
        w = self.k.weave()
        s = w.get(session_id)
        if s is None or s.type != IDENTITY:
            raise KeyError(f"no identity session {session_id!r}")
        cap = w.get(s.content["capability"])
        return {
            "provider": s.content["provider"],
            "subject": s.content["subject"],
            "principal": s.content["principal"],
            "active": bool(s.content.get("active")),
            "scope": list(s.content.get("scope", [])),
            "clamped": list(s.content.get("clamped", [])),
            "capability": s.content["capability"],
            "capability_revoked": (cap.retracted if cap else True),
            "effect": cap.content.get("effect") if cap else None,
            "caveats": dict(cap.content.get("caveats", {})) if cap else {},
        }

    def authorized(self, session_id: str, scope: str) -> tuple[bool, str]:
        """Does the live session's issued capability authorize `scope`? Runs the
        SAME ocap check as any INVOKE (fails closed once revoked)."""
        w = self.k.weave()
        s = w.get(session_id)
        if s is None or s.type != IDENTITY:
            return False, "no such session"
        ag = w.get(s.content["agent"])
        cap_id = s.content["capability"]
        ok, why = authorize(w, ag, cap_id, {}, ag.content["principal"],
                            spent=self.k.spent.get(ag.id, 0.0), approvals=self.k.approvals)
        if not ok:
            return False, why
        cap = w.get(cap_id)
        if nfc(scope) not in set(cap.content.get("caveats", {}).get("scope", [])):
            return False, f"scope {scope!r} not in issued grant"
        return True, "ok"

    # -- logout / revoke: kill the cap + broker handle → fail closed ----------
    def logout(self, session_id: str) -> None:
        """RETRACT the issued capability and the broker handle, mark the session
        inactive. `authorize` then denies the cap; the handle fails closed too."""
        w = self.k.weave()
        s = w.get(session_id)
        if s is None or s.type != IDENTITY:
            raise KeyError(f"no identity session {session_id!r}")
        cap_id = s.content["capability"]
        handle_id = s.content["handle"]
        # Kill the issued capability (RETRACT) — every future authorize fails closed.
        self.k.weft.append(self.principal.id, RETRACT, {"cell": cap_id})
        # Kill the broker handle to the external token (cascades to children).
        self.broker.revoke(handle_id)
        # Mark the session inactive (LWW).
        assert_content(self.k.weft, self.principal.id, session_id, IDENTITY,
                       {**s.content, "active": False})
        self._audit(session_id, "logout", ok=True, detail="capability + handle revoked")

    # revoke is logout — the OS revokes a session by killing its issued authority.
    revoke = logout

    def _audit(self, session_id, event, *, ok, detail) -> str:
        """Record a login / logout on the Weft. No secret, no token."""
        aud_id = content_id({"sso_login": session_id, "event": event,
                            "n": self.k.weft.lamport})
        assert_content(self.k.weft, self.principal.id, aud_id, SSO_LOGIN, {
            "session": session_id, "event": event, "ok": bool(ok),
            "detail": detail, "disclosed": False,
        })
        return aud_id


# ── Module-level convenience API (login/whoami/logout over a per-realm IdP) ──
# A single IdP per kernel, lazily created, so callers can use the verbs directly.

def _provider_for(k) -> IdentityProvider:
    idp = getattr(k, "_identity_provider", None)
    if idp is None:
        idp = IdentityProvider(k)
        k._identity_provider = idp
    return idp


def login(k, provider, subject, *, grants, token=None, provider_scope=None) -> str:
    """SSO login → mints an attenuated capability on the Weft + records a session.
    `token` defaults to a stub external bearer token (held by CRED1, never exposed)."""
    if token is None:
        token = f"oidc-bearer-{provider}-{subject}-XXXXXXXX"   # stub external token
    return _provider_for(k).login(provider, subject, grants=list(grants),
                                  token=token, provider_scope=provider_scope)


def whoami(k, session) -> dict:
    return _provider_for(k).whoami(session)


def authorized(k, session, scope) -> tuple[bool, str]:
    return _provider_for(k).authorized(session, scope)


def logout(k, session) -> None:
    return _provider_for(k).logout(session)


def revoke(k, session) -> None:
    return _provider_for(k).logout(session)
