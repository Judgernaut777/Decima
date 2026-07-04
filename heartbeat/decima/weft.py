"""The Weft — the append-only, signed, content-addressed log.

Law 1: nothing happens off the Log. Every state change in Decima is one Event
appended here. There is no UPDATE and no DELETE — only INSERT. The four verbs
are the entire instruction set.

Storage is SQLite ("fine to start"), but the table is treated as append-only;
`seq` gives a total order for folding and time-travel.

Verification is ROTATION-AWARE (Cycle 54's succession chain, made live): an
author enrolled on a key_rotation chain is verified against the key valid AT
each event's logical point — old events under the old key, post-rotation events
under the new key, a retired key refused — so an identity survives its keys and
its whole history keeps verifying. An author that never rotates (every existing
principal) verifies exactly as before, through the one-key Keyring.
"""
import sqlite3
from dataclasses import dataclass, field

from decima.hashing import content_id, nfc_deep

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
        # ROTATION-AWARE VERIFICATION STATE (Cycle 54 made live): per-author
        # succession chains folded from the log's own key_rotation Cells, so
        # verifying an event can consult the key valid AT that event's logical
        # point instead of a one-key-forever Keyring. An author with NO chain —
        # every existing principal — never touches this and verifies exactly as
        # before. `_rot_chains` maps principal_ref -> {"links": [(key_hex,
        # from_point, seq), ...], "recovery": key_hex | None}; links only enter
        # after rotation._valid_link re-verifies their endorsement (a forged
        # link is DATA on the log, never a successor). Kept current by the two
        # INSERT paths (`append`/`ingest`); a warm start re-folds from the log.
        self._rot_chains: dict = {}
        self.head, self.lamport = self._load_head()
        self._rot_scan()

    # ── the succession chain, folded at the weft (rotation made live) ──────────
    #
    # Layering: rotation cells are themselves weft events, and the weft sits
    # BELOW the weave — so the chain is folded HERE, incrementally, as rotation
    # links land on the log (never by folding the weave from inside the weft).
    # Link validation is rotation.py's own `_valid_link` (lazy import: rotation
    # composes over the weft), so the weft weaves in exactly the links the
    # weave-level `key_history` fold would. Registering/rotating a key confers
    # NO authority (Law 2): this projection decides only which PUBLIC key
    # verifies an author's signature at a logical point — never who may do what.

    def _rot_scan(self):
        """Warm start: re-fold the succession chains from an existing log. The
        LIKE prefilter is a cheap SUPERSET screen (a key_rotation payload always
        contains the literal type string); `_rot_apply` does the real check."""
        import json
        for seq, payload_text in self.db.execute(
                "SELECT seq, payload FROM events WHERE payload LIKE ? ORDER BY seq ASC",
                ('%"key_rotation"%',)):
            try:
                payload = json.loads(payload_text)
            except (ValueError, TypeError):
                continue
            self._rot_apply(seq, payload)

    def _rot_apply(self, seq: int, payload: dict):
        """Fold ONE stored event into the succession chains iff it is an ASSERT
        carrying a key_rotation Cell whose link VERIFIES as the next link of its
        principal's chain (rotation._valid_link — the same fail-closed
        endorsement check the weave-level fold uses). Anything else — ordinary
        events, forged/unendorsed links, replays — is inert here: the chain only
        ever advances on a verified endorsement (fail closed)."""
        if not isinstance(payload, dict) or payload.get("verb") != ASSERT:
            return
        body = payload.get("body")
        if not isinstance(body, dict) or body.get("type") != "key_rotation":
            return
        content = body.get("content")
        if not isinstance(content, dict):
            return
        ref = content.get("principal")
        if not isinstance(ref, str):
            return
        from decima import rotation
        st = self._rot_chains.get(ref, {"links": [], "recovery": None})
        links = st["links"]
        cur_key, cur_fp = (links[-1][0], links[-1][1]) if links else (None, None)
        if not rotation._valid_link(content, ref, len(links),
                                    cur_key, cur_fp, st["recovery"]):
            return
        links.append((content["new_key"], content["from_point"], seq))
        if len(links) == 1:
            st["recovery"] = content.get("recovery_key")
        st["links"] = links
        self._rot_chains[ref] = st

    def succession_key_at(self, author: str, point, upto_seq: int | None = None):
        """(enrolled, key_hex) — is `author` enrolled on a succession chain (as
        of the log prefix `seq < upto_seq`; None = the whole log), and if so
        which public key was valid for it AT logical `point` (rotation
        `valid_key_at` semantics: the link with the greatest from_point <=
        point). Fail closed: enrolled with a non-int point, or a point before
        the genesis enrollment, yields (True, None) — enrolled but NO valid key.
        The seq prefix matters for causality: a link cannot retroactively refuse
        events that were woven (and verified) before it existed."""
        st = self._rot_chains.get(author)
        if st is None:
            return False, None
        links = [l for l in st["links"] if upto_seq is None or l[2] < upto_seq]
        if not links:
            return False, None            # not yet enrolled at this log prefix
        if not isinstance(point, int) or isinstance(point, bool):
            return True, None             # enrolled + malformed point → fail closed
        key = None
        for kh, fp, _seq in links:
            if fp <= point:
                key = kh
            else:
                break
        return True, key

    def _verify_author(self, author: str, eid: str, sig: str, point,
                       upto_seq: int | None = None) -> bool:
        """Rotation-aware event verification — the seam Cycle 54 left decorative.

        An author ENROLLED on a succession chain verifies against the key valid
        AT this event's logical point: pre-rotation events keep verifying under
        the old key, post-rotation events verify under the new key, and an event
        signed by a RETIRED key is refused (fail closed — no valid key at the
        point is a refusal, never a fallback). An author with NO chain — every
        existing principal — verifies EXACTLY as before, through the one-key
        Keyring (backward compatible)."""
        enrolled, key = self.succession_key_at(author, point, upto_seq)
        if enrolled:
            if key is None:
                return False
            from decima import rotation
            return rotation._verify_sig(key, eid.encode(), sig)
        return self.keyring.verify(author, eid, sig)

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
        # NFC-normalize the body's text on the way in, so the STORED (and folded)
        # content is canonical UTF-8/NFC on every nested field — not just its hash
        # (Weft Protocol §1). Idempotent for ASCII / already-normalized content.
        payload = {
            "parents": parents,
            "author": author_pid,
            "authorized": authorized,
            "verb": verb,
            "body": nfc_deep(body),
            "lamport": lamport,
        }
        eid = content_id(payload, kind="event")
        sig = self.keyring.sign(author_pid, eid)
        # FAIL CLOSED AT THE DOOR for a ROTATING author: an author enrolled on a
        # succession chain must have signed with the key valid AT this event's
        # logical point (its lamport) — a RETIRED key records NOTHING, so the
        # append-only log never carries an event its own fold would refuse.
        # Authors with no chain (every existing principal) skip this entirely:
        # two dict lookups, zero crypto, byte-identical behavior.
        enrolled, key = self.succession_key_at(author_pid, lamport)
        if enrolled:
            from decima import rotation
            if key is None or not rotation._verify_sig(key, eid.encode(), sig):
                raise WeftError(
                    f"author {author_pid} signed with a key that is not valid at "
                    f"point {lamport} on its succession chain (retired or "
                    f"pre-enrollment) — refused, nothing recorded (fail closed)")
        import json
        self.db.execute(
            "INSERT INTO events (id, payload, author, sig) VALUES (?,?,?,?)",
            (eid, json.dumps(payload, sort_keys=True), author_pid, sig),
        )
        self.db.commit()
        self.head = eid
        self.lamport = lamport
        seq = self._seq_of(eid)
        # A key_rotation Cell advances the succession chain the moment it lands
        # (if — and only if — its endorsement verifies); ordinary events return
        # from `_rot_apply` after one dict compare.
        self._rot_apply(seq, payload)
        ev = self._row_to_event(seq, eid, payload, author_pid, sig)
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
            # ROTATION-AWARE (the Cycle 54 promise made real): verify against
            # the key valid for this author AT this event's logical point (its
            # lamport), per the succession chain folded from links EARLIER in
            # the log (`seq` prefix — a later link never orphans woven history).
            # Old events verify under the old key, post-rotation events under
            # the new key, a retired key is refused; a chain-less author takes
            # the exact pre-existing keyring path.
            if not self._verify_author(author, eid, sig, payload["lamport"], upto_seq=seq):
                raise WeftError(f"bad signature at seq {seq}")
            yield self._row_to_event(seq, eid, payload, author, sig)

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def ingest(self, row) -> str:
        """Accept ONE foreign event from a peer feed, with full WEFT §2 ACCEPTANCE
        VALIDATION, and union it into the log. `row` is a wire record
        `(id, payload_text, author, sig)` — the shape a networked sync transport
        delivers. This is the acceptance gate that makes cross-peer sync sound: a peer
        trusts NOTHING it is handed; an event enters the append-only DAG only if it
        proves itself.

        Returns a status string:
          - "ingested"          — validated and unioned in;
          - "duplicate"         — already present (idempotent no-op);
          - "orphan"            — a parent is not present yet; the caller MAY retry after
                                  ingesting more (an out-of-order feed converges by
                                  retry). It is NOT inserted;
          - "rejected:<reason>" — terminal; the event is malformed, forged, or violates
                                  §2 and is NEVER inserted (fail closed).

        Validation (all fail closed):
          1. well-formed payload with the required fields + a known verb;
          2. `parents` is a canonically SORTED id list (WEFT §2);
          3. the wire `author` matches the payload author;
          4. the content id RECOMPUTES from the payload (integrity + canonical bytes) —
             a single edited byte changes the id;
          5. every parent is ALREADY present — no dangling causal edge, so the log stays
             a CLOSED DAG (→ "orphan" if not, so a feed can be completed then retried).
             Causal completeness is judged BEFORE authenticity because verification is
             now ROTATION-AWARE: an honestly-produced post-rotation event causally
             descends from its rotation link (the signing weft held the link when it
             appended), so once the parents are in, the chain the signature needs is
             folded — an out-of-order feed defers ("orphan") and converges by retry
             instead of terminally rejecting a valid rotated signature;
          6. the signature verifies under the key valid AT the event's point (authentic
             author; possession of the id buys nothing; chain-less authors verify
             through the keyring exactly as before);
          7. the causal clock is honest: `lamport == 1 + max(parent lamports)` (0-parent
             genesis → 1), exactly as `append` computes it — a forged lamport that would
             jump the frontier is rejected.

        Authority is NOT re-judged here: each event was authorized at its ORIGIN in its
        own causal frontier (kernel.invoke → verify_proof) and carries that proof; sync
        is pure event UNION, so it can never re-authorize a revoked grant (SYNC.md)."""
        import json
        eid, payload_text, author, sig = row
        if self.db.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone():
            return "duplicate"
        try:
            payload = json.loads(payload_text)
        except (ValueError, TypeError):
            return "rejected:malformed-payload"
        if not isinstance(payload, dict):
            return "rejected:malformed-payload"
        required = {"parents", "author", "authorized", "verb", "body", "lamport"}
        if not required.issubset(payload):
            return "rejected:missing-fields"
        if payload["verb"] not in VERBS:
            return "rejected:bad-verb"
        parents = payload["parents"]
        if not isinstance(parents, list) or parents != sorted(parents):
            return "rejected:parents-not-canonical"     # WEFT §2: parents sorted
        if payload["author"] != author:
            return "rejected:author-mismatch"
        if content_id(payload, kind="event") != eid:
            return "rejected:id-mismatch"               # integrity + canonical bytes
        # Causal completeness FIRST: every parent must already be here (closed DAG).
        # Judged before authenticity because verification is rotation-aware: an
        # honest post-rotation event causally descends from its rotation link, so
        # parents-present ⇒ (by induction over prior ingests) the full ancestor
        # closure — the link included — is in, and the chain the signature needs
        # is folded. An out-of-order feed thus defers ("orphan", retryable) rather
        # than terminally rejecting a valid rotated signature; still fail closed —
        # an orphan is NEVER inserted.
        parent_lamports = []
        for p in parents:
            prow = self.db.execute("SELECT payload FROM events WHERE id=?", (p,)).fetchone()
            if prow is None:
                return "orphan"                         # feed incomplete — retry later
            parent_lamports.append(json.loads(prow[0])["lamport"])
        # Rotation-aware authenticity: an enrolled author's signature must hold
        # under the key valid AT the event's point (a chain-less author verifies
        # through the keyring exactly as before; a malformed lamport fails the
        # chain path closed here and the honesty check below regardless).
        if not self._verify_author(author, eid, sig, payload["lamport"]):
            return "rejected:bad-signature"             # authenticity (possession)
        # Honest causal clock: lamport = 1 + max(parent lamports) — matches `append`.
        if payload["lamport"] != 1 + max(parent_lamports, default=0):
            return "rejected:bad-lamport"
        # Accept — union into the append-only log (never overwrites; only grows).
        self.db.execute(
            "INSERT INTO events (id, payload, author, sig) VALUES (?,?,?,?)",
            (eid, json.dumps(payload, sort_keys=True), author, sig))
        self.db.commit()
        seq = self._seq_of(eid)
        self._rot_apply(seq, payload)   # an ingested rotation link advances the chain too
        lam = payload["lamport"]
        head_seq = self._seq_of(self.head) if self.head else -1
        if (lam, seq) > (self.lamport, head_seq):    # keep head = max-(lamport, seq)
            self.head = eid
        self.lamport = max(self.lamport, lam)
        return "ingested"
