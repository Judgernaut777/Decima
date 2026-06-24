"""SY1 — sync convergence in the reference (no network).

specs/SYNC.md says peers reconcile by DAG **union** of immutable signed events: no
peer overwrites another's history, conflicts surface through the merge reducers, and
authorization is judged at an event's causal frontier — so sync can't re-authorize a
revoked grant. We exercise those invariants against M1's merge layer by modelling two
peers as **concurrent branches off a shared base** (a fork in one Weft), then taking
the union:

  - **convergence** — the union folds to ONE state_root no matter which peer's events
    are applied first (SYNC §10; FOLD §11 #2);
  - **no overwrite** — both peers' concurrent OR-set adds survive the union (SYNC §1);
  - **revocation respected** — a grant revoked on one branch is revoked in the merged
    state regardless of order, live at a pre-revoke frontier, and a causal descendant
    INVOKE fails closed (SYNC §4 / FOLD §11).

This is a reference simulation: one process, one Weft, forks standing in for peers.
The real network transport behind SYNC.md is a later cycle. Contract: run(k, line). Fail loud.
"""
from decima import model, reckoner
from decima.weave import Weave, MERGE_ORSET
from decima.weft import ASSERT, RETRACT
from decima.hashing import content_id


def run(k, line):
    line("\n== SYNC SIMULATION (two peers · DAG union · convergence) ==")
    root, wf = k.root.id, k.weft

    # A throwaway capability we can revoke without disturbing shared state.
    reckoner.forge(k, "synccap", "transform", "upper", "x", "X")   # forged, promoted, granted to Decima
    cap = next(c for c in k.weave().of_type("capability") if c.content["name"] == "synccap")

    # An OR-set so we can prove the union drops no peer's add.
    model.define_type(wf, root, "peerset", merge_class=MERGE_ORSET)
    pset = content_id({"peerset": "members"})

    base = wf.head        # the common frontier both peers fork from

    def fork(parents, verb, body):
        return wf.append(root, verb, body, parents=parents)

    # Peer A (never saw B): add 'alpha', and REVOKE synccap — both concurrent with B.
    a_add = fork([base], ASSERT, {"cell": pset, "type": "peerset", "kind": "CONTENT",
                                  "content": {"op": "add", "element": "alpha"}})
    a_rev = fork([base], RETRACT, {"cell": cap.id})
    # Peer B (never saw A): add 'beta'.
    b_add = fork([base], ASSERT, {"cell": pset, "type": "peerset", "kind": "CONTENT",
                                  "content": {"op": "add", "element": "beta"}})

    trio = [a_add, a_rev, b_add]                       # the three concurrent (equal-lamport) events
    trio_ids = {e.id for e in trio}
    common = sorted((ev for ev in wf.events() if ev.id not in trio_ids),
                    key=lambda e: (e.lamport, e.id))   # everything causally below the fork

    # ---- (1) CONVERGENCE: union folds to ONE state_root in any peer order ----
    # The three forked events share a lamport, so any interleaving is a valid total
    # order. "A then B", "B then A", and a shuffle must all converge — that is exactly
    # "two peers that union to a common frontier compute the same state."
    def root_of(order):
        w = Weave()
        for ev in order:
            w._apply(ev)
        return w.state_root()

    a_then_b = common + [a_add, a_rev, b_add]
    b_then_a = common + [b_add, a_add, a_rev]
    shuffled = common + [a_rev, b_add, a_add]
    r1, r2, r3, rc = (root_of(a_then_b), root_of(b_then_a), root_of(shuffled),
                      k.weave().state_root())
    assert r1 == r2 == r3 == rc, (r1[:8], r2[:8], r3[:8], rc[:8])
    line(f"  two peers union → ONE state_root regardless of who synced first: {r1[:12]} ✓")

    # ---- (2) NO OVERWRITE: both peers' concurrent adds survive --------------
    members = sorted(k.weave().get(pset).content["elements"])
    assert members == ["alpha", "beta"], members
    line(f"  union drops no peer's event — OR-set holds both adds: {members} ✓")

    # ---- (3) REVOCATION RESPECTED across the union -------------------------
    # The revoke is observed in the merged state (order-independently, by (1))...
    assert k.weave().get(cap.id).retracted, "revoke must be observed in the union"
    # ...a fold to a frontier BEFORE the revoke shows the cap still live (auth is
    # frontier-relative, not 'whatever the latest event said')...
    pre = k.weave(upto_seq=a_rev.seq - 1)
    live_before = pre.get(cap.id) is not None and not pre.get(cap.id).retracted
    # ...and a CAUSAL DESCENDANT of the revoke can't use it. Make a merge point that
    # descends from BOTH branches, so a subsequent INVOKE is genuinely after the
    # revoke's effective frontier, then watch it fail closed (FOLD §11).
    fork([a_rev.id, b_add.id], ASSERT,
         {"cell": content_id({"sync": "merge-point"}), "type": "note",
          "kind": "CONTENT", "content": {"merged": True}})
    decima = k.weave().get(k.decima_agent_id)
    denied = "denied" in k.invoke(decima, cap.id, {"text": "post-revoke"})
    assert live_before and denied, (live_before, denied)
    line(f"  revoked grant: live at a pre-revoke frontier={live_before}; a descendant "
         f"INVOKE denied={denied} — sync can't re-authorize it ✓")
    line("  (full frontier-relative auth of *concurrent* invokes is the durable SYNC.md "
         "contract; the reference judges at merged state — noted, deferred.)")
