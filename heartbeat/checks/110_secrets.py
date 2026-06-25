"""CRED1 — secrets broker: hold an opaque credential, hand out scoped handles, and
NEVER let the raw secret onto the Weft or back to a caller.

Proves: store → a reference (raw secret never returned, never on the Weft); use via
the handle works (dispense-don't-disclose, audited); attenuation narrows downhill;
revoke → the handle (and its children) fail closed; a privacy alias is recorded.

Runs on its OWN fresh Kernel (it mints a broker principal + agents and forges
handle capabilities; smoke discovers checks by lexical filename order). Contract:
run(k, line). Fail loud.
"""
import json
import os
import tempfile

from decima import secrets
from decima.kernel import Kernel
from decima.hashing import content_id


def _secret_on_weft(k, raw: str) -> bool:
    """Does the raw secret appear ANYWHERE in the signed log? It must not."""
    for seq, payload in k.weft.db.execute("SELECT seq, payload FROM events"):
        if raw in payload:
            return True
    return False


def run(_k, line):
    line("\n== SECRETS BROKER (opaque credential · scoped handle · dispense-not-disclose) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    broker = secrets.SecretsBroker(k)
    RAW = "sk_live_DEADBEEF_super_secret_42"

    # ---- store: raw value stays in the broker; the Weft gets a reference ----
    ref = broker.store("stripe-key", RAW, alias="acme.shop@relay.privaliases.example",
                       service="stripe")
    refc = k.weave().get(ref)
    assert refc.content["digest"] and "digest" in refc.content
    assert RAW not in json.dumps(refc.content)                       # reference, not value
    assert not _secret_on_weft(k, RAW), "raw secret leaked onto the Weft at store"
    assert broker.alias_of("stripe-key").endswith("privaliases.example")  # privacy alias recorded
    line(f"  stored 'stripe-key' → reference {ref[:8]} (digest only); "
         f"alias={broker.alias_of('stripe-key')!r}; raw secret on Weft: {_secret_on_weft(k, RAW)}")

    # ---- issue a scoped handle to Decima -----------------------------------
    decima = k.weave().get(k.decima_agent_id)
    h1 = broker.issue("stripe-key", decima, "charge customers", budget=100)
    hc = k.weave().get(h1)
    assert hc.type == "capability" and hc.content["grantee"] == decima.content["principal"]
    assert hc.content["caveats"]["credential"] == "stripe-key"       # references by NAME
    assert RAW not in json.dumps(hc.content)                         # the handle holds no secret
    line(f"  issued handle {h1[:8]} → cap bound to Decima for purpose "
         f"{hc.content['target']!r}, budget {hc.content['caveats']['budget']} (no secret inside)")

    # ---- use: the broker dispenses, never discloses ------------------------
    r = broker.use(decima, h1, {"op": "charge", "cost": 30})
    assert "ok" in r and r["token"] and RAW not in json.dumps(r)     # a derived token, not the secret
    assert not _secret_on_weft(k, RAW), "raw secret leaked onto the Weft at use"
    audits = [c for c in k.weave().of_type(secrets.SECRET_USE) if c.content["handle"] == h1]
    assert len(audits) == 1 and audits[0].content["ok"] and RAW not in json.dumps(audits[0].content)
    line(f"  used handle → {r['ok']['out']!r}; token={r['token'][:12]}…; "
         f"audited on Weft (secret never returned/logged) ✓")

    # ---- attenuate downhill: a narrower handle to a sub-agent --------------
    sub_p = k.keyring.mint("scout", "agent")
    sub_id = content_id({"secrets_sub": "scout"})
    from decima.model import assert_content
    assert_content(k.weft, k.root.id, sub_id, "agent",
                   {"principal": sub_p.id, "envelope": [], "objective": "narrow use"})
    sub = k.weave().get(sub_id)
    decima = k.weave().get(k.decima_agent_id)                         # holds h1 → may delegate downhill
    h2 = broker.attenuate(h1, decima, sub, {"budget": 20})           # 100 → 20
    h2c = k.weave().get(h2)
    assert h2c.content["parent"] == h1 and h2c.content["caveats"]["budget"] == 20
    assert broker.use(sub, h2, {"op": "charge", "cost": 5})["ok"]    # the sub can dispense within scope
    line(f"  attenuated {h1[:8]} → {h2[:8]} for the sub-agent (budget 100→20); "
         f"sub used it within scope ✓")

    # ---- revoke: the handle fails closed (and cascades to the child) -------
    broker.revoke(h1)
    rr = broker.use(decima, h1, {"op": "charge", "cost": 1})
    assert "denied" in rr and "revoked" in rr["denied"].lower(), rr
    rc = broker.use(sub, h2, {"op": "charge", "cost": 1})            # child via revoked parent
    assert "denied" in rc and "revoked" in rc["denied"].lower(), rc
    line(f"  revoked {h1[:8]} → handle DENIED ({rr['denied']}); "
         f"child {h2[:8]} fails closed too (delegation path revoked) ✓")

    # ---- final proof: across the whole run, the raw secret never hit the Weft
    assert not _secret_on_weft(k, RAW)
    line("  → end-to-end: the broker dispensed and audited every action; the raw "
         "secret never appeared on the Weft or in any return value ✓")
