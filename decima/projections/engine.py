"""The projection engine — disposable read-models over the Weft (invariant 2/5).

A projection is a DERIVED VIEW: a cache in the Law-5 sense (a rebuildable
projection, never authoritative state). The Weft is the sole canonical store
(invariant 1); everything here is folded from it and can be thrown away and
rebuilt byte-identically at any time. Nothing in this package appends to the
Weft, mints authority, or executes anything — it only READS the signed log.

The contract every read-model implements (`Projection`):

  * ``name``    — a stable identifier (its key in a driver / on disk).
  * ``version`` — an int schema version. Bumping it forces a clean rebuild
                  (migration-by-rebuild): the old materialization is discarded
                  and replayed, because a projection is never migrated in place.
  * ``reset()`` — drop all derived state, returning to the empty projection.
  * ``apply(event)`` — fold ONE Weft event into the derived state, in seq order.
  * ``checkpoint()`` — a ``ProjectionCheckpoint`` (name, version, last_seq, and a
                  deterministic ``state_root`` over the derived view).

Because ``apply`` is a deterministic function applied in the log's ``seq`` order,
an INCREMENTAL update (feed the tail since ``last_seq``) and a FULL REBUILD (reset
then replay every event) always converge on the same state — the property the
acceptance test pins. Two rebuilds of the same Weft yield an equal ``state_root``.

``FoldState`` is the shared reducer: a small, disposable fold of the linear log
into live Cells (LWW content, retraction tombstones, edges, attestations,
invocations) — exactly the slice the read-models need. It is a projection, never
the canonical store: for the linear domain logs the runtime writes, its per-cell
result equals the kernel ``Weave`` fold (a fidelity test asserts this), but it is
always rebuildable from the Weft and holds no authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, cast, runtime_checkable

from decima.kernel.hashing import content_id
from decima.kernel.weft import ASSERT, ATTEST, INVOKE, RETRACT, Event, Weft


# ── the shared reducer: a disposable fold into live Cells ─────────────────────
@dataclass
class PCell:
    """A live Cell in a projection's fold. A disposable materialization — the
    canonical truth is the Weft events whose ids are in ``provenance``."""

    id: str
    type: str
    content: dict = field(default_factory=dict)
    retracted: bool = False
    provenance: list[str] = field(default_factory=list)
    edges_out: list[dict] = field(default_factory=list)
    edges_in: list[dict] = field(default_factory=list)
    attestations: list[dict] = field(default_factory=list)


class FoldState:
    """A minimal, disposable fold of the linear Weft into live Cells.

    Mirrors ``decima.kernel.weave`` for the linear / last-writer-wins case the
    runtime produces: an ASSERT upserts a cell's content (LWW), a RETRACT
    tombstones it (REDACT also erases the payload), an EDGE is folded onto both
    endpoints, an INVOKE is recorded, and an ATTEST appends to its target. It is
    NOT a second canonical store — it is recomputed from the Weft on demand and
    discarded freely (invariant 2)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.cells: dict[str, PCell] = {}
        self.invocations: list[dict] = []
        self.frontier_lamport: int = 0

    def _ensure(self, cid: str, type_: str = "thing") -> PCell:
        cell = self.cells.get(cid)
        if cell is None:
            cell = PCell(id=cid, type=type_)
            self.cells[cid] = cell
        return cell

    def apply(self, ev: Event) -> None:
        lamport = getattr(ev, "lamport", 0) or 0
        if lamport > self.frontier_lamport:
            self.frontier_lamport = lamport
        body = ev.body if isinstance(ev.body, dict) else {}
        verb = ev.verb

        if verb == ASSERT:
            kind = body.get("kind", "CONTENT")
            if kind == "EDGE":
                src, rel, dst = body.get("src"), body.get("rel"), body.get("dst")
                if not (isinstance(src, str) and isinstance(dst, str)):
                    return
                s, d = self._ensure(src), self._ensure(dst)
                key = (rel, src, dst)
                if not any((e["rel"], e["src"], e["dst"]) == key for e in s.edges_out):
                    edge = {"rel": rel, "src": src, "dst": dst, "event": ev.id}
                    s.edges_out.append(edge)
                    d.edges_in.append(edge)
                return
            cid = body.get("cell")
            if not isinstance(cid, str):
                return
            cell = self._ensure(cid, body.get("type", "thing"))
            cell.type = body.get("type", cell.type)
            cell.content = body.get("content", {})  # LWW overwrite on the linear log
            cell.retracted = False
            cell.provenance.append(ev.id)

        elif verb == RETRACT:
            retracted_cell = self.cells.get(cast(str, body.get("cell")))
            if retracted_cell is not None:
                retracted_cell.retracted = True
                retracted_cell.provenance.append(ev.id)
                if body.get("mode") == "REDACT":
                    retracted_cell.content = {}

        elif verb == INVOKE:
            self.invocations.append(
                {
                    "event": ev.id,
                    "by": ev.author,
                    "cap": body.get("cap"),
                    "args": body.get("args", {}),
                }
            )

        elif verb == ATTEST:
            target = self.cells.get(cast(str, body.get("target_cell")))
            if target is not None:
                target.attestations.append(
                    {"by": ev.author, "claim": body.get("claim", ""), "event": ev.id}
                )

    # -- read helpers (deterministic) --------------------------------------
    def of_type(self, type_: str) -> list[PCell]:
        return [c for c in self.cells.values() if c.type == type_ and not c.retracted]

    def get(self, cid: str) -> PCell | None:
        return self.cells.get(cid)

    def type_of(self, cid: str | None) -> str | None:
        c = self.cells.get(cid) if cid else None
        return c.type if c is not None else None


# ── the projection contract ───────────────────────────────────────────────────
@dataclass(frozen=True)
class ProjectionCheckpoint:
    """A frontier commitment for a projection: which log prefix it reflects
    (``last_seq``), under which ``version``, producing which deterministic
    ``state_root``. A checkpoint is comparable state — two rebuilds of the same
    Weft produce an equal one — but it is NOT canonical; it only witnesses a
    disposable view."""

    name: str
    version: int
    last_seq: int
    state_root: str


@runtime_checkable
class Projection(Protocol):
    name: str
    version: int
    last_seq: int

    def reset(self) -> None: ...

    def apply(self, event: Event) -> None: ...

    def checkpoint(self) -> ProjectionCheckpoint: ...


class BaseProjection:
    """Common machinery for the read-models: a shared ``FoldState`` plus a
    deterministic ``state_root`` over the subclass's ``view()``. Subclasses
    implement ``view()`` (a JSON-safe, deterministically ordered structure) and
    their own query methods over ``self.fold``."""

    name: str = "base"
    version: int = 1

    def __init__(self) -> None:
        self.fold = FoldState()
        self.last_seq: int = 0
        self.reset()

    def reset(self) -> None:
        self.fold.reset()
        self.last_seq = 0

    def apply(self, event: Event) -> None:
        self.fold.apply(event)
        seq = getattr(event, "seq", None)
        if isinstance(seq, int):
            self.last_seq = seq

    def view(self) -> object:
        raise NotImplementedError

    def state_root(self) -> str:
        return content_id(
            {"projection": self.name, "version": int(self.version), "view": self.view()},
            kind="projection",
        )

    def checkpoint(self) -> ProjectionCheckpoint:
        return ProjectionCheckpoint(
            name=self.name,
            version=int(self.version),
            last_seq=int(self.last_seq),
            state_root=self.state_root(),
        )


# ── the driver ────────────────────────────────────────────────────────────────
class ProjectionDriver:
    """Pumps signed Weft events into registered projections.

    Supports the four things the engine promises:
      * INCREMENTAL update — ``update`` feeds only the tail (``seq > last_seq``),
        which ``weft.events(from_seq=...)`` reads (and verifies) without rescanning
        the whole log;
      * FULL REBUILD — ``rebuild`` resets a projection and replays every event, so
        the derived state is reproduced from the Weft alone;
      * MIGRATION BY REBUILD — if a projection's ``version`` differs from the one
        last built, ``update`` rebuilds it instead of an in-place migration;
      * LAG reporting — ``lag`` is the number of committed events a projection has
        not yet folded.

    It reads the Weft only through the public ``events``/``count`` seams, so the
    log stays append-only and its tamper-evidence (signature check on read) rides
    along — a tampered log raises out of ``events`` rather than silently feeding a
    forged event into a view."""

    def __init__(self, weft: Weft) -> None:
        self.weft = weft
        self._projections: dict[str, Projection] = {}
        self._built_version: dict[str, int] = {}

    def register(self, projection: Projection, *, rebuild: bool = True) -> None:
        self._projections[projection.name] = projection
        if rebuild:
            self.rebuild(projection.name)

    def get(self, name: str) -> Projection:
        return self._projections[name]

    def names(self) -> list[str]:
        return sorted(self._projections)

    def rebuild(self, name: str) -> ProjectionCheckpoint:
        """Discard and replay: the FULL REBUILD from an empty projection. The
        result is a pure function of the Weft — no incremental history matters."""
        p = self._projections[name]
        p.reset()
        for ev in self.weft.events():
            p.apply(ev)
        self._built_version[name] = int(p.version)
        return p.checkpoint()

    def update(self, name: str | None = None) -> dict[str, ProjectionCheckpoint]:
        """Bring projection(s) current. A version bump triggers a clean rebuild
        (migration-by-rebuild); otherwise only the tail since ``last_seq`` is
        folded (incremental)."""
        targets = [name] if name is not None else list(self._projections)
        out: dict[str, ProjectionCheckpoint] = {}
        for n in targets:
            p = self._projections[n]
            if self._built_version.get(n) != int(p.version):
                out[n] = self.rebuild(n)
                continue
            for ev in self.weft.events(from_seq=p.last_seq):
                p.apply(ev)
            out[n] = p.checkpoint()
        return out

    def frontier(self) -> int:
        """The seq of the log's head — the append-only, gapless event count."""
        return int(self.weft.count())

    def lag(self, name: str) -> int:
        """Events committed to the Weft that this projection has not yet folded."""
        return self.frontier() - int(self._projections[name].last_seq)
