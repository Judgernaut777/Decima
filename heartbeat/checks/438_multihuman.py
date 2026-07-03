"""MULTI-HUMAN — one Decima, many human principals, each with their own scoped authority.

The law under test: AUTHORITY ISOLATION BETWEEN CO-TENANT HUMANS. Two humans
sharing one Decima must be two DISTINCT principals — human A can neither act
with, nor approve on behalf of, human B, and each sees only their own scoped
view. `decima/multihuman.py` composes identity/capability/inbox/kernel/memory
public seams; this check is the adversarial detector over the REAL kernel spine.

Proves, offline + deterministically (fresh Kernel, logical ints, no clock):

  (a) TWO HUMANS, SEPARATE AUTHORITY — Alice and Bob enroll with DISJOINT grants
      (each attenuated downhill from a realm cap). Alice invokes her own cap →
      SUCCEEDS; Alice invokes the cap only Bob holds → DENIED at the ocap gate
      (envelope/grantee, "no ambient authority"), and vice-versa — the effect
      never fires cross-principal;
  (b) APPROVAL IS BOUND TO THE APPROVER — a Morta-gated action enqueued FOR Bob
      cannot be approved by Alice: her attempt is REFUSED (a REFUSAL Cell lands,
      the item stays pending, ZERO invocations of Bob's cap), while Bob's own
      approval ENACTS it through the full gate (exactly one invocation, the
      decision names Bob's principal as approver of record). A second decision
      on the same item fails closed;
  (c) SCOPED VIEW — a claim Alice writes in her scope is NOT returned by Bob's
      scoped recall (and vice-versa); an explicit shared-realm-scope claim is
      visible to both; every human-authored claim is `instruction_eligible=False`
      (observed = DATA, never obeyed); each `view_of` shows only that human's
      claims and pending items;
  (d) IDENTITY IS SELF-CERTIFYING + DISTINCT — Alice's and Bob's principal ids
      differ and each is content-derived (pid == blake2b(their public key));
      both enrollments are Cells on the Weft; the envelope is EXACTLY the
      granted caps, each a valid downhill delegation from the realm cap; a realm
      capability never granted (shell) is denied; re-enrolling fails closed.

Mutation-resistance (the load-bearing line): neuter `if approver != bound:` in
`multihuman.approve_as` (make it always-false / let any human's approval enact
any item) → (b) goes RED — Alice's approval enacts Bob's gated action (the
invocation count for Bob's cap rises under HER approval, and the "refused"
assertion fails). Neuter `acting_as`'s honest agent resolution → (a) goes RED.

Registers its OWN hermetic effects (mh_note_probe / mh_send_probe) — never
'echo'. Contract: run(k, line). Fail loud (assert / expected error).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.capability import verify_delegation
from decima.inbox import ApprovalInbox
from decima import executor, multihuman as mh

# Check-local, deterministic effects so the probes are HERMETIC — independent of
# whatever a prior check left in the module-global executor registry.
_NOTE_EFFECT = "mh_note_probe"
_SEND_EFFECT = "mh_send_probe"


def _fired(k, cap_id) -> int:
    """Deterministic fold: how many INVOKEs this capability has authorized."""
    return sum(1 for inv in k.weave().invocations if inv.cap == cap_id)


def _fresh():
    """A fresh, isolated Kernel with two realm caps: mh.note (plain) and
    mh.send (Morta-gated), both held by Decima — the downhill roots."""
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    executor.register(_NOTE_EFFECT,
                      lambda impl, args: {"out": "noted:" + str(args.get("text", ""))})
    executor.register(_SEND_EFFECT,
                      lambda impl, args: {"out": "sent:" + str(args.get("text", ""))})
    mh.mint_realm_capability(k, "mh.note", _NOTE_EFFECT)
    mh.mint_realm_capability(k, "mh.send", _SEND_EFFECT, gated=True)
    return k


def run(k, line):
    line("\n== MULTI-HUMAN — two humans, one Decima, separate authority ==")
    k1 = _fresh()
    alice = mh.register_human(k1, "alice", grants=["mh.note"], scope="human:alice")
    bob = mh.register_human(k1, "bob", grants=["mh.send"], scope="human:bob")
    a_cap, b_cap = alice["caps"]["mh.note"], bob["caps"]["mh.send"]
    assert a_cap != b_cap, "disjoint grants must be distinct grant cells"

    # ── (a) TWO HUMANS, SEPARATE AUTHORITY (load-bearing). ────────────────────
    res = mh.invoke_as(k1, "alice", a_cap, {"text": "hi"})
    assert res.get("status") == "SUCCEEDED" and res["ok"]["out"] == "noted:hi", \
        f"Alice must be able to invoke HER OWN grant: {res}"
    cross = mh.invoke_as(k1, "alice", b_cap, {"text": "exfiltrate"})
    assert "denied" in cross and "ok" not in cross, \
        f"Alice invoking a cap only Bob holds must be DENIED at the gate: {cross}"
    assert "envelope" in cross["denied"] or "grant" in cross["denied"], \
        f"the denial must come from the ocap gate (no grant / wrong grantee): {cross}"
    rev = mh.invoke_as(k1, "bob", a_cap, {"text": "peek"})
    assert "denied" in rev, f"Bob invoking Alice's grant must be DENIED too: {rev}"
    assert _fired(k1, b_cap) == 0, "no cross-principal INVOKE may ever land"
    assert mh.holds(k1, "alice", a_cap) and not mh.holds(k1, "alice", b_cap), \
        "the envelope test must say Alice holds hers and NOT Bob's"
    line("  separate authority: Alice's own invoke SUCCEEDS; Alice on Bob's cap "
         "(and Bob on Alice's) is DENIED at the ocap gate — zero cross-principal "
         "invokes on the log ✓")

    # ── (b) APPROVAL IS BOUND TO THE APPROVER. ────────────────────────────────
    direct = mh.invoke_as(k1, "bob", b_cap, {"text": "wire 5"})
    assert "denied" in direct and "approval" in direct["denied"], \
        f"Bob's gated cap must hit the Morta gate un-approved: {direct}"
    item = mh.enqueue_gated(k1, "bob", b_cap, {"text": "wire 5"},
                            description="bob's outward send")
    ib = ApprovalInbox(k1)
    assert item in [c.id for c in ib.pending()], "the gated action must be queued"
    ref = mh.approve_as(k1, "alice", item)                # Alice tries to approve BOB's item
    assert "refused" in ref and "ok" not in ref, \
        f"Alice approving Bob's pending item must be REFUSED: {ref}"
    assert _fired(k1, b_cap) == 0, \
        "the action must NOT fire under Alice's approval (approval bound to the approver)"
    assert item in [c.id for c in ib.pending()], "the refused item must stay PENDING for Bob"
    refusals = k1.weave().of_type(mh.REFUSAL)
    assert len(refusals) == 1 and refusals[0].content["approver"] == alice["principal"] \
        and refusals[0].content["bound_to"] == bob["principal"], \
        "the refusal must be audited on the Weft, naming both principals"
    stranger_deny = mh.deny_as(k1, "alice", item)          # a stranger's DENY is refused too
    assert "refused" in stranger_deny and item in [c.id for c in ib.pending()], \
        f"Alice may not deny Bob's item either: {stranger_deny}"
    enact = mh.approve_as(k1, "bob", item)                 # Bob's OWN approval enacts it
    assert enact.get("status") == "SUCCEEDED" and enact["ok"]["out"] == "sent:wire 5", \
        f"Bob's approval must enact his gated action through the full gate: {enact}"
    assert _fired(k1, b_cap) == 1, "exactly ONE invocation — Bob's, and only Bob's"
    assert item not in [c.id for c in ib.pending()], "the enacted item is decided"
    st = ib.inspect(item)
    assert st["status"] == "approved" \
        and st["decision"].content["approver"] == bob["principal"], \
        "the decision of record must name BOB's principal as approver"
    try:
        mh.approve_as(k1, "alice", item)
        raise AssertionError("a decided item was re-approvable (must fail closed)")
    except mh.MultiHumanError:
        pass
    line("  approval binding: Alice's approve (and deny) of Bob's item is REFUSED "
         "(audited, item stays pending, zero fires); Bob's own approval enacts it "
         "exactly once with Bob as approver of record ✓")

    # ── (c) SCOPED VIEW — recall never crosses a private scope. ───────────────
    a_claim = mh.write_claim_as(k1, "alice", "alice keeps her travel notes here")
    b_claim = mh.write_claim_as(k1, "bob", "bob keeps his ledger memo here")
    shared = mh.write_claim_as(k1, "alice", "the realm standup is at nine",
                               shared=True)
    assert [c.id for c in mh.recall_as(k1, "alice", "travel notes")] == [a_claim], \
        "Alice's scoped recall must return her own claim"
    assert mh.recall_as(k1, "bob", "travel notes") == [], \
        "Bob's scoped recall must NOT return Alice's scoped-private claim"
    assert [c.id for c in mh.recall_as(k1, "bob", "ledger memo")] == [b_claim] \
        and mh.recall_as(k1, "alice", "ledger memo") == [], \
        "and vice-versa: Alice never sees Bob's private claim"
    assert shared in {c.id for c in mh.recall_as(k1, "alice", "standup")} \
        and shared in {c.id for c in mh.recall_as(k1, "bob", "standup")}, \
        "an explicit shared-realm-scope claim is visible to BOTH humans"
    for cid in (a_claim, b_claim, shared):
        cell = k1.weave().get(cid)
        assert cell.content.get("instruction_eligible") is False, \
            "a human's text is observed content: DATA, never an instruction"
        assert isinstance(cell.content.get("confidence"), int), "ints-not-floats"
    va, vb = mh.view_of(k1, "alice"), mh.view_of(k1, "bob")
    assert a_claim in va["claims"] and b_claim not in va["claims"], \
        "Alice's view shows only her scoped claims"
    assert b_claim in vb["claims"] and a_claim not in vb["claims"], \
        "Bob's view shows only his scoped claims"
    assert va["pending"] == [] and vb["pending"] == [], "nothing pending after the enactment"
    try:
        mh.write_claim_as(k1, "alice", "float sneaks in", confidence=0.5)
        raise AssertionError("a float confidence was accepted (ints-not-floats violated)")
    except mh.MultiHumanError:
        pass
    line("  scoped view: private claims never cross scopes, the shared-scope claim "
         "reaches both, every claim is instruction_eligible=False (data, never "
         "obeyed), and a float confidence is refused at the door ✓")

    # ── (d) IDENTITY IS SELF-CERTIFYING + DISTINCT; nothing ambient. ──────────
    assert alice["principal"] != bob["principal"], "two humans, two principals"
    for ent in (alice, bob):
        pid = ent["principal"]
        assert pid == k1.keyring.keyed_pid(k1.keyring.public_key(pid)), \
            "each principal id must be CONTENT-DERIVED from its own public key (self-certifying)"
    enrolled = {c.content["subject"] for c in k1.weave().of_type(mh.ENROLLMENT)}
    assert enrolled == {"alice", "bob"}, "both enrollments must be Cells on the Weft"
    agent_a = k1.weave().get(alice["agent"])
    assert agent_a.content["envelope"] == [a_cap], \
        "enrollment confers EXACTLY the granted caps — nothing ambient"
    cap_cell = k1.weave().get(a_cap)
    assert cap_cell.content["grantee"] == alice["principal"] \
        and cap_cell.content["parent"] is not None, \
        "the grant names HER principal and descends from the realm cap"
    ok, why = verify_delegation(k1.weave(), cap_cell)
    assert ok, f"the human grant must be a valid downhill delegation: {why}"
    shell = next(c for c in k1.weave().of_type("capability")
                 if c.content.get("name") == "shell" and not c.content.get("parent"))
    ambient = mh.invoke_as(k1, "alice", shell.id, {"cmd": "date"})
    assert "denied" in ambient, \
        f"a realm capability never granted to Alice must be DENIED: {ambient}"
    try:
        mh.register_human(k1, "alice", grants=["mh.note"])
        raise AssertionError("a duplicate enrollment was accepted (must fail closed)")
    except mh.MultiHumanError:
        pass
    line("  identity: distinct, self-certifying principals (pid == blake2b(pubkey)); "
         "enrollment on the Weft grants exactly the named caps, every grant a "
         "downhill delegation, ambient reach denied, re-enrollment fails closed ✓")

    line("  → Decima is MULTI-HUMAN: each human is their own self-certifying "
         "principal with a downhill-attenuated envelope; cross-principal invokes "
         "die at the ocap gate, a Morta approval is bound to the approver's "
         "principal, and every scoped view stays that human's own.")
