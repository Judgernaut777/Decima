"""The Weft — the append-only, signed, content-addressed log.

Law 1: nothing happens off the Log. Every state change in Decima is one Event
appended here. There is no UPDATE and no DELETE — only INSERT. The four verbs
are the entire instruction set.

Storage is SQLite ("fine to start"), but the table is treated as append-only;
`seq` gives a total order for folding and time-travel.

PAYLOAD SEALING (FOLD §10.3): a redactable ASSERT may store its `content` as an
ENCRYPTED envelope (`sealing.seal`) whose data key lives OUTSIDE the log in a
`PayloadVault`. The event id and the signature cover the CIPHERTEXT, so destroying the
key (`erase_redacted`, driven by a REDACT on the log) makes the payload unrecoverable
while `content_id(payload) == eid` and the Ed25519 signature still verify — verify-on-read
passes for a redacted event exactly as before. The read path opens sealed content
transparently; a payload whose key is gone folds as a content-free skeleton.

Verification is ROTATION-AWARE (Cycle 54's succession chain, made live): an
author enrolled on a key_rotation chain is verified against the key valid AT
each event's logical point — old events under the old key, post-rotation events
under the new key, a retired key refused — so an identity survives its keys and
its whole history keeps verifying. An author that never rotates (every existing
principal) verifies exactly as before, through the one-key Keyring.

Threading (0.3.1 T1.3): a Weft is safe to share between THREADS of one process —
the connection is opened `check_same_thread=False` and every path that touches it
holds the store's re-entrant `lock`, so MUTATION STAYS SERIALIZED (see `Weft`'s
docstring). Nothing about durability, canonical bytes or the fold changes: the log
one thread-mixed run writes is the log a single-threaded run would have written.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from decima.kernel import sealing
from decima.kernel.hashing import content_id, nfc_deep

if TYPE_CHECKING:
    from decima.kernel.crypto import Keyring

# The entire instruction set. belief | action | trust.
# An ASSERT body may carry an optional `kind` (CONTENT | EDGE | TYPE_DEF),
# mapping to WEFT Protocol §4 `assertion` (1 CONTENT, 2 EDGE, 8 TYPE_DEF). The
# verb set stays four; the body shape is opaque to `append` and read by the fold.
ASSERT = "ASSERT"  # bring a fact/version of a Cell into being
RETRACT = "RETRACT"  # withdraw a prior assertion. body `mode` (WEFT §5):
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
INVOKE = "INVOKE"  # request an effect in the world through a capability
ATTEST = "ATTEST"  # witness/sign another event or cell (verification, trust, promotion)
VERBS = (ASSERT, RETRACT, INVOKE, ATTEST)


@dataclass
class Event:
    seq: int | None
    id: str
    parents: list[str]
    author: str  # principal id
    authorized: str | None  # capability cell id that permitted this (provenance of power)
    verb: str
    body: dict[str, Any]
    lamport: int
    sig: str

    def hashed_payload(self) -> dict[str, Any]:
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


# The on-disk store-format version, distinct from the package version. Bumped when the
# content-address scheme changes (durable Weft v0.1: BLAKE3-256 ids, deterministic CBOR,
# base32 kind-prefixed ids). A store stamped with a different value is rejected on open —
# clean-break, no in-place upgrade (see docs/design/adopt-durable-protocol.md §7 D2).
WEFT_STORE_VERSION = "decima-weft/0.1"

# How many rows one step of a verified read fetches while holding the store lock. The
# read is a KEYSET scan over the append-only `seq`, so the chunk size changes NOTHING
# about which events are yielded or in what order (determinism): it only bounds how long
# the lock is held per step and how much of the log is resident at once.
_READ_CHUNK = 512

# How long a statement waits for another CONNECTION's write lock before raising
# `sqlite3.OperationalError: database is locked`. Same-process access is already
# serialized by `Weft.lock`; this covers the other short-lived readers that open the
# same file (`services/backup` `_raw_rows`, `services/diagnostics` `_raw_row_integrity`,
# a second Weft over the same path), which previously failed instantly on contention.
# It changes no durability setting and nothing that is hashed or signed.
_BUSY_TIMEOUT_MS = 5000

# Types whose content the KERNEL ITSELF must read to stay sound, so they may never be
# sealed (fail closed at the door rather than write a log the kernel cannot interpret):
# the succession chain folds `key_rotation` content here in the weft, and authorization /
# the type registry fold `capability`, `type` and `promoter` content in the weave. Sealing
# is for USER payloads — the bytes a right-to-be-forgotten request is about.
UNSEALABLE_TYPES: frozenset[str] = frozenset({"key_rotation", "capability", "type", "promoter"})


class Weft:
    """THREAD DISCIPLINE (0.3.1 T1.3).

    The store is safe to use from MORE THAN ONE THREAD of one process, without
    weakening durability or determinism:

      * the SQLite connection is opened `check_same_thread=False`, so it is no longer
        bound to the thread that constructed it (the API/Shell hosts, a reactor beat and
        the `concurrency` runner's owner thread are all separate threads);
      * EVERY path that touches the connection or the in-memory head/lamport/rotation
        state takes `self.lock` — one re-entrant lock per store. Mutation therefore stays
        fully SERIALIZED: `append` reads `head`, derives `parents`/`lamport`, signs,
        INSERTs and moves `head` as ONE critical section, so concurrent appends can never
        interleave into a forked chain or a duplicated lamport, and the log a fold reads
        is byte-identical to the one a single-threaded run would have written;
      * reads are chunked KEYSET scans (see `events`) that hold the lock only per chunk —
        never while verifying signatures and never while yielding to the caller — so a
        slow consumer cannot stall a writer and a consumer that appends mid-iteration
        cannot deadlock. Because the log is APPEND-ONLY, a read pinned to the max `seq`
        at its first step is a stable prefix: no transaction is needed for a read to be
        consistent.

    `lock` is PUBLIC and re-entrant on purpose: a caller that needs several store
    operations to be atomic with respect to other threads (e.g. `count()` then `append`
    then `events(from_seq=...)`) wraps them in `with weft.lock:`. Hold it only around
    store work — never around an effect, a model call or any I/O of unbounded latency.
    """

    def __init__(
        self, db_path: str, keyring: Keyring, vault: sealing.PayloadVault | None = None
    ) -> None:
        self.keyring = keyring
        # Custody of PAYLOAD data keys — deliberately OUTSIDE the append-only log, because
        # erasure means destroying something and the log has no DELETE (FOLD §10.3).
        # None = this store cannot seal (`append(seal=True)` fails closed), and a sealed
        # event already on the log reads as a content-free skeleton.
        self.vault = vault
        # Re-entrant so the internal composition is trivially safe: `append` holds the
        # lock and calls `_seq_of`/`succession_key_at`/`_rot_apply`, which take it too.
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        # Journal mode is deliberately LEFT AT THE DEFAULT (rollback journal). WAL would
        # buy nothing here — same-process access funnels through the one guarded
        # connection — and it would put `weft.db-wal`/`-shm` sidecars beside the store
        # that the restore path (`services/backup/service.py`, which removes exactly
        # `weft.db`) does not know about. `synchronous` is untouched, so commit durability
        # is exactly what it was.
        self.db.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        # The schema/stamp path is taken under the lock as well. It cannot actually race
        # another THREAD (no other thread can hold this object before __init__ returns),
        # but two Wefts over the SAME FILE are two connections, and the DDL is
        # `IF NOT EXISTS` + `busy_timeout` so that case degrades to a wait, not an error.
        with self.lock:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS events (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   id TEXT UNIQUE NOT NULL,
                   payload TEXT NOT NULL,
                   author TEXT NOT NULL,
                   sig TEXT NOT NULL
               )"""
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.db.commit()
        self._check_store_version()
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
        self._rot_chains: dict[str, dict[str, Any]] = {}
        self.head, self.lamport = self._load_head()
        self._rot_scan()

    def _check_store_version(self) -> None:
        """Clean-break protocol guard. The durable Weft v0.1 changed the content-address
        scheme (BLAKE3-256 ids, deterministic CBOR, base32 kind-prefixed ids), so a store
        written by the old stdlib profile is NOT upgraded in place — its ids and state_root
        would simply fail to verify. Stamp fresh stores with the current version, verify a
        stamped store matches, and reject an unstamped store that already holds events (an
        old-profile store) with a clear message instead of a cryptic id-mismatch fold error.
        The stamp lives in a side table and never enters any signed/hashed content.
        Held under `self.lock`, so the read-then-stamp is one critical section."""
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key = 'store_version'").fetchone()
            if row is not None:
                if row[0] != WEFT_STORE_VERSION:
                    raise WeftError(
                        f"Weft store is {row[0]!r} but this build speaks {WEFT_STORE_VERSION!r}; "
                        "the durable protocol changed the content-address scheme "
                        "(BLAKE3-256 / CBOR / base32) — stores are not upgraded in place. "
                        "Export from the matching build and re-import (clean break)."
                    )
                return
            has_events = self.db.execute("SELECT 1 FROM events LIMIT 1").fetchone() is not None
            if has_events:
                raise WeftError(
                    "Weft store predates protocol versioning and its content-address scheme is "
                    f"incompatible with {WEFT_STORE_VERSION!r} (BLAKE3-256 / CBOR / base32). "
                    "Export from the old build and re-import (clean break)."
                )
            self.db.execute(
                "INSERT INTO meta (key, value) VALUES ('store_version', ?)", (WEFT_STORE_VERSION,)
            )
            self.db.commit()

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

    def _rot_scan(self) -> None:
        """Warm start: re-fold the succession chains from an existing log. The
        LIKE prefilter is a cheap SUPERSET screen (a key_rotation payload always
        contains the literal type string); `_rot_apply` does the real check.

        The rows are FETCHED under the store lock and folded after it is released: no
        cursor is held across the lock, so a concurrent append can neither be blocked by
        this scan nor appear halfway through it."""
        import json

        with self.lock:
            rows = self.db.execute(
                "SELECT seq, payload FROM events WHERE payload LIKE ? ORDER BY seq ASC",
                ('%"key_rotation"%',),
            ).fetchall()
        for seq, payload_text in rows:
            try:
                payload = json.loads(payload_text)
            except (ValueError, TypeError):
                continue
            self._rot_apply(seq, payload)

    def _rot_apply(self, seq: int, payload: dict[str, Any]) -> None:
        """Fold ONE stored event into the succession chains iff it is an ASSERT
        carrying a key_rotation Cell whose link VERIFIES as the next link of its
        principal's chain (rotation._valid_link — the same fail-closed
        endorsement check the weave-level fold uses). Anything else — ordinary
        events, forged/unendorsed links, replays — is inert here: the chain only
        ever advances on a verified endorsement (fail closed).

        The check-then-advance on `_rot_chains` is one critical section under the store
        lock (re-entrant: `append`/`ingest` already hold it), so two threads landing
        rotation links can never both believe they are extending the same chain link."""
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
        from decima.kernel import rotation

        with self.lock:
            st = self._rot_chains.get(ref, {"links": [], "recovery": None})
            links = st["links"]
            cur_key, cur_fp = (links[-1][0], links[-1][1]) if links else (None, None)
            if not rotation._valid_link(content, ref, len(links), cur_key, cur_fp, st["recovery"]):
                return
            links.append((content["new_key"], content["from_point"], seq))
            if len(links) == 1:
                st["recovery"] = content.get("recovery_key")
            st["links"] = links
            self._rot_chains[ref] = st

    def succession_key_at(
        self, author: str, point: object, upto_seq: int | None = None
    ) -> tuple[bool, str | None]:
        """(enrolled, key_hex) — is `author` enrolled on a succession chain (as
        of the log prefix `seq < upto_seq`; None = the whole log), and if so
        which public key was valid for it AT logical `point` (rotation
        `valid_key_at` semantics: the link with the greatest from_point <=
        point). Fail closed: enrolled with a non-int point, or a point before
        the genesis enrollment, yields (True, None) — enrolled but NO valid key.
        The seq prefix matters for causality: a link cannot retroactively refuse
        events that were woven (and verified) before it existed.

        The chain is SNAPSHOTTED under the store lock (a plain list-comprehension over a
        list another thread may be appending to is not a consistent read), and the key is
        then chosen from that immutable snapshot off the lock."""
        with self.lock:
            st = self._rot_chains.get(author)
            if st is None:
                return False, None
            links = [link for link in st["links"] if upto_seq is None or link[2] < upto_seq]
        if not links:
            return False, None  # not yet enrolled at this log prefix
        if not isinstance(point, int) or isinstance(point, bool):
            return True, None  # enrolled + malformed point → fail closed
        key = None
        for kh, fp, _seq in links:
            if fp <= point:
                key = kh
            else:
                break
        return True, key

    def _verify_author(
        self, author: str, eid: str, sig: str, point: object, upto_seq: int | None = None
    ) -> bool:
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
            from decima.kernel import rotation

            return rotation._verify_sig(key, eid.encode(), sig)
        return self.keyring.verify(author, eid, sig)

    def verify_author_sig(self, author: str, message: str, sig: str, point: object) -> bool:
        """Rotation-aware verification of ANY statement signed by `author` at logical
        `point` — the exact key selection `events()` applies to an event signature (the
        succession chain if the author is enrolled, else the one-key Keyring; no valid
        key at the point is a refusal, never a fallback).

        The acceptance gate uses it for an AuthorizationProof's `holder_sig` (WEFT §3
        field 6), which the origin signed with the very key that signed the event — so a
        rotated author's proof verifies under the key valid at its point instead of being
        refused. Read-only; it confers no authority (Law 2)."""
        return self._verify_author(author, message, sig, point)

    def _load_head(self) -> tuple[str | None, int]:
        with self.lock:
            row = self.db.execute(
                "SELECT id, payload FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None, 0
        import json

        return row[0], json.loads(row[1])["lamport"]

    def append(
        self,
        author_pid: str,
        verb: str,
        body: dict[str, Any],
        authorized: str | None = None,
        parents: list[str] | None = None,
        seal: bool = False,
        seal_key: bytes | None = None,
    ) -> Event:
        if verb not in VERBS:
            raise WeftError(f"unknown verb {verb!r}")
        import json

        # ONE CRITICAL SECTION, and the reason `Weft.lock` exists: this reads `head`/
        # `lamport`, derives the parents and the causal clock from them, content-addresses
        # and SIGNS the payload, INSERTs it, and only then moves `head`/`lamport` and the
        # succession chain. Two threads interleaving inside that window would fork the
        # chain (both descending from the same head), duplicate a lamport, or collide on
        # the id — so the whole sequence is serialized. Nothing of unbounded latency is
        # inside: the signature is a local Ed25519 over 32-odd bytes.
        with self.lock:
            # `parents=None` is the linear default: descend from the current head.
            # Passing an explicit parent set appends a CONCURRENT event — a fork — used
            # by the merge layer (the only place a non-linear frontier is created).
            # Lamport follows WEFT §2: 1 + max(parent.lamport), 0-base for genesis; on
            # the linear path this is exactly the old `self.lamport + 1`.
            if parents is None:
                parents = [self.head] if self.head else []
                parent_lamports = [self.lamport] if self.head else []
            else:
                parents = sorted(parents)  # canonical frontier (WEFT §2: parents sorted)
                parent_lamports = [self._lamport_of(p) for p in parents]
            lamport = 1 + max(parent_lamports, default=0)
            # NFC-normalize the body's text on the way in, so the STORED (and folded)
            # content is canonical UTF-8/NFC on every nested field — not just its hash
            # (Weft Protocol §1). Idempotent for ASCII / already-normalized content.
            # SEALED payloads (FOLD §10.3): the plaintext is encrypted BEFORE the id is
            # computed, so the id and the signature cover the CIPHERTEXT and survive the
            # later destruction of the data key. The plaintext never enters the payload.
            canon_body = nfc_deep(body)
            if seal:
                canon_body = self._seal_body(cast("dict[str, Any]", canon_body), seal_key)
            payload = {
                "parents": parents,
                "author": author_pid,
                "authorized": authorized,
                "verb": verb,
                "body": canon_body,
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
                from decima.kernel import rotation

                if key is None or not rotation._verify_sig(key, eid.encode(), sig):
                    raise WeftError(
                        f"author {author_pid} signed with a key that is not valid at "
                        f"point {lamport} on its succession chain (retired or "
                        f"pre-enrollment) — refused, nothing recorded (fail closed)"
                    )
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

    # ── sealed payloads + cryptographic erasure (FOLD §10.3) ──────────────────
    def _seal_body(self, body: dict[str, Any], key: bytes | None) -> dict[str, Any]:
        """Replace a CONTENT body's plaintext `content` with a sealed envelope and hand the
        data key to the vault. Fails closed: no vault, a non-CONTENT body, non-dict
        content, or a kernel-critical type (`UNSEALABLE_TYPES`) refuses the append rather
        than write a log the kernel cannot interpret."""
        if self.vault is None:
            raise WeftError(
                "sealing requires a payload vault — construct "
                "Weft(db, keyring, vault=sealing.DirectoryPayloadVault(...)); "
                "a key that cannot be destroyed is not erasure"
            )
        if body.get("kind", "CONTENT") != "CONTENT":
            raise WeftError("only a CONTENT assertion body can be sealed")
        if body.get("type") in UNSEALABLE_TYPES:
            raise WeftError(
                f"type {body.get('type')!r} may never be sealed: the kernel folds its own "
                f"content for these types — {sorted(UNSEALABLE_TYPES)}"
            )
        content = body.get("content")
        if not isinstance(content, dict):
            raise WeftError("sealed content must be a dict")
        env, data_key = sealing.seal(content, key)
        ref = sealing.key_ref_of(env)
        if ref is None:  # unreachable via sealing.seal — belt-and-braces fail closed
            raise WeftError("sealed envelope carries no key ref")
        self.vault.put(ref, data_key)
        return {**body, "content": env}

    def _open_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a stored payload's sealed content FOR THE FOLD, after its id and signature
        have been verified against the stored (ciphertext) bytes.

        Three outcomes, all deterministic:
          * not sealed            → returned unchanged (every pre-existing event takes
                                    this path — zero behavior change);
          * sealed, key held      → `content` becomes the decrypted plaintext, so the fold
                                    sees exactly what it saw before sealing;
          * sealed, key DESTROYED → `content` becomes `{}` and the body is marked
                                    `erased: 1`; the cell folds as a content-free
                                    skeleton. This is what makes REDACT irreversible: not
                                    even a full replay can resurrect the payload.
        A sealed envelope that fails AUTHENTICATION under the held key means a tampered
        vault/store — raise, never fall back to ciphertext-as-content (fail closed)."""
        body = payload.get("body")
        if not isinstance(body, dict) or not sealing.is_sealed(body.get("content")):
            return payload
        env = body["content"]
        ref = sealing.key_ref_of(env)
        key = self.vault.get(ref) if (self.vault is not None and ref is not None) else None
        opened: dict[str, Any]
        if key is None:
            opened = {}
        else:
            try:
                opened = sealing.unseal(env, key)
            except sealing.SealError as exc:
                raise WeftError(f"sealed payload failed to open: {exc}") from exc
        new_body: dict[str, Any] = {**body, "content": opened}
        if key is None:
            new_body["erased"] = 1  # int, never a float/bool — skeleton marker for readers
        return {**payload, "body": new_body}

    def sealed_key_refs(self, cell_id: str | None = None) -> list[str]:
        """Every vault handle held by a sealed ASSERT on this log (optionally just one
        cell's), in log order, deduplicated. Pure read; the LIKE is a cheap superset
        screen (as in `_rot_scan`) and `key_ref_of` does the real check."""
        import json

        refs: list[str] = []
        for (payload_text,) in self.db.execute(
            "SELECT payload FROM events WHERE payload LIKE ? ORDER BY seq ASC",
            (f"%{sealing.SEALED}%",),
        ):
            try:
                payload = json.loads(payload_text)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or payload.get("verb") != ASSERT:
                continue
            body = payload.get("body")
            if not isinstance(body, dict):
                continue
            if cell_id is not None and body.get("cell") != cell_id:
                continue
            ref = sealing.key_ref_of(body.get("content"))
            if ref is not None and ref not in refs:
                refs.append(ref)
        return refs

    def redacted_cells(self) -> list[str]:
        """Cells covered by an effective REDACT — GC-eligibility condition #1 of FOLD
        §10.3 (\"payload is covered by an effective REDACT\"), read from the log itself (a
        RETRACT whose `mode` is REDACT). Authority for that RETRACT was judged when it was
        appended, exactly as for the projection-level erasure."""
        import json

        cells: list[str] = []
        for (payload_text,) in self.db.execute(
            "SELECT payload FROM events WHERE payload LIKE ? ORDER BY seq ASC", ('%"REDACT"%',)
        ):
            try:
                payload = json.loads(payload_text)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or payload.get("verb") != RETRACT:
                continue
            body = payload.get("body")
            if not isinstance(body, dict) or body.get("mode") != "REDACT":
                continue
            cell = body.get("cell")
            if isinstance(cell, str) and cell not in cells:
                cells.append(cell)
        return cells

    def erase_redacted(self, cell_id: str | None = None) -> list[str]:
        """CRYPTOGRAPHIC ERASURE — the GC act FOLD §10.3 specifies and this profile
        deferred. Destroy the data key of every sealed payload covered by an effective
        REDACT (all of them, or just one cell's) and return the handles actually
        destroyed, sorted (deterministic).

        Deliberately a SEPARATE, explicit sweep rather than a fold side effect: the log
        records the DECISION (the RETRACT REDACT), the sweep enacts it on bytes. NOTHING on
        the log is mutated — no id, no signature, no stored payload byte — so the log
        verifies event-for-event afterwards. Idempotent: a second sweep destroys nothing
        and returns []. A cell with no sealed payload (every pre-sealing event) yields [],
        leaving projection-level REDACT exactly as it was. Fails closed on a cell no
        REDACT covers — erasure is never freelance."""
        covered = self.redacted_cells()
        if cell_id is not None and cell_id not in covered:
            raise WeftError(
                f"refusing to erase {cell_id!r}: no effective REDACT on the log covers it "
                "(FOLD §10.3 eligibility #1) — append the RETRACT first"
            )
        if self.vault is None:
            return []
        destroyed: list[str] = []
        for cell in covered if cell_id is None else [cell_id]:
            for ref in self.sealed_key_refs(cell):
                if self.vault.destroy(ref):
                    destroyed.append(ref)
        return sorted(destroyed)

    def _seq_of(self, eid: str) -> int:
        with self.lock:
            row = self.db.execute("SELECT seq FROM events WHERE id=?", (eid,)).fetchone()
        return int(row[0])

    def _lamport_of(self, eid: str) -> int:
        """The lamport of a stored event (for computing a fork's lamport from an
        explicit parent set). Linear appends never need this — they reuse the
        in-memory head lamport."""
        import json

        with self.lock:
            row = self.db.execute("SELECT payload FROM events WHERE id=?", (eid,)).fetchone()
        return json.loads(row[0])["lamport"] if row else 0

    @staticmethod
    def _row_to_event(seq: int, eid: str, payload: dict[str, Any], author: str, sig: str) -> Event:
        return Event(
            seq=seq,
            id=eid,
            parents=payload["parents"],
            author=author,
            authorized=payload["authorized"],
            verb=payload["verb"],
            body=payload["body"],
            lamport=payload["lamport"],
            sig=sig,
        )

    def events(self, upto_seq: int | None = None, from_seq: int | None = None) -> Iterator[Event]:
        """Yield events in causal (seq) order, VERIFYING each as we read it.

        This is where Laws 1 & 4 are enforced on read: recompute the content id
        and check the author's signature. Tampering with the log is detected.

        `from_seq` windows the read to events with `seq > from_seq` — the tail above
        a snapshot frontier — so an incremental fold reads/verifies only the new
        events, not the whole log (IFB1). `from_seq=None` reads from genesis.

        THREAD DISCIPLINE (T1.3): the scan is CHUNKED and KEYSET-paged on `seq`, and the
        store lock is taken per chunk only — never while verifying (BLAKE3 + Ed25519) and
        never while yielding to the caller. Three consequences:

          * the window is PINNED to the log's max `seq` (bounded further by `upto_seq`)
            at the first step, so a concurrent append can never extend, shorten or
            reorder a read already in flight. Since the log is APPEND-ONLY, every row at
            or below that bound is immutable, so this is a consistent snapshot read
            WITHOUT a transaction — a fold in one thread sees exactly the prefix a
            single-threaded run would have folded (DETERMINISM: same prefix, same
            state_root);
          * a writer is never blocked behind a slow reader (no cursor is held across the
            lock), and a consumer that appends while iterating cannot deadlock;
          * row-for-row identical to the single-statement read it replaces: `seq` is a
            monotone append-only key, so paging by it yields the same events in the same
            order, and each row is still verified before it is yielded."""
        import json

        with self.lock:
            bounds = self.db.execute("SELECT MIN(seq), MAX(seq) FROM events").fetchone()
        if bounds is None or bounds[0] is None:
            return  # empty log
        lo, hi = int(bounds[0]), int(bounds[1])
        if from_seq is not None:
            lo = max(lo, from_seq + 1)
        if upto_seq is not None:
            hi = min(hi, upto_seq)
        cursor = lo - 1
        while cursor < hi:
            with self.lock:
                rows = self.db.execute(
                    "SELECT seq, id, payload, author, sig FROM events "
                    "WHERE seq > ? AND seq <= ? ORDER BY seq ASC LIMIT ?",
                    (cursor, hi, _READ_CHUNK),
                ).fetchall()
            if not rows:
                return
            for seq, eid, payload_text, author, sig in rows:
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
                # ONLY NOW — after the id + signature checks have run against the STORED
                # (ciphertext) bytes — open any sealed content for the fold. Verification
                # is therefore completely independent of whether the data key still
                # exists: a REDACTed, key-destroyed event verifies exactly like any other
                # and folds as a content-free skeleton (FOLD §10.3).
                payload = self._open_payload(payload)
                yield self._row_to_event(seq, eid, payload, author, sig)
            cursor = int(rows[-1][0])

    def count(self) -> int:
        with self.lock:
            row = self.db.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def ingest(self, row: tuple[str, str, str, str]) -> str:
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
                                  §2 and is NEVER inserted (fail closed). The authority
                                  re-check (8) adds three: "missing-authorization-proof",
                                  "proof-holder-mismatch", "unauthorized-invoke".

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
             jump the frontier is rejected;
          8. PER-INVOCATION AUTHORITY (WEFT §2 item 7): an INVOKE that CLAIMS a
             capability must carry an AuthorizationProof (§3) that still verifies against
             the local view AT THIS EVENT'S CAUSAL FRONTIER — the fold of exactly its
             ancestor closure (`Weave.fold_frontier`). A peer's signature proves WHO
             acted, never that it MAY: a forged INVOKE naming a grant it never held, or
             one revoked before it acted, is refused here (decima/kernel/acceptance.py).

        Authority is judged AT THE FRONTIER, never against mutable "current" state (§2
        item 7): each event was authorized at its ORIGIN in its own causal frontier
        (kernel.invoke → verify_proof) and carries that proof, and (8) re-verifies that
        proof against the ancestor closure the origin acted on. Sync therefore stays pure
        event UNION — it can never re-authorize a revoked grant (SYNC.md), and it can
        never retroactively REFUSE a legitimately-authorized event just because the local
        current state moved on (a grant revoked later, a lease since exhausted, or a
        single-use approval the origin consumed right after acting — that consuming
        RETRACT is a DESCENDANT of the INVOKE, so it is not in its frontier). Because an
        ancestor closure is a property of the DAG and not of delivery order, acceptance is
        deterministic: the same event set converges to the same accepted set and the same
        state_root however a feed ordered it."""
        # ONE CRITICAL SECTION (T1.3). The §2 acceptance gate is a chain of
        # check-then-acts (duplicate? parents present? lamport correct? authority still
        # valid?) ending in an INSERT plus a head/rotation update. Two threads ingesting
        # concurrently must not both pass the checks and both insert, so the whole gate
        # is serialized. The lock is re-entrant, so the helpers it calls may take it too.
        # Every validation, status string and ordering below is UNCHANGED.
        with self.lock:
            return self._ingest_validated(row)

    def _ingest_validated(self, row: tuple[str, str, str, str]) -> str:
        """The §2 acceptance gate itself. Callers MUST hold `self.lock` (see `ingest`)."""
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
            return "rejected:parents-not-canonical"  # WEFT §2: parents sorted
        if payload["author"] != author:
            return "rejected:author-mismatch"
        if content_id(payload, kind="event") != eid:
            return "rejected:id-mismatch"  # integrity + canonical bytes
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
                return "orphan"  # feed incomplete — retry later
            parent_lamports.append(json.loads(prow[0])["lamport"])
        # Rotation-aware authenticity: an enrolled author's signature must hold
        # under the key valid AT the event's point (a chain-less author verifies
        # through the keyring exactly as before; a malformed lamport fails the
        # chain path closed here and the honesty check below regardless).
        if not self._verify_author(author, eid, sig, payload["lamport"]):
            return "rejected:bad-signature"  # authenticity (possession)
        # Honest causal clock: lamport = 1 + max(parent lamports) — matches `append`.
        if payload["lamport"] != 1 + max(parent_lamports, default=0):
            return "rejected:bad-lamport"
        # PER-INVOCATION AUTHORITY RE-CHECK (WEFT §2 item 7) — the last gate before the
        # log grows. A signature proves WHO acted, never that it MAY: an INVOKE that
        # claims a capability must carry a §3 proof that still verifies at THIS event's
        # causal frontier (its ancestor closure), so a foreign INVOKE naming a grant its
        # author never held — or one revoked before it acted — is refused, while a
        # legitimately-authorized one still ingests (never re-judged against mutable
        # "current" state). Lazy import: acceptance composes over the weave, which is
        # layered ABOVE the weft (the same shape as `_rot_apply`'s rotation import).
        # Any non-INVOKE event, and any INVOKE that claims no capability, returns from
        # the predicate after a couple of dict lookups — no fold, no crypto.
        if payload["verb"] == INVOKE:
            from decima.kernel import acceptance

            ok, code = acceptance.recheck_invoke_authority(self, payload)
            if not ok:
                return f"rejected:{code}"  # terminal; nothing inserted (fail closed)
        # Accept — union into the append-only log (never overwrites; only grows).
        self.db.execute(
            "INSERT INTO events (id, payload, author, sig) VALUES (?,?,?,?)",
            (eid, json.dumps(payload, sort_keys=True), author, sig),
        )
        self.db.commit()
        seq = self._seq_of(eid)
        self._rot_apply(seq, payload)  # an ingested rotation link advances the chain too
        lam = payload["lamport"]
        head_seq = self._seq_of(self.head) if self.head else -1
        if (lam, seq) > (self.lamport, head_seq):  # keep head = max-(lamport, seq)
            self.head = eid
        self.lamport = max(self.lamport, lam)
        return "ingested"
