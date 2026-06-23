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
ASSERT = "ASSERT"     # bring a fact/version of a Cell into being
RETRACT = "RETRACT"   # withdraw a prior assertion (tombstone; never a delete)
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
               authorized: str | None = None) -> Event:
        if verb not in VERBS:
            raise WeftError(f"unknown verb {verb!r}")
        lamport = self.lamport + 1
        parents = [self.head] if self.head else []
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

    @staticmethod
    def _row_to_event(seq, eid, payload, author, sig) -> Event:
        return Event(
            seq=seq, id=eid, parents=payload["parents"], author=author,
            authorized=payload["authorized"], verb=payload["verb"],
            body=payload["body"], lamport=payload["lamport"], sig=sig,
        )

    def events(self, upto_seq: int | None = None):
        """Yield events in causal (seq) order, VERIFYING each as we read it.

        This is where Laws 1 & 4 are enforced on read: recompute the content id
        and check the author's signature. Tampering with the log is detected.
        """
        import json
        q = "SELECT seq, id, payload, author, sig FROM events"
        args = ()
        if upto_seq is not None:
            q += " WHERE seq <= ?"
            args = (upto_seq,)
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
