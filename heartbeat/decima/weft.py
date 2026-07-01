"""The Weft — the append-only, signed, content-addressed log.

Law 1: nothing happens off the Log. Every state change in Decima is one Event
appended here. There is no UPDATE and no DELETE — only INSERT. The four verbs
are the entire instruction set.

Storage is SQLite ("fine to start"), but the table is treated as append-only;
`seq` gives a total order for folding and time-travel.
"""
import sqlite3
from dataclasses import dataclass, field

from decima.hashing import content_id

# The entire instruction set. belief | action | trust.
# An ASSERT body may carry an optional `kind` (CONTENT | EDGE | TYPE_DEF),
# mapping to WEFT Protocol §4 `assertion` (1 CONTENT, 2 EDGE, 8 TYPE_DEF). The
# verb set stays four; the body shape is opaque to `append` and read by the fold.
ASSERT = "ASSERT"     # bring a fact/version of a Cell into being
RETRACT = "RETRACT"   # withdraw a prior assertion. body `mode` (WEFT §5):
                      #   WITHDRAW  — default tombstone (the cell leaves projections;
                      #               its payload is still recoverable from the events);
                      #   REVOKE    — a capability WITHDRAW (fails closed via cascade);
                      #   SUPERSEDE — tombstone + record the `replacement` that took its
                      #               place; payload NOT erased, no cascade by default;
                      #   REDACT    — also ERASE the payload from every projection
                      #               (FOLD §10); the event skeleton stays on the Log;
                      #   TERMINATE — hard shutdown: fail closed the whole lease tree
                      #               descending from the cell (default LEASE_TREE cascade).
                      # Never a delete: the event remains.
                      # body `cascade` (WEFT §5):
                      #   NONE               — default; affects only the target;
                      #   DERIVED_AUTHORITY  — fail closed every grant/lease/cell whose
                      #                        authority DESCENDS from the target
                      #                        (capability revocation — FOLD §10.2);
                      #   LEASE_TREE         — a TERMINATE's cascade; fails closed the
                      #                        authority-descendants exactly like above.
                      # The fold defaults a capability RETRACT to DERIVED_AUTHORITY and a
                      # TERMINATE to LEASE_TREE; the descendant marking is derived in weave.py.
INVOKE = "INVOKE"     # request an effect in the world through a capability
ATTEST = "ATTEST"     # witness/sign another event or cell (verification, trust, promotion)
VERBS = (ASSERT, RETRACT, INVOKE, ATTEST)


@dataclass
class Event:
    seq: int | None
    id: str
    parents: list
    author: str            # principal id
    authorized: str | None  # capability cell id that permitted this (provenance of power)
    verb: str
    body: dict
    lamport: int
    sig: str

    def hashed_payload(self) -> dict:
        # Everything that defines the event's identity (content + cause).
        # The signature is NOT part of the id — it attests authorship of the id.
        return {
            "parents": self.parents,
            "author": self.author,
            "authorized": self.authorized,
            "verb": self.verb,
            "body": self.body,
            "lamport": self.lamport,
        }


class WeftError(Exception):
    pass


class Weft:
    def __init__(self, db_path: str, keyring):
        self.keyring = keyring
        self.db = sqlite3.connect(db_path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   id TEXT UNIQUE NOT NULL,
                   payload TEXT NOT NULL,
                   author TEXT NOT NULL,
                   sig TEXT NOT NULL
               )"""
        )
        self.db.commit()
        self.head, self.lamport = self._load_head()

    def _load_head(self):
        row = self.db.execute(
            "SELECT id, payload FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None, 0
        import json
        return row[0], json.loads(row[1])["lamport"]

    def append(self, author_pid: str, verb: str, body: dict,
               authorized: str | None = None, parents: list | None = None) -> Event:
        if verb not in VERBS:
            raise WeftError(f"unknown verb {verb!r}")
        # `parents=None` is the linear default: descend from the current head.
        # Passing an explicit parent set appends a CONCURRENT event — a fork — used
        # by the merge layer (the only place a non-linear frontier is created).
        # Lamport follows WEFT §2: 1 + max(parent.lamport), 0-base for genesis; on
        # the linear path this is exactly the old `self.lamport + 1`.
        if parents is None:
            parents = [self.head] if self.head else []
            parent_lamports = [self.lamport] if self.head else []
        else:
            parents = sorted(parents)          # canonical frontier (WEFT §2: parents sorted)
            parent_lamports = [self._lamport_of(p) for p in parents]
        lamport = 1 + max(parent_lamports, default=0)
        payload = {
            "parents": parents,
            "author": author_pid,
            "authorized": authorized,
            "verb": verb,
            "body": body,
            "lamport": lamport,
        }
        eid = content_id(payload, kind="event")
        sig = self.keyring.sign(author_pid, eid)
        import json
        self.db.execute(
            "INSERT INTO events (id, payload, author, sig) VALUES (?,?,?,?)",
            (eid, json.dumps(payload, sort_keys=True), author_pid, sig),
        )
        self.db.commit()
        self.head = eid
        self.lamport = lamport
        ev = self._row_to_event(self._seq_of(eid), eid, payload, author_pid, sig)
        return ev

    def _seq_of(self, eid: str) -> int:
        return self.db.execute("SELECT seq FROM events WHERE id=?", (eid,)).fetchone()[0]

    def _lamport_of(self, eid: str) -> int:
        """The lamport of a stored event (for computing a fork's lamport from an
        explicit parent set). Linear appends never need this — they reuse the
        in-memory head lamport."""
        import json
        row = self.db.execute("SELECT payload FROM events WHERE id=?", (eid,)).fetchone()
        return json.loads(row[0])["lamport"] if row else 0

    @staticmethod
    def _row_to_event(seq, eid, payload, author, sig) -> Event:
        return Event(
            seq=seq, id=eid, parents=payload["parents"], author=author,
            authorized=payload["authorized"], verb=payload["verb"],
            body=payload["body"], lamport=payload["lamport"], sig=sig,
        )

    def events(self, upto_seq: int | None = None, from_seq: int | None = None):
        """Yield events in causal (seq) order, VERIFYING each as we read it.

        This is where Laws 1 & 4 are enforced on read: recompute the content id
        and check the author's signature. Tampering with the log is detected.

        `from_seq` windows the read to events with `seq > from_seq` — the tail above
        a snapshot frontier — so an incremental fold reads/verifies only the new
        events, not the whole log (IFB1). `from_seq=None` reads from genesis."""
        import json
        q = "SELECT seq, id, payload, author, sig FROM events"
        clauses, args = [], []
        if upto_seq is not None:
            clauses.append("seq <= ?")
            args.append(upto_seq)
        if from_seq is not None:
            clauses.append("seq > ?")
            args.append(from_seq)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        args = tuple(args)
        q += " ORDER BY seq ASC"
        for seq, eid, payload_text, author, sig in self.db.execute(q, args):
            payload = json.loads(payload_text)
            if content_id(payload, kind="event") != eid:
                raise WeftError(f"content tampered at seq {seq}: id mismatch")
            if not self.keyring.verify(author, eid, sig):
                raise WeftError(f"bad signature at seq {seq}")
            yield self._row_to_event(seq, eid, payload, author, sig)

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
