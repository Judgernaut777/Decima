"""Retraction MODES — SUPERSEDE + TERMINATE with cascade (WEFT §5 / FOLD §10, T2.2).

WITHDRAW and REDACT were already folded; these are the two modes the fold now resolves:

  * SUPERSEDE — a forward-pointing tombstone. The payload is NEVER erased; the fold
    records the `replacement` and `Weave.current()` resolves the chain to the SUCCESSOR
    while `get()` stays the audit view (FOLD §10: "current-state projections prefer
    `replacement`; audit and temporal projections retain both and expose the supersession
    edge"). It does NOT cascade unless the body explicitly asks.
  * TERMINATE — a hard shutdown that composes the existing fail-closed cascade
    (LEASE_TREE), and is TERMINAL: no later ASSERT can resurrect the ended thread, and
    descendants asserted AFTER the termination fail closed too.

Everything here is a pure fold over a real in-process Weft (fixed seed, no wall-clock,
integer lease bounds), and the last three tests pin the determinism contract: two folds
agree, an incremental fold from a mid-history checkpoint agrees, and shuffled +
duplicated delivery agrees (FOLD §11.1/2/3).
"""

from __future__ import annotations

import os
import random
import tempfile

from decima.kernel import lifecycle
from decima.kernel.authorization import ReasonCode, authorize_decision
from decima.kernel.capability import capability_content
from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import RETRACT, Weft

_SEED = bytes(32)  # fixed master seed → reproducible keys, no randomness in content


def _setup() -> tuple[Weft, Keyring, str, str]:
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    kr = Keyring(seed=_SEED)
    root = kr.mint("root", "root").id
    alice = kr.mint("alice", "agent").id
    return Weft(db, kr), kr, root, alice


def _cap(
    weft: Weft,
    root: str,
    cap_id: str,
    principal: str,
    *,
    parent: str | None = None,
    granter: str | None = None,
    caveats: dict | None = None,
) -> None:
    """A capability Cell, optionally attenuated from `parent` (the authority edge the
    DERIVED_AUTHORITY / LEASE_TREE cascade walks)."""
    assert_content(
        weft,
        root,
        cap_id,
        "capability",
        capability_content(
            cap_id,
            "transform",
            target="*",
            caveats=caveats or {},
            grantee=principal,
            granter=granter or root,
            parent=parent,
        ),
    )


def _agent(weft: Weft, root: str, agent_id: str, principal: str, envelope: list[str]) -> None:
    assert_content(weft, root, agent_id, "agent", {"principal": principal, "envelope": envelope})


# ── SUPERSEDE ─────────────────────────────────────────────────────────────────────
def test_supersede_keeps_the_payload_and_points_at_the_successor() -> None:
    """SUPERSEDE is currency, not erasure: the tombstone keeps its payload and carries
    `superseded_by`; the replacement is the live cell of that type."""
    weft, _kr, root, _alice = _setup()
    assert_content(weft, root, "note:v1", "note", {"text": "first", "n": 1})
    assert_content(weft, root, "note:v2", "note", {"text": "corrected", "n": 2})
    lifecycle.supersede(weft, root, "note:v1", "note:v2")

    w = Weave.fold(weft)
    old = w.get("note:v1")
    assert old is not None
    assert old.retracted is True, "a superseded cell is tombstoned out of live state"
    assert old.redacted is False, "SUPERSEDE must not erase (that is REDACT)"
    assert old.content == {"text": "first", "n": 1}, "the payload survives supersession"
    assert old.superseded_by == "note:v2"
    assert old.cascade_root is False and old.cascade_mode is None, "SUPERSEDE does not cascade"

    live = {c.id for c in w.of_type("note")}
    assert live == {"note:v2"}, "current-state projections show the successor only"

    current = w.current("note:v1")
    assert current is not None and current.id == "note:v2", "current() resolves the successor"
    assert w.supersession_chain("note:v1") == ["note:v1", "note:v2"]


def test_current_resolves_a_multi_hop_chain_and_get_stays_the_audit_view() -> None:
    """v1 → v2 → v3: `current()` walks to the newest version from ANY point in the chain;
    `get()` still returns the tombstone as folded (audit/temporal view)."""
    weft, _kr, root, _alice = _setup()
    for i in (1, 2, 3):
        assert_content(weft, root, f"note:v{i}", "note", {"text": f"v{i}", "n": i})
    lifecycle.supersede(weft, root, "note:v1", "note:v2")
    lifecycle.supersede(weft, root, "note:v2", "note:v3")

    w = Weave.fold(weft)
    for start in ("note:v1", "note:v2"):
        cur = w.current(start)
        assert cur is not None and cur.id == "note:v3", f"{start} must resolve to note:v3"
        got = w.get(start)
        assert got is not None and got.id == start, "get() is the audit view, not a redirect"
    assert w.supersession_chain("note:v1") == ["note:v1", "note:v2", "note:v3"]
    newest = w.current("note:v3")
    assert newest is not None and newest.id == "note:v3", "the newest version resolves to itself"


def test_supersession_resolution_is_total_on_cycles_and_dangling_pointers() -> None:
    """Reducers are TOTAL (FOLD §2): a cyclic chain stops at the first repeat and a
    `replacement` naming something that is not a Cell (an EVENT id is legal) resolves to
    the last KNOWN cell instead of raising or looping."""
    weft, _kr, root, _alice = _setup()
    assert_content(weft, root, "note:a", "note", {"text": "a"})
    assert_content(weft, root, "note:b", "note", {"text": "b"})
    lifecycle.supersede(weft, root, "note:a", "note:b")
    lifecycle.supersede(weft, root, "note:b", "note:a")  # a cycle

    w = Weave.fold(weft)
    cur = w.current("note:a")
    assert cur is not None and cur.id == "note:b", "a cycle stops at the first repeat"
    assert w.supersession_chain("note:a") == ["note:a", "note:b"]

    weft2, _kr2, root2, _a2 = _setup()
    assert_content(weft2, root2, "note:x", "note", {"text": "x"})
    ev = assert_content(weft2, root2, "note:y", "note", {"text": "y"})
    lifecycle.supersede(weft2, root2, "note:x", ev.id)  # replacement names an EVENT
    w2 = Weave.fold(weft2)
    cur2 = w2.current("note:x")
    assert cur2 is not None and cur2.id == "note:x", "a dangling pointer resolves to the last cell"
    assert w2.supersession_chain("note:x") == ["note:x", ev.id], "the edge is still exposed"


def test_a_fresher_assertion_of_the_target_overtakes_the_supersession() -> None:
    """Supersession is the LATEST word or nothing: re-asserting the target after the
    SUPERSEDE makes it current again and clears the stale pointer (derived, so the answer
    is the same on every re-fold)."""
    weft, _kr, root, _alice = _setup()
    assert_content(weft, root, "note:v1", "note", {"text": "first"})
    assert_content(weft, root, "note:v2", "note", {"text": "second"})
    lifecycle.supersede(weft, root, "note:v1", "note:v2")
    assert_content(weft, root, "note:v1", "note", {"text": "revived"})

    w = Weave.fold(weft)
    cell = w.get("note:v1")
    assert cell is not None
    assert cell.superseded_by is None, "a fresher assertion overtook the supersession"
    assert cell.retracted is False and cell.content == {"text": "revived"}
    cur = w.current("note:v1")
    assert cur is not None and cur.id == "note:v1"
    assert Weave.fold(weft).state_root() == w.state_root(), "re-folding must be stable"


def test_concurrent_supersessions_resolve_by_canonical_order_not_arrival() -> None:
    """Two SUPERSEDEs of the same cell on CONCURRENT branches (an explicit parent set is a
    fork) resolve to the max-(lamport, event_id) replacement — the same answer whichever
    branch is delivered first."""
    weft, _kr, root, _alice = _setup()
    assert_content(weft, root, "note:v1", "note", {"text": "first"})
    assert_content(weft, root, "note:a", "note", {"text": "a"})
    assert_content(weft, root, "note:b", "note", {"text": "b"})
    fork = [weft.head] if weft.head else []
    e1 = weft.append(
        root,
        RETRACT,
        {"cell": "note:v1", "mode": "SUPERSEDE", "replacement": "note:a"},
        parents=list(fork),
    )
    e2 = weft.append(
        root,
        RETRACT,
        {"cell": "note:v1", "mode": "SUPERSEDE", "replacement": "note:b"},
        parents=list(fork),
    )
    assert e1.lamport == e2.lamport, "the two retractions are concurrent (same lamport)"
    winner = "note:a" if e1.id > e2.id else "note:b"

    w = Weave.fold(weft)
    cell = w.get("note:v1")
    assert cell is not None and cell.superseded_by == winner
    cur = w.current("note:v1")
    assert cur is not None and cur.id == winner

    # Arrival order cannot change it: replay the same events shuffled.
    events = list(weft.events())
    replay = Weave()
    rnd = random.Random(11)
    delivered = list(events) + rnd.sample(events, k=3)
    rnd.shuffle(delivered)
    for ev in delivered:
        replay._apply(ev)
    replay._ensure_cascade()
    assert replay.state_root() == w.state_root()


def test_supersede_does_not_cascade_but_an_explicit_cascade_does() -> None:
    """SUPERSEDE never fails closed derived authority by default (that is REVOKE's job);
    an explicit `cascade` in the body opts in, reusing the same closure walk."""
    weft, _kr, root, alice = _setup()
    _cap(weft, root, "cap:parent", alice)
    _cap(weft, root, "cap:child", alice, parent="cap:parent", granter=alice)
    _cap(weft, root, "cap:replacement", alice)

    lifecycle.supersede(weft, root, "cap:parent", "cap:replacement")
    w = Weave.fold(weft)
    child = w.get("cap:child")
    assert child is not None
    assert child.retracted is False and child.cascaded is False, "SUPERSEDE must not cascade"

    lifecycle.supersede(weft, root, "cap:parent", "cap:replacement", cascade="DERIVED_AUTHORITY")
    w2 = Weave.fold(weft)
    parent2, child2 = w2.get("cap:parent"), w2.get("cap:child")
    assert parent2 is not None and child2 is not None
    assert parent2.cascade_root is True and parent2.cascade_mode == "DERIVED_AUTHORITY"
    assert child2.retracted is True and child2.cascaded is True, "explicit cascade fails closed"
    assert parent2.superseded_by == "cap:replacement", "the pointer still records the successor"


# ── TERMINATE ─────────────────────────────────────────────────────────────────────
def test_terminate_fails_closed_the_whole_lease_tree() -> None:
    """A TERMINATE composes the existing fail-closed cascade: the target becomes a
    LEASE_TREE cascade root and every authority descendant — via `parent` or a
    `leased_from` edge, transitively — is retracted + `cascaded`."""
    weft, _kr, root, alice = _setup()
    _cap(weft, root, "cap:lease", alice, caveats={"expires_at": 9_000, "max_uses": 9})
    _cap(weft, root, "cap:sub", alice, parent="cap:lease", granter=alice)
    _cap(weft, root, "cap:subsub", alice, parent="cap:sub", granter=alice)
    _cap(weft, root, "cap:sibling", alice)  # unrelated authority: must stay live

    lifecycle.terminate(weft, root, "cap:lease")
    w = Weave.fold(weft)

    lease = w.get("cap:lease")
    assert lease is not None
    assert lease.retracted is True and lease.cascade_root is True
    assert lease.cascade_mode == "LEASE_TREE"
    assert lease.superseded_by is None, "TERMINATE is not a supersession"
    assert w.is_terminated("cap:lease") is True
    for cid in ("cap:sub", "cap:subsub"):
        d = w.get(cid)
        assert d is not None, cid
        assert d.retracted is True and d.cascaded is True, f"{cid} must fail closed"
    sib = w.get("cap:sibling")
    assert sib is not None and sib.retracted is False, "unrelated authority is untouched"


def test_terminated_capability_denies_authorization() -> None:
    """The cascade is not cosmetic: an INVOKE under a TERMINATEd grant fails closed at the
    authorization gate, exactly like a revoked one."""
    weft, _kr, root, alice = _setup()
    _cap(weft, root, "cap:lease", alice)
    _cap(weft, root, "cap:sub", alice, parent="cap:lease", granter=alice)
    _agent(weft, root, "agent:alice", alice, ["cap:lease", "cap:sub"])
    w0 = Weave.fold(weft)
    agent0 = w0.get("agent:alice")
    assert agent0 is not None
    assert authorize_decision(w0, agent0, "cap:sub", {}, alice).allowed

    lifecycle.terminate(weft, root, "cap:lease")
    w = Weave.fold(weft)
    agent = w.get("agent:alice")
    assert agent is not None
    for cid in ("cap:lease", "cap:sub"):
        d = authorize_decision(w, agent, cid, {}, alice)
        assert not d.allowed, cid
        assert d.reason_code == ReasonCode.REVOKED


def test_terminate_is_terminal_no_later_assert_resurrects_it() -> None:
    """TERMINATE moves the target to a TERMINAL state (FOLD §10): a later ASSERT — a
    status write, a re-registration, a replayed branch — cannot bring it back, and the
    subtree stays closed."""
    weft, _kr, root, alice = _setup()
    _cap(weft, root, "cap:lease", alice)
    _cap(weft, root, "cap:sub", alice, parent="cap:lease", granter=alice)
    lifecycle.terminate(weft, root, "cap:lease")
    # Someone re-asserts the terminated grant (or a status writer touches its cell).
    _cap(weft, root, "cap:lease", alice, caveats={"max_uses": 5})

    w = Weave.fold(weft)
    lease = w.get("cap:lease")
    sub = w.get("cap:sub")
    assert lease is not None and sub is not None
    assert lease.retracted is True, "a TERMINATEd cell must not be resurrected by an ASSERT"
    assert lease.cascade_root is True and lease.cascade_mode == "LEASE_TREE"
    assert w.is_terminated("cap:lease") is True
    assert sub.retracted is True and sub.cascaded is True, "the subtree stays closed"
    assert "cap:lease" not in {c.id for c in w.of_type("capability")}


def test_terminate_covers_a_descendant_asserted_after_it() -> None:
    """The cascade is a DERIVED pass over the folded graph, so authority minted UNDER an
    already-terminated root fails closed too (no window where a late child escapes)."""
    weft, _kr, root, alice = _setup()
    _cap(weft, root, "cap:lease", alice)
    lifecycle.terminate(weft, root, "cap:lease")
    _cap(weft, root, "cap:late", alice, parent="cap:lease", granter=alice)
    assert_content(weft, root, "cell:derived", "thing", {"derived_from": "cap:late"})

    w = Weave.fold(weft)
    for cid in ("cap:late", "cell:derived"):
        c = w.get(cid)
        assert c is not None, cid
        assert c.retracted is True and c.cascaded is True, f"{cid} must fail closed"


# ── determinism / replay ──────────────────────────────────────────────────────────
def _mixed_history(weft: Weft, root: str, alice: str) -> None:
    """One script exercising all four modes plus the two cascades."""
    assert_content(weft, root, "note:v1", "note", {"text": "first", "n": 1})
    assert_content(weft, root, "note:v2", "note", {"text": "second", "n": 2})
    assert_content(weft, root, "note:secret", "note", {"text": "pii", "n": 3})
    assert_content(weft, root, "note:gone", "note", {"text": "withdrawn", "n": 4})
    _cap(weft, root, "cap:lease", alice, caveats={"expires_at": 9_000})
    _cap(weft, root, "cap:sub", alice, parent="cap:lease", granter=alice)
    _cap(weft, root, "cap:root", alice)
    _cap(weft, root, "cap:derived", alice, parent="cap:root", granter=alice)
    weft.append(root, RETRACT, {"cell": "note:gone", "mode": "WITHDRAW"})
    lifecycle.redact(weft, root, "note:secret")
    lifecycle.supersede(weft, root, "note:v1", "note:v2")
    lifecycle.terminate(weft, root, "cap:lease")
    lifecycle.revoke(weft, root, "cap:root")
    # Post-termination traffic: a re-assert of the terminated grant and a late child.
    _cap(weft, root, "cap:lease", alice, caveats={"expires_at": 9_000, "max_uses": 2})
    _cap(weft, root, "cap:late", alice, parent="cap:lease", granter=alice)


def test_two_folds_and_an_incremental_fold_agree_on_the_state_root() -> None:
    """Replay determinism (FOLD §11.1): folding twice, and folding from a mid-history
    checkpoint, yield the identical state_root over a history that uses every mode."""
    weft, _kr, root, alice = _setup()
    _mixed_history(weft, root, alice)

    r1 = Weave.fold(weft).state_root()
    r2 = Weave.fold(weft).state_root()
    assert r1 == r2, "two folds of the same Weft diverged"

    head = weft.count()
    assert Weave.fold(weft, head).state_root() == r1
    frontier = max(1, head // 2)
    base = Weave.fold(weft, frontier).checkpoint()
    inc = Weave.fold_incremental(weft, base)
    assert inc.state_root() == r1, "incremental fold != genesis fold"
    assert inc.last_seq == head


def _mode_history(weft: Weft, root: str, alice: str) -> None:
    """A SUPERSEDE + TERMINATE history, including post-termination traffic (a re-assert of
    the terminated grant, a late child, a derived cell). Deliberately no plain
    WITHDRAW/REVOKE/REDACT: those are applied at fold time only if the target cell already
    exists, which the canonical (lamport, event_id) fold guarantees but a deliberately
    scrambled direct-`_apply` delivery does not — a pre-existing property of those modes,
    unrelated to the two implemented here (whose state is DERIVED, hence order-free)."""
    assert_content(weft, root, "note:v1", "note", {"text": "first", "n": 1})
    assert_content(weft, root, "note:v2", "note", {"text": "second", "n": 2})
    _cap(weft, root, "cap:lease", alice)
    _cap(weft, root, "cap:sub", alice, parent="cap:lease", granter=alice)
    _cap(weft, root, "cap:sibling", alice)
    lifecycle.supersede(weft, root, "note:v1", "note:v2")
    lifecycle.terminate(weft, root, "cap:lease")
    _cap(weft, root, "cap:lease", alice, caveats={"max_uses": 2})  # post-termination re-assert
    _cap(weft, root, "cap:late", alice, parent="cap:lease", granter=alice)
    assert_content(weft, root, "cell:derived", "thing", {"derived_from": "cap:late"})


def test_shuffled_and_duplicated_delivery_folds_to_one_state_root() -> None:
    """Arrival-order independence + duplicate harmlessness (FOLD §11.2/3) for the mode
    state: SUPERSEDE currency, TERMINATE terminality and the LEASE_TREE cascade are derived
    from the folded event SET and the canonical (lamport, event_id) order, never from the
    order events were handed to the reducer."""
    weft, _kr, root, alice = _setup()
    _mode_history(weft, root, alice)
    canonical = Weave.fold(weft)
    events = list(weft.events())

    for seed in range(12):
        rnd = random.Random(seed)
        delivered = list(events) + rnd.sample(events, k=rnd.randint(0, len(events)))
        rnd.shuffle(delivered)
        replay = Weave()
        for ev in delivered:
            replay._apply(ev)
        replay._ensure_cascade()
        assert replay.state_root() == canonical.state_root(), f"seed {seed} diverged"
        lease = replay.get("cap:lease")
        assert lease is not None and lease.retracted is True, "termination survives any order"
        assert lease.cascade_root is True and lease.cascade_mode == "LEASE_TREE"
        for cid in ("cap:sub", "cap:late", "cell:derived"):
            d = replay.get(cid)
            assert d is not None and d.retracted is True, f"{cid} must fail closed in any order"
        v1 = replay.get("note:v1")
        assert v1 is not None and v1.superseded_by == "note:v2"


def test_the_cascade_pass_is_idempotent_over_the_modes() -> None:
    """`_cascade_retractions` is re-runnable: calling it repeatedly on a folded Weave
    changes nothing (it clears what it derived, then recomputes)."""
    weft, _kr, root, alice = _setup()
    _mixed_history(weft, root, alice)
    w = Weave.fold(weft)
    before = w.state_root()
    for _ in range(3):
        w._cascade_retractions()
    assert w.state_root() == before, "re-deriving the cascade changed folded state"
