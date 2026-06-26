"""INSURANCE1 — an insurance capability by composition (Cycle 21).

Proves `decima.insurance` composes LEDGER1 + PAY1 + SUBS1: it adds a policy (int
premium/coverage, recurring premium via SUBS1), files a claim, REFUSES (fail closed) a
claim exceeding coverage, reimburses a claim through a Morta-gated FINANCIAL payout
(denied → approve → paid) that posts a BALANCED ledger entry citing the claim, and
tracks claim status open→paid.

Runs on its OWN fresh Kernel (it forges a FINANCIAL capability and moves "money", so it
stays out of the shared kernel's state). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import insurance, accounts, payments, subscriptions
from decima.kernel import Kernel


def run(_k, line):
    line("\n== INSURANCE1 (policy · claim · coverage fail-closed · Morta payout · ledger) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)
    spent = lambda: k.spent.get(k.decima_agent_id, 0.0)

    # ---- (1) add a policy: int premium/coverage + recurring premium (SUBS1) ---
    pol = insurance.add_policy(k, "renters", premium=1_200, coverage=50_000,
                               category="insurance", every=30, next_at=30)
    pc = k.weave().get(pol)
    assert pc.type == insurance.POLICY and pc.content["coverage"] == 50_000
    assert isinstance(pc.content["premium"], int) and isinstance(pc.content["coverage"], int)
    sub = k.weave().get(pc.content["premium_subscription"])
    assert sub is not None and sub.type == subscriptions.SUBSCRIPTION
    assert sub.content["amount"] == 1_200, sub.content       # premium is recurring spend
    line(f"  policy renters: premium=1200 coverage=50000 (ints); recurring premium via "
         f"SUBS1 {sub.id[:8]} ✓")

    # ---- (2) file a claim within coverage (status open) ----------------------
    cl = insurance.file_claim(k, pol, 12_000, description="water damage")
    assert k.weave().get(cl).type == insurance.CLAIM
    assert insurance.status(k, cl) == insurance.OPEN
    # provenance: the claim covers its policy
    assert any(e["dst"] == pol for e in k.weave().edges_from(cl, insurance.COVERS))
    line(f"  filed claim {cl[:8]}: amount=12000 ≤ coverage 50000; status=open ✓")

    # ---- (3) a claim EXCEEDING coverage is REFUSED — nothing written ----------
    before = len(insurance.claims(k))
    try:
        insurance.file_claim(k, pol, 50_001, description="over coverage")
        raise AssertionError("file_claim accepted a claim exceeding coverage")
    except ValueError:
        pass
    after = len(insurance.claims(k))
    assert after == before, "an over-coverage claim must write NOTHING (fail closed)"
    line(f"  over-coverage claim (50001 > 50000) REFUSED; claims unchanged "
         f"({before}→{after}) — fail closed ✓")

    # ---- (4) payout is Morta-gated: DENIED until approved --------------------
    rail = payments.install_rail(k, cap=20_000)              # hard FINANCIAL cap
    r0 = insurance.approve_payout(k, decima(), cl, pay_cap=rail)
    assert "denied" in r0 and "approval" in r0["denied"].lower(), r0
    assert insurance.status(k, cl) == insurance.OPEN, "no payout before approval"
    assert spent() == 0.0, spent()                           # nothing charged
    line(f"  pre-approval: payout DENIED — {r0['denied']}; claim still open, spent=0 ✓")

    k.approve(rail)                                          # human/Morta approves
    line("  (a human approves the FINANCIAL capability — Morta gate)")

    # ---- approved payout: pays, posts a BALANCED ledger entry citing the claim
    r1 = insurance.approve_payout(k, decima(), cl, pay_cap=rail)
    assert r1["status"] == payments.executor.SUCCEEDED and not r1.get("denied"), r1
    assert spent() == 12_000.0, spent()
    receipt = k.weave().get(r1["receipt"])
    assert receipt.content["effect_class"] == payments.FINANCIAL
    assert receipt.content["status"] == payments.executor.SUCCEEDED
    assert receipt.content["amount"] == 12_000

    entry = k.weave().get(r1["entry"])
    assert entry.type == accounts.JOURNAL_ENTRY
    # balanced: debits == credits == 12000
    debits = sum(l["amount"] for l in entry.content["lines"] if l["side"] == accounts.DEBIT)
    credits = sum(l["amount"] for l in entry.content["lines"] if l["side"] == accounts.CREDIT)
    assert debits == credits == 12_000, entry.content
    assert entry.content["source"] == r1["receipt"], entry.content
    # provenance: the entry RECORDS the claim, and the claim PAYS the receipt
    assert any(e["dst"] == cl for e in k.weave().edges_from(r1["entry"], accounts.RECORDS))
    assert any(e["dst"] == r1["receipt"] for e in k.weave().edges_from(cl, insurance.PAYS))
    line(f"  approved: payout 12000 → receipt {r1['receipt'][:8]} (FINANCIAL); balanced "
         f"entry {r1['entry'][:8]} (debit 12000 / credit 12000) cites the claim ✓")

    # ---- (5) status tracked open→paid; over-cap second payout refused --------
    assert insurance.status(k, cl) == insurance.PAID, insurance.status(k, cl)
    # a second distinct claim whose payout would exceed the running cap is REFUSED
    cl2 = insurance.file_claim(k, pol, 15_000, description="theft")
    r2 = insurance.approve_payout(k, decima(), cl2, pay_cap=rail)
    assert "denied" in r2 and "budget" in r2["denied"].lower(), r2   # 12000+15000 > 20000
    assert insurance.status(k, cl2) == insurance.OPEN
    line(f"  status open→paid tracked; over-cap second payout (12000+15000>20000) REFUSED ✓")

    line("  → INSURANCE1 composes SUBS1 premiums + fail-closed coverage + a Morta-gated "
         "FINANCIAL payout posted to the LEDGER1 ledger, every payout cited to its claim.")
