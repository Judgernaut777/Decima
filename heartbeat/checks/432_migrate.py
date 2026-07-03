"""SCHEMA MIGRATION — reproject Cell shapes forward on the Weft, never rewriting history.

The types-as-data model (Phase 1: TYPE_DEF is itself a Cell) evolves; `decima/migrate.py`
carries old-shape cells forward. Law 1 is what this check defends: a migration is itself
expressed ON the Weft — a declaration Cell + NEW LWW versions + provenance edges — and the
old shape is never retracted, erased, or rewritten. Old readers and `fold(upto_seq)` time-
travel still see the old shape; only the current projection shows the new one.

This check proves, offline + deterministically (fresh Kernel, no clock, no randomness):

  (a) FORWARD — define a type, assert cells in an OLD shape (`name`); declare + run a
      migration to a NEW shape (rename `name`→`title`, add a defaulted int `priority`);
      the current fold shows every migrated cell in the NEW shape with `schema_v = 2`;
  (b) HISTORY PRESERVED (load-bearing for Law 1) — the OLD-shape ASSERTs are still in the
      log (reachable, byte-for-byte content), NO RETRACT ever targeted a migrated cell,
      and each cell's version/provenance grew forward (a new version, not a rewrite);
  (c) TIME-TRAVEL — a fold at the PRE-migration frontier (`weave(upto_seq)`) still shows
      the OLD shape: the migration is forward-only, history is immutable;
  (d) IDEMPOTENT — re-running the same migration migrates 0 (all cells already at to_v);
      a LATE old-shape cell asserted afterwards is caught up by one more run (exactly 1);
  (e) PROVENANCE + INTS + ZERO AUTHORITY — the declaration Cell records type/from_v/to_v
      as ints with a content-addressed transform_id; `migrated` edges link it to every
      touched cell; run counts are ints; no capability was minted and no INVOKE fired;
  (f) FAIL CLOSED — a float-emitting transform is refused and migrates NOTHING (no
      partial write); a swapped-in transform that does not hash to the declared
      transform_id is refused; unknown types / float versions / non-forward versions are
      refused at declaration; a cell with a poisoned (non-int) schema_v marker is DATA,
      never migrated.

Mutation-resistance (the load-bearing line): neuter the from_v skip guard in
`migrate.migrate` (`if v != from_v:`) and (d) goes red — the re-run re-migrates every
already-migrated cell and reports nonzero.

Contract: run(k, line). Fail loud (assert / expected MigrateError). Owns a fresh Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.weft import ASSERT, RETRACT
from decima.hashing import content_id
from decima import model, migrate

TYPE = "memo432"          # a check-local type so the probe is hermetic


def _rename_and_default(old):
    """The declared PURE transform: rename `name` -> `title`, add a defaulted int
    `priority`. Top-level def so its source (its content-identity) is retrievable."""
    new = {k: v for k, v in old.items() if k != "name"}
    new["title"] = old.get("name", "")
    new["priority"] = old.get("priority", 5)
    return new


def _bad_float_transform(old):
    """An adversarial transform smuggling a float into signed content."""
    return {"title": old.get("name", ""), "priority": 2.5}


def _assert_int(x, what):
    assert isinstance(x, int) and not isinstance(x, bool), \
        f"{what} must be an int (ints-not-floats), got {type(x).__name__} {x!r}"


def run(k, line):
    line("\n== SCHEMA MIGRATION — reproject shapes forward on the Weft, history immutable ==")

    k2 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    author = k2.decima.id

    # Old-shape corpus: three v1 cells (no marker = baseline shape), plus one already
    # at v2 and one with a POISONED marker (untrusted data — must never migrate).
    model.define_type(k2.weft, author, TYPE)
    names = ["alpha", "beta", "gamma"]
    cells = {n: content_id({"memo432": n}) for n in names}
    for n, cid in cells.items():
        model.assert_content(k2.weft, author, cid, TYPE, {"name": n})
    already = content_id({"memo432": "already-new"})
    model.assert_content(k2.weft, author, already, TYPE,
                         {"title": "already-new", "priority": 9, "schema_v": 2})
    poisoned = content_id({"memo432": "poisoned"})
    model.assert_content(k2.weft, author, poisoned, TYPE,
                         {"name": "poisoned", "schema_v": "2"})   # non-int marker: DATA
    pre_seq = k2.weave().last_seq                    # the PRE-migration frontier
    caps_before = len(k2.weave().of_type("capability"))
    invokes_before = len(k2.weave().invocations)

    # ── (a) FORWARD — declare + run; migrated cells now hold the NEW shape. ───────────
    mig = migrate.define_migration(k2, TYPE, 1, 2, _rename_and_default)
    rep = migrate.migrate(k2, mig)
    assert rep["migrated"] == 3 and sorted(rep["cells"]) == sorted(cells.values()), \
        f"exactly the three v1 cells must migrate: {rep}"
    assert rep["skipped"] == 2, \
        f"the already-at-v2 cell AND the poisoned-marker cell are skipped: {rep}"
    now = k2.weave()
    for n, cid in cells.items():
        c = now.get(cid).content
        assert c == {"title": n, "priority": 5, "schema_v": 2}, \
            f"migrated cell must hold the NEW shape (title + int default + marker): {c}"
        assert "name" not in c, "the old field must be gone from the CURRENT projection"
    assert now.get(poisoned).content == {"name": "poisoned", "schema_v": "2"}, \
        "a poisoned (non-int) schema marker is untrusted DATA — the cell is never migrated"
    line("  forward: 3 old-shape (v1) cells reprojected to the new shape "
         "(name→title, defaulted int priority, schema_v=2); the v2 cell and the "
         "poisoned-marker cell were skipped ✓")

    # ── (b) HISTORY PRESERVED — old versions remain in the log; nothing retracted. ────
    events = list(k2.weft.events())
    for n, cid in cells.items():
        assert any(ev.verb == ASSERT and ev.body.get("cell") == cid
                   and ev.body.get("content") == {"name": n} for ev in events), \
            f"the OLD-shape ASSERT for {n} must still be reachable in the log (append-only)"
    assert not any(ev.verb == RETRACT and ev.body.get("cell") in set(cells.values())
                   for ev in events), \
        "a migration must never RETRACT — no erasure, no rewrite (Law 1)"
    for cid in cells.values():
        c = now.get(cid)
        assert c.version >= 2 and len(c.provenance) >= 2, \
            "migration is a NEW LWW version — the cell's fold grew forward, not rewritten"
    line("  history preserved: every OLD-shape ASSERT is still in the log byte-for-byte, "
         "no RETRACT touched a migrated cell, and each cell folded a NEW version forward ✓")

    # ── (c) TIME-TRAVEL — the pre-migration frontier still shows the OLD shape. ───────
    past = k2.weave(upto_seq=pre_seq)
    for n, cid in cells.items():
        pc = past.get(cid).content
        assert pc == {"name": n}, f"the pre-migration fold must show the OLD shape: {pc}"
        assert "title" not in pc, "the new shape must NOT exist at the old frontier"
    line("  time-travel: fold(upto_seq=pre-migration) shows {'name': ...} untouched — "
         "the migration is forward-only, history immutable ✓")

    # ── (d) IDEMPOTENT — a re-run migrates 0; a LATE old-shape cell is caught up. ─────
    again = migrate.migrate(k2, mig)
    assert again["migrated"] == 0 and again["cells"] == [], \
        f"re-running the same migration must migrate 0 (all at to_v): {again}"
    assert again["skipped"] == 5, f"all five cells are now skipped: {again}"
    late = content_id({"memo432": "late"})
    model.assert_content(k2.weft, author, late, TYPE, {"name": "late"})
    catchup = migrate.migrate(k2, mig)
    assert catchup["migrated"] == 1 and catchup["cells"] == [late], \
        f"a late old-shape cell is caught up by exactly one more run: {catchup}"
    assert k2.weave().get(late).content == {"title": "late", "priority": 5, "schema_v": 2}
    assert migrate.migrate(k2, mig)["migrated"] == 0, "and the run after that is 0 again"
    line("  idempotent: re-run migrates 0; a late old-shape cell is caught up by exactly "
         "one; then 0 again ✓")

    # ── (e) PROVENANCE + INTS + ZERO AUTHORITY. ───────────────────────────────────────
    w = k2.weave()
    decl = w.get(mig["cell"])
    assert decl.type == migrate.MIGRATION and decl.content["type"] == TYPE
    _assert_int(decl.content["from_v"], "migration.from_v")
    _assert_int(decl.content["to_v"], "migration.to_v")
    assert decl.content["transform_id"] == migrate.transform_identity(_rename_and_default), \
        "the declaration records the transform's content-identity (Law 4: identity is content)"
    touched = {e["dst"] for e in w.edges_from(mig["cell"], "migrated")}
    assert touched == set(cells.values()) | {late}, \
        f"the declaration must be edged to EVERY cell it touched (provenance): {touched}"
    assert any(e["rel"] == "migrates" and e["dst"] == w.types[TYPE]
               for e in w.edges_from(mig["cell"])), \
        "the declaration is edged to the TYPE_DEF cell it migrates"
    runs = [c for c in w.of_type(migrate.MIGRATION_RUN)
            if c.content.get("migration") == mig["cell"]]
    assert runs, "every run must land a migration_run report Cell"
    for r in runs:
        for key in ("migrated", "skipped", "at_seq", "from_v", "to_v"):
            _assert_int(r.content[key], f"migration_run.{key}")
        assert any(e["rel"] == "run_of" and e["dst"] == mig["cell"] for e in r.edges_out), \
            "each run report is edged run_of → its declaration"
    assert len(w.of_type("capability")) == caps_before, \
        "ZERO authority: a migration mints/grants NO capability"
    assert len(w.invocations) == invokes_before, \
        "ZERO authority: a migration fires NO effect (belief, not action)"
    line("  provenance + ints: declaration {type, from_v, to_v, transform_id} all "
         "ints/content-ids, `migrated` edges to every touched cell, run counts ints; "
         "no capability minted, no INVOKE fired ✓")

    # ── (f) FAIL CLOSED — bad transforms and bad declarations are refused at the door. ─
    bad = migrate.define_migration(k2, TYPE, 2, 3, _bad_float_transform)
    n_events = k2.weft.count()
    try:
        migrate.migrate(k2, bad)
        raise AssertionError("a float-emitting transform was accepted (ints-not-floats violated)")
    except migrate.MigrateError:
        pass
    assert k2.weft.count() == n_events, \
        "a refused transform must migrate NOTHING — validation precedes the first assert"
    assert k2.weave().get(cells["alpha"]).content["schema_v"] == 2, "cells are untouched"
    tampered = dict(mig)
    tampered["transform"] = _bad_float_transform      # swap the code, keep the declaration
    try:
        migrate.migrate(k2, tampered)
        raise AssertionError("a transform that does not hash to the declared transform_id ran")
    except migrate.MigrateError:
        pass
    for args in ((k2, "no_such_type_432", 1, 2, _rename_and_default),   # unknown type
                 (k2, TYPE, 1.0, 2, _rename_and_default),               # float version
                 (k2, TYPE, 2, 2, _rename_and_default)):                # not forward
        try:
            migrate.define_migration(*args)
            raise AssertionError(f"a bad declaration was accepted: {args[1:]}")
        except migrate.MigrateError:
            pass
    line("  fail closed: a float-emitting transform migrates NOTHING (no partial write); "
         "a swapped-in transform is refused by the declared transform_id; unknown type / "
         "float version / non-forward declarations are refused at the door ✓")

    line("  → schema migration is now ON the Weft: a declared, content-addressed transform "
         "reprojects old-shape cells to NEW LWW versions with provenance edges and int "
         "counts — history is never rewritten (old ASSERTs stay in the log, pre-migration "
         "folds still show the old shape), re-runs are idempotent, and every bad input "
         "fails closed before a single byte lands.")
