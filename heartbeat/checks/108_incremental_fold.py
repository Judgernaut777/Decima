"""IFB1 — incremental fold-from-base: the snapshot perf win, provably ≡ genesis.

Folding from genesis is O(all events); a long-running OS log grows unbounded. An
incremental fold resumes from a verified checkpoint at frontier F and applies only
the events after F — O(tail) — while producing the SAME state_root as a full
genesis fold (FOLD §11.1). Proves:
  - incremental(base@F + events>F).state_root == genesis fold to head;
  - the base is verified against a trusted snapshot root — a tampered base is rejected;
  - re-delivering the tail is idempotent (duplicate events change nothing).

The snapshot (SN1) supplies the trusted materialized root (it explicitly deferred
this continuation); the checkpoint adds the reducer substrate needed to continue an
OR-set/Counter/Sequence/Map fold. Contract: run(k, line). Fail loud.
"""
from decima import snapshot
from decima.weave import Weave


def run(k, line):
    line("\n== INCREMENTAL FOLD-FROM-BASE (resume from a verified snapshot; ≡ genesis) ==")
    wf = k.weft
    head = wf.count()
    F = max(1, head // 2)                       # a mid-history frontier

    # The trusted root at F: a snapshot (SN1), restored + verified end-to-end.
    manifest, store = snapshot.snapshot(wf, F, created_by=k.executor.id, keyring=k.keyring)
    restored = snapshot.restore(manifest, store, keyring=k.keyring)
    trusted_root = manifest["state_root"]
    assert restored.state_root() == trusted_root

    # Checkpoint the FULL fold state at F (cells + reducer substrate) — what an
    # incremental fold resumes from. (Its materialized root equals the snapshot's.)
    base_ckpt = Weave.fold(wf, F).checkpoint()

    # Incremental fold: base@F + only the events after F, verified against the
    # trusted snapshot root → must equal a full genesis fold to head.
    inc = Weave.fold_incremental(wf, base_ckpt, verify_root=trusted_root)
    genesis = Weave.fold(wf)
    assert inc.state_root() == genesis.state_root(), "incremental != genesis fold"
    assert inc.last_seq == genesis.last_seq == head, (inc.last_seq, genesis.last_seq, head)
    line(f"  snapshot@e{F}; incremental(base@{F} + {head - F} tail events) "
         f"state_root == genesis fold to e{head} ✓  (FOLD §11.1)")

    # A tampered base is rejected: mutate a cell in the checkpoint → its state_root
    # no longer matches the trusted snapshot root.
    bad = Weave.fold(wf, F).checkpoint()
    victim = next(iter(bad["cells"].values()))
    victim.content = {**victim.content, "__tamper__": True}
    try:
        Weave.fold_incremental(wf, bad, verify_root=trusted_root)
        assert False, "tampered base was NOT rejected"
    except ValueError as e:
        line(f"  tampered base → rejected: {e}")

    # Duplicate delivery is harmless: re-apply the entire tail onto the incremental
    # fold a second time → state_root unchanged (idempotent by Event ID).
    root_once = inc.state_root()
    for ev in wf.events(from_seq=F):
        inc._apply(ev)
    assert inc.state_root() == root_once, "duplicate delivery changed state"
    line(f"  re-delivering the tail ({head - F} events) is idempotent — state_root stable ✓")

    line("  → a fold resumes from a verified base in O(tail), provably equal to "
         "genesis. Full-fold remains the default; this is the verifiable cache.")
