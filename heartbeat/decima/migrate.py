"""SCHEMA MIGRATION — reproject Cell shapes as the types-as-data model evolves (Phase 4).

Types are DATA on the Weft (Law 3: a TYPE_DEF is itself a Cell), so when the domain
model evolves, the migration must be data too — declared, applied, and audited ON the
log it migrates. Law 1 is the law this lane keeps: a migration NEVER rewrites history.
There is no UPDATE and no DELETE — reprojection is a fan of NEW `assert_content`
versions (LWW forward), so every old-shape version stays in the log, an old reader or
a `fold(upto_seq)` time-travel still sees the old shape at its frontier, and only the
current projection shows the new shape.

Two moves, both pure composition over public APIs (`model.assert_content`,
`model.assert_edge`, `kernel.weave`, the hashing seam):

  - `define_migration(k, type_name, from_v, to_v, transform)` records a migration
    DECLARATION Cell `{type, from_v, to_v, transform_id}`. The transform is a PURE
    function `old_content(dict) -> new_content(dict)`; its content-address
    (`transform_identity`, Law 4: identity is content) is what the declaration
    records — the transform is data/config, NEVER authority.
  - `migrate(k, migration)` reprojects every CURRENT cell of the type at shape
    `from_v` to a new LWW version carrying the transformed content + the `schema_v`
    marker `to_v`, links the declaration to each touched cell (a `migrated` EDGE —
    provenance), and records a `migration_run` Cell with int counts. Idempotent: a
    cell already at `to_v` (or any shape ≠ `from_v`) is SKIPPED, so a re-run
    migrates 0.

Fail closed and deterministic throughout: unknown types are refused at declaration;
a supplied transform whose hash does not match the declared `transform_id` is refused
at apply time; every transformed content is validated (dict shape, NO floats — ints
only in signed content) for ALL cells BEFORE the first assert lands, so a bad
transform migrates nothing. No wall-clock — the run is stamped with the log frontier
(`at_seq`, a logical int). Zero authority: a migration mints no capability and fires
no effect; it is belief (ASSERT), not action (INVOKE).
"""
from __future__ import annotations

import copy
import inspect
import textwrap

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, blob_id

MIGRATION = "migration"          # the declaration Cell type
MIGRATION_RUN = "migration_run"  # the per-run report Cell type
SCHEMA_KEY = "schema_v"          # the in-content shape-version marker (an int)
BASELINE_SCHEMA_V = 1            # an unmarked cell is at the type's original shape


class MigrateError(Exception):
    """A migration was refused at the door (fail closed): unknown type, non-int
    version, transform mismatch, or a transform emitting non-Weft-safe content."""


def _int(name: str, v) -> int:
    """Admit only a real int (bool is not a version). INTS-NOT-FLOATS at the door."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise MigrateError(f"{name} must be an int, got {type(v).__name__} {v!r}")
    return v


def _reject_floats(obj, path="content"):
    """No float may enter signed content (PROFILE.md: floats forbidden). Recursive:
    a float hiding in a nested list/dict is refused just the same."""
    if isinstance(obj, float):
        raise MigrateError(f"float at {path} — ints-not-floats: no float enters signed content")
    if isinstance(obj, dict):
        for kk, vv in obj.items():
            _reject_floats(vv, f"{path}.{kk}")
    elif isinstance(obj, (list, tuple)):
        for i, vv in enumerate(obj):
            _reject_floats(vv, f"{path}[{i}]")


def transform_identity(transform) -> str:
    """The content-address of a transform (Law 4: identity is content + cause).

    The declaration records WHICH pure function the migration runs, as data — its
    source (dedented; falling back to code-object bytes for builtins/callables with
    no retrievable source). Recording the hash — not the function — keeps the
    transform config, never authority: `migrate` re-derives this from the callable
    it is handed and refuses a mismatch (fail closed)."""
    if not callable(transform):
        raise MigrateError("transform must be a pure callable old_content -> new_content")
    try:
        src = textwrap.dedent(inspect.getsource(transform)).strip()
    except (OSError, TypeError):
        code = getattr(transform, "__code__", None)
        if code is None:
            raise MigrateError("transform has no retrievable identity (source or code)")
        src = code.co_code.hex() + repr(code.co_consts) + repr(code.co_names)
    return blob_id(src.encode("utf-8"), kind="transform")


def shape_version(content) -> int | None:
    """The shape version a cell's content claims: its int `schema_v` marker, or
    BASELINE_SCHEMA_V when unmarked (the type's original shape). A NON-INT marker is
    untrusted DATA, never instruction — it returns None so the cell matches no
    migration and is left untouched (fail closed, counted `skipped`)."""
    if not isinstance(content, dict):
        return None
    v = content.get(SCHEMA_KEY, BASELINE_SCHEMA_V)
    if not isinstance(v, int) or isinstance(v, bool):
        return None
    return v


def define_migration(k, type_name: str, from_v, to_v, transform) -> dict:
    """Record a migration DECLARATION Cell on the Weft and return the migration
    handle `{cell, type, from_v, to_v, transform, transform_id}`.

    The declaration Cell carries `{type, from_v, to_v, transform_id}` — all data,
    all ints where numeric — and is content-addressed by that identity, so re-
    declaring the same migration is idempotent (same cell, one identity). It is
    edged `migrates → TYPE_DEF cell`, so the type's own Cell shows every migration
    ever declared against it. Fail closed: the type must already be declared
    (`model.define_type`), versions must be ints, and a migration is FORWARD-ONLY
    (`to_v > from_v`) — history is immutable, there is no downgrade-by-rewrite."""
    from_v = _int("from_v", from_v)
    to_v = _int("to_v", to_v)
    if to_v <= from_v:
        raise MigrateError(f"forward-only: to_v ({to_v}) must be > from_v ({from_v})")
    weave = k.weave()
    if type_name not in weave.types:
        raise MigrateError(f"unknown type {type_name!r} — define_type it first (fail closed)")
    tid = transform_identity(transform)
    cid = content_id({"migration": [type_name, from_v, to_v, tid]})
    assert_content(k.weft, k.decima.id, cid, MIGRATION, {
        "type": type_name, "from_v": from_v, "to_v": to_v, "transform_id": tid,
    })
    assert_edge(k.weft, k.decima.id, cid, "migrates", weave.types[type_name])
    return {"cell": cid, "type": type_name, "from_v": from_v, "to_v": to_v,
            "transform": transform, "transform_id": tid}


def migrate(k, migration) -> dict:
    """Apply a declared migration: reproject every CURRENT cell of the type at shape
    `from_v` to a NEW LWW version (transformed content + `schema_v = to_v`), and
    record the run. Returns::

        {"migration", "run", "type", "from_v", "to_v",
         "migrated": int, "skipped": int, "at_seq": int, "cells": [ids...]}

    Law 1 — append-only, never rewrites: the ONLY writes are `assert_content` (a new
    version; the old versions stay in the log and at every pre-migration `upto_seq`
    frontier), `assert_edge` (declaration → touched cell, provenance), and the
    `migration_run` report Cell. Nothing is RETRACTed or erased.

    Fail closed: the declaration must be live on the Weft; the supplied transform
    must hash to the DECLARED `transform_id` (a swapped-in transform is refused —
    the log, not the caller, says what this migration does); and every transformed
    content is validated (dict, no floats) for ALL cells BEFORE the first assert,
    so a bad transform migrates nothing at all.

    Idempotent: a cell whose shape is not `from_v` — already at `to_v`, past it, or
    carrying an untrusted marker — is skipped; a re-run reports `migrated == 0`.
    Deterministic: cells are processed in sorted-id order; the run is stamped with
    the pre-run log frontier `at_seq` (a logical int, never a wall-clock).
    Zero authority: no capability is minted, granted, or invoked."""
    if not isinstance(migration, dict) or "cell" not in migration:
        raise MigrateError("migrate takes the handle define_migration returned")
    weave = k.weave()
    mig = weave.get(migration["cell"])
    if mig is None or mig.type != MIGRATION or mig.retracted:
        raise MigrateError("migration declaration is not live on the Weft (fail closed)")
    decl = mig.content
    transform = migration.get("transform")
    if transform_identity(transform) != decl["transform_id"]:
        raise MigrateError("transform does not hash to the declared transform_id (fail closed)")
    type_name = decl["type"]
    from_v, to_v = _int("from_v", decl["from_v"]), _int("to_v", decl["to_v"])
    at_seq = weave.last_seq

    # Phase 1 — plan + validate EVERYTHING before the first byte lands (fail closed:
    # a transform that misbehaves on any cell migrates none).
    planned, skipped = [], 0
    for cell in sorted(weave.of_type(type_name), key=lambda c: c.id):
        v = shape_version(cell.content)
        if v != from_v:   # already at (or past) another shape — skip: idempotent, forward-only
            skipped += 1
            continue
        new = transform(copy.deepcopy(cell.content))   # deepcopy: a transform cannot reach the fold
        if not isinstance(new, dict):
            raise MigrateError(f"transform must return a dict, got {type(new).__name__}")
        _reject_floats(new)
        new = dict(new)
        new[SCHEMA_KEY] = to_v                         # the version marker of the new shape
        planned.append((cell.id, new))

    # Phase 2 — assert the new versions (LWW forward) + provenance edges. Append-only.
    for cid, new in planned:
        assert_content(k.weft, k.decima.id, cid, type_name, new)
        assert_edge(k.weft, k.decima.id, mig.id, "migrated", cid)

    # The run report is itself a Cell — counts as INTS, stamped with the logical
    # frontier, edged back to the declaration it enacted.
    run_id = content_id({"migration_run": [mig.id, at_seq]})
    report = {"migration": mig.id, "type": type_name, "from_v": from_v, "to_v": to_v,
              "migrated": len(planned), "skipped": skipped, "at_seq": at_seq}
    assert_content(k.weft, k.decima.id, run_id, MIGRATION_RUN, report)
    assert_edge(k.weft, k.decima.id, run_id, "run_of", mig.id)
    return {**report, "run": run_id, "cells": [cid for cid, _ in planned]}
