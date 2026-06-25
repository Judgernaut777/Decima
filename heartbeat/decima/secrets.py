"""Secrets broker — hold a credential; hand out a handle, never the secret (CRED1).

`powerbox.py` (E1) issues scoped capability *grants* but says of itself: "The broker
never sees secrets … not modelled in the prototype." This is that missing layer, so
Decima can hold a payment method (PAY1) and engine/API keys while **no agent ever
touches the raw value**.

The shape:

  • store(name, secret, alias) — the raw credential lives ONLY in the broker's
    in-memory store (a stand-in for an HSM/enclave). On the Weft it leaves a
    *reference*: a content digest + metadata (alias/service), never the value in
    clear. You can prove WHICH secret without revealing it.
  • issue(name, agent, purpose) — a **scoped, attenuable, revocable handle**: an
    ordinary capability (Law 2) bound to the agent's principal + a purpose + the
    credential *name*. Holding the handle's id buys nothing — `authorize` still
    gates every use.
  • use(agent, handle, request) — the broker **dispenses, it does not disclose**:
    it authorizes the handle, then USES the secret to produce a result (a derived
    token / an authenticated action) and returns THAT. The secret never leaves the
    broker, and every use is audited on the Weft.
  • attenuate(handle, …) — a narrower handle downhill (`capability.attenuate` +
    structural `attenuation_valid`); authority only shrinks.
  • revoke(handle) — RETRACT the handle; it **fails closed**, and because revocation
    walks the delegation chain, revoking a parent fails every child closed too.

Built entirely on `capability` + the public weft/weave API; it does not edit
`powerbox.py` or any core file.
"""
from decima.capability import (capability_content, authorize,
                               attenuate as _attenuate, attenuation_valid)
from decima.model import assert_content
from decima.weft import RETRACT
from decima.hashing import content_id, blob_id, nfc

CREDENTIAL = "credential"        # the on-Weft REFERENCE cell (digest + metadata only)
SECRET_USE = "secret_use"        # an audit receipt for a dispense
HANDLE_EFFECT = "secret.use"     # the effect a handle authorizes


class SecretsBroker:
    """Holds opaque credentials and brokers scoped handles to them. One per realm;
    its raw store is in-memory only (the HSM/enclave seam)."""

    def __init__(self, k):
        self.k = k
        # The broker is its own principal (it signs the store-reference, the handle
        # grants, the use-audits, and the revocations it authors).
        self.principal = k.keyring.mint("secrets-broker", "broker")
        self._store: dict = {}   # name -> {secret, alias, service, digest} — NEVER on the Weft

    # -- store: the raw value stays in the broker; the Weft gets a reference ---
    def store(self, name: str, secret: str, *, alias: str | None = None,
              service: str | None = None) -> str:
        """Hold a credential opaquely. Returns the id of its on-Weft REFERENCE cell —
        a digest + metadata, never the secret. `alias` is a per-service privacy email
        alias (so the real address is never exposed to the service either)."""
        name = nfc(name)
        digest = blob_id(secret.encode("utf-8"), kind="secret")     # commits to the value, hides it
        self._store[name] = {"secret": secret, "alias": alias, "service": service,
                             "digest": digest}
        ref_id = content_id({"credential": name})
        assert_content(self.k.weft, self.principal.id, ref_id, CREDENTIAL, {
            "name": name, "digest": digest, "alias": alias, "service": service,
            "held_by": "broker", "disclosed": False,
        })
        return ref_id

    def alias_of(self, name: str) -> str | None:
        rec = self._store.get(nfc(name))
        return rec["alias"] if rec else None

    # -- issue: a scoped handle (a capability), bound to principal + purpose ---
    def issue(self, name: str, grantee_agent, purpose: str, *,
              budget: int | None = None, requires_approval: bool = False) -> str:
        """Issue a handle to `grantee_agent` for `purpose`. The handle references the
        credential by NAME (never the value). Returns the handle (capability) id."""
        name = nfc(name)
        if name not in self._store:
            raise KeyError(f"no credential {name!r} in the broker")
        grantee = grantee_agent.content["principal"]
        caveats = {"credential": name, "purpose": nfc(purpose)}
        if budget is not None:
            caveats["budget"] = int(budget)
        if requires_approval:
            caveats["requires_approval"] = True
        content = capability_content(
            name=f"secret:{name}", effect=HANDLE_EFFECT, target=nfc(purpose),
            caveats=caveats, grantee=grantee, granter=self.principal.id)
        hid = content_id({"handle": name, "to": grantee, "purpose": nfc(purpose)})
        assert_content(self.k.weft, self.principal.id, hid, "capability", content)
        self._grant_to(grantee_agent.id, hid)
        return hid

    def _grant_to(self, agent_id: str, handle_id: str) -> None:
        """Add a handle to an agent's envelope (re-assert the agent cell, broker-signed)."""
        ag = self.k.weave().get(agent_id)
        env = list(ag.content.get("envelope", []))
        if handle_id not in env:
            env.append(handle_id)
        assert_content(self.k.weft, self.principal.id, agent_id, "agent",
                       {**ag.content, "envelope": env})

    # -- attenuate: a narrower handle downhill --------------------------------
    def attenuate(self, parent_handle_id: str, granter_agent, sub_agent,
                  stricter: dict) -> str:
        """Issue a downhill handle to `sub_agent`, narrower than `parent_handle_id`
        which `granter_agent` holds. Proven ⊆ parent before it is written."""
        w = self.k.weave()
        parent = w.get(parent_handle_id)
        granter = granter_agent.content["principal"]      # must equal parent.grantee
        sub = sub_agent.content["principal"]
        child = _attenuate(parent.content, stricter, parent_handle_id,
                           grantee=sub, granter=granter)
        ok, why = attenuation_valid(child, parent.content)
        if not ok:
            raise ValueError(f"attenuation not downhill: {why}")
        hid = content_id({"handle_att": parent_handle_id, "to": sub})
        assert_content(self.k.weft, granter, hid, "capability", child)
        self._grant_to(sub_agent.id, hid)
        return hid

    # -- use: dispense, don't disclose ----------------------------------------
    def use(self, agent_cell, handle_id: str, request: dict) -> dict:
        """Use the credential behind `handle_id` on the holder's behalf. Authorizes
        the handle (fails closed on revoke / wrong principal / over-budget), then
        returns the RESULT of using the secret — a derived token / authenticated
        action — never the secret. Audited on the Weft either way."""
        w = self.k.weave()
        ag = w.get(agent_cell.id)
        principal = ag.content["principal"]
        ok, why = authorize(w, ag, handle_id, request, principal,
                            spent=self.k.spent.get(ag.id, 0.0), approvals=self.k.approvals)
        if not ok:
            self._audit(handle_id, request, principal, ok=False, detail=why, token=None)
            return {"denied": why}
        cap = w.get(handle_id)
        name = cap.content["caveats"]["credential"]
        rec = self._store[name]
        # dispense-don't-disclose: the secret is APPLIED to produce a token; it is
        # never placed in the result or on the Weft.
        token = blob_id((rec["secret"] + "|" + nfc(str(request.get("op", "use")))).encode("utf-8"),
                        kind="cred-token")
        result = {"out": f"performed {request.get('op', 'use')} via {name!r} "
                         f"as {rec.get('alias') or 'self'}",
                  "token": token, "alias": rec.get("alias"), "service": rec.get("service")}
        self.k.spent[ag.id] = self.k.spent.get(ag.id, 0.0) + float(request.get("cost", 0))
        self._audit(handle_id, request, principal, ok=True, detail="dispensed", token=token)
        return {"ok": result, "token": token}

    def _audit(self, handle_id, request, principal, *, ok, detail, token) -> str:
        """Record a dispense (or a denied attempt) on the Weft — the credential
        reference, the op, the holder, the outcome, the derived token. No secret."""
        aud_id = content_id({"secret_use": handle_id, "op": request.get("op"),
                             "by": principal, "n": self.k.weft.lamport})
        assert_content(self.k.weft, self.principal.id, aud_id, SECRET_USE, {
            "handle": handle_id, "op": request.get("op"), "by": principal,
            "ok": bool(ok), "detail": detail, "token": token, "disclosed": False,
        })
        return aud_id

    # -- revoke: the handle fails closed (and cascades to children) ------------
    def revoke(self, handle_id: str) -> None:
        """RETRACT the handle. `authorize` then denies it ('capability revoked'), and
        because delegation is checked up the chain, every handle attenuated from it
        fails closed too ('delegation path revoked upstream')."""
        self.k.weft.append(self.principal.id, RETRACT, {"cell": handle_id})
