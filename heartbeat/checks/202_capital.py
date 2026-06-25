"""CAPITAL1 — programmatic capital: ephemeral cards + pluggable fiat/crypto rails.

Proves the D3.4 ephemeral-card-as-capability design:
  - mint an EPHEMERAL card scoped to an amount cap + a merchant lock;
  - an in-cap, in-category charge is Morta-gated (denied → approve → charged), receipt
    on the Weft;
  - reuse of the single-use card fails closed (auto-revoked after one charge);
  - an over-cap OR wrong-merchant charge is REFUSED;
  - BOTH rails (stripe=fiat + coinbase=crypto) work behind ONE FINANCIAL interface;
  - the provider credential is NEVER exposed in a cell.

Runs on its OWN fresh Kernel (it moves "money"). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import executor, payments
from decima.kernel import Kernel
from decima.capital import CapitalDesk
from decima.secrets import CREDENTIAL


def run(_k, line):
    line("\n== PROGRAMMATIC CAPITAL (ephemeral cards · fiat+crypto · Morta · single-use) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)     # isolated
    desk = CapitalDesk(k)
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- (1) mint an ephemeral, scoped card (amount cap + merchant lock) -----
    card = desk.mint_card(k, decima(), amount_cap=50, merchant_category="compute", rail="stripe")
    cap = k.weave().get(card["cap_id"])
    assert cap.content["caveats"]["budget"] == 50                          # amount cap
    assert cap.content["target"] == "compute"                              # merchant lock
    assert cap.content["caveats"]["effect_class"] == payments.FINANCIAL    # ONE FINANCIAL contract
    assert cap.content["caveats"]["requires_approval"] is True             # Morta gate
    assert cap.content["parent"] is not None                               # downhill of the rail cap
    line(f"  mint(stripe): card {card['cap_id'][:8]} — cap=50, lock=compute, "
         f"class=FINANCIAL, single-use ✓")

    # ---- (2a) the charge is DENIED until the Morta gate is satisfied ---------
    r0 = desk.charge(k, decima(), card, amount=30, merchant="gpu-cloud",
                     merchant_category="compute", idempotency_key="buy-1")
    assert "denied" in r0 and "approval" in r0["denied"].lower(), r0
    assert payments.find_payment(k.weave(), f"{card['cap_id'][:12]}:buy-1") is None
    line(f"  pre-approval: charge(30) DENIED — {r0['denied']}")

    # ---- (2b) approve → the in-cap, in-category charge settles, receipt on Weft
    desk.approve(card)
    r1 = desk.charge(k, decima(), card, amount=30, merchant="gpu-cloud",
                     merchant_category="compute", idempotency_key="buy-1")
    assert r1.get("status") == executor.SUCCEEDED and not r1.get("denied"), r1
    assert r1["revoked"] is True                                           # single-use → auto-revoked
    receipt = k.weave().get(r1["result_cell"])
    assert receipt.content["effect_class"] == payments.FINANCIAL
    assert receipt.content["status"] == executor.SUCCEEDED
    assert receipt.content["amount"] == 30 and receipt.content["rail"] == "stripe"
    line(f"  approved: charge(30)→gpu-cloud → receipt {r1['result_cell'][:8]} "
         f"(class=FINANCIAL, rail=stripe); card auto-revoked ✓")

    # ---- (3) reuse of the SINGLE-USE card fails closed (revoked) -------------
    reuse = desk.charge(k, decima(), card, amount=10, merchant="gpu-cloud",
                        merchant_category="compute", idempotency_key="buy-2")
    assert "denied" in reuse and "revok" in reuse["denied"].lower(), reuse
    line(f"  reuse: charge(10) on a spent card FAILS CLOSED — {reuse['denied']}")

    # ---- (4a) over-cap charge is REFUSED ------------------------------------
    card2 = desk.mint_card(k, decima(), amount_cap=50, merchant_category="compute", rail="stripe")
    desk.approve(card2)
    over = desk.charge(k, decima(), card2, amount=80, merchant="gpu-cloud",
                       merchant_category="compute", idempotency_key="buy-3")
    assert "refused" in over and "over card cap" in over["refused"], over
    line(f"  over-cap: charge(80) on cap-50 card REFUSED — {over['refused']}")

    # ---- (4b) wrong-merchant (out of category) charge is REFUSED ------------
    wrong = desk.charge(k, decima(), card2, amount=10, merchant="luxury-store",
                        merchant_category="retail", idempotency_key="buy-4")
    assert "refused" in wrong and "outside card lock" in wrong["refused"], wrong
    assert payments.find_payment(k.weave(), f"{card2['cap_id'][:12]}:buy-4") is None
    line(f"  wrong-merchant: charge→retail on a compute-locked card REFUSED — {wrong['refused']}")

    # ---- (5) BOTH rails behind ONE FINANCIAL interface (crypto/USDC) --------
    cc = desk.mint_card(k, decima(), amount_cap=40, merchant_category="usdc-payout", rail="coinbase")
    desk.approve(cc)
    rc = desk.charge(k, decima(), cc, amount=25, merchant="vendor-x",
                     merchant_category="usdc-payout", idempotency_key="xfer-1")
    assert rc.get("status") == executor.SUCCEEDED and rc["rail"] == "coinbase", rc
    rec_c = k.weave().get(rc["result_cell"])
    assert rec_c.content["effect_class"] == payments.FINANCIAL and rec_c.content["rail"] == "coinbase"
    line(f"  crypto rail: charge(25 USDC)→vendor-x → receipt {rc['result_cell'][:8]} "
         f"(class=FINANCIAL, rail=coinbase) — same interface, different rail ✓")

    # ---- (6) the provider credential is NEVER exposed in a cell -------------
    for c in k.weave().of_type(CREDENTIAL):
        assert c.content.get("disclosed") is False
        # the on-Weft reference carries a digest + metadata, never the raw value
        for v in RAILS_CREDS:
            assert v not in str(c.content), f"raw provider cred leaked in cell {c.id[:8]}"
    # and no FINANCIAL receipt carries a raw credential either
    for c in k.weave().of_type("result"):
        if c.content.get("effect_class") == payments.FINANCIAL:
            for v in RAILS_CREDS:
                assert v not in str(c.content), f"raw cred leaked in receipt {c.id[:8]}"
    line("  cred safety: provider creds held by the broker; never exposed in any cell ✓")


# the raw provider credentials minted into the broker (must never appear on the Weft)
from decima.capital import RAILS as _RAILS
RAILS_CREDS = [cfg["cred"] for cfg in _RAILS.values()]
