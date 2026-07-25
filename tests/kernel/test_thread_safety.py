"""Weft thread discipline (0.3.1 T1.3): `check_same_thread=False` + one lock.

The store may now be driven from more than one thread of one process (the API/Shell
hosts, a reactor beat, the concurrency runner's owner thread). What must hold:

  1. the connection is usable off the constructing thread at all (before this it raised
     `sqlite3.ProgrammingError` on the first cross-thread read);
  2. every MUTATION is still serialized — concurrent appends produce ONE linear chain:
     contiguous seqs, lamports exactly 1..N, each event's parents == [predecessor], no
     duplicate ids. An unlocked append (read head → sign → insert → move head) would
     interleave and fork that chain;
  3. the log stays verifiable and re-foldable: `events()` verifies every row, a fresh
     Weft over the same file warm-starts on the same head, and the fold's `state_root` is
     stable across reconstruction (determinism/replay);
  4. a read in flight is a PINNED prefix: a concurrent append can neither extend nor
     reorder it, so concurrent readers each see some prefix of the final log — never a
     torn or reordered one;
  5. `ingest` is serialized the same way: N threads racing the SAME wire row union it in
     exactly once (one "ingested", the rest "duplicate").

Every assertion is interleaving-INVARIANT (a set/prefix/count property, never "thread A
got there first"), and no test sleeps: real overlap is forced with `threading.Barrier`,
which only trips if all parties are genuinely in flight at once.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from decima.kernel.crypto import Keyring
from decima.kernel.model import assert_content
from decima.kernel.weave import Weave
from decima.kernel.weft import Event, Weft

THREADS = 8
PER_THREAD = 6


def _db() -> str:
    return os.path.join(tempfile.mkdtemp(), "weft.db")


def _fresh() -> tuple[Weft, Keyring, str]:
    kr = Keyring(seed=bytes(32))
    weft = Weft(_db(), kr)
    return weft, kr, kr.mint("thread-test").id


def _note(weft: Weft, author: str, tag: str) -> Event:
    """One ordinary ASSERT. `tag` is unique per event, so ids never collide by content."""
    return assert_content(weft, author, f"note:{tag}", "note", {"text": tag})


def _assert_single_linear_chain(events: list[Event]) -> None:
    """The whole point of the lock: one chain, no fork, no duplicated clock."""
    assert [ev.seq for ev in events] == list(range(1, len(events) + 1))
    assert [ev.lamport for ev in events] == list(range(1, len(events) + 1))
    assert len({ev.id for ev in events}) == len(events)
    assert events[0].parents == []
    for prev, cur in zip(events, events[1:], strict=False):
        assert cur.parents == [prev.id]


# ── 1. the connection is usable from another thread ──────────────────────────


def test_store_is_usable_from_a_thread_that_did_not_create_it() -> None:
    weft, _kr, author = _fresh()
    out: dict[str, object] = {}

    def worker() -> None:
        ev = _note(weft, author, "from-another-thread")
        out["id"] = ev.id
        out["count"] = weft.count()
        out["read"] = [e.id for e in weft.events()]

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert out["count"] == 1
    assert out["read"] == [out["id"]]
    # And the creating thread still sees the same store.
    assert weft.count() == 1
    assert weft.head == out["id"]


def test_reads_from_many_threads_agree_on_the_same_log() -> None:
    weft, _kr, author = _fresh()
    ids = [_note(weft, author, f"n{i}").id for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        reads = list(pool.map(lambda _: [ev.id for ev in weft.events()], range(4)))
    assert reads == [ids] * 4


# ── 2/3. concurrent appends: serialized, verifiable, replayable ──────────────


def test_concurrent_appends_produce_one_verifiable_linear_log() -> None:
    weft, kr, author = _fresh()
    barrier = threading.Barrier(THREADS)

    def worker(w: int) -> None:
        barrier.wait()  # release all writers together: real contention, no sleeps
        for i in range(PER_THREAD):
            _note(weft, author, f"w{w}-{i}")

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        list(pool.map(worker, range(THREADS)))

    total = THREADS * PER_THREAD
    assert weft.count() == total
    events = list(weft.events())  # verifies id + signature of every row
    assert len(events) == total
    _assert_single_linear_chain(events)
    assert weft.head == events[-1].id
    assert weft.lamport == total

    # Reconstruction: a fresh Weft over the same file warm-starts on the same head and
    # folds to the same root — the concurrently written log replays deterministically.
    reopened = Weft(_same_path(weft), kr)
    assert reopened.head == events[-1].id
    assert reopened.lamport == total
    assert [ev.id for ev in reopened.events()] == [ev.id for ev in events]
    assert Weave.fold(reopened).state_root() == Weave.fold(weft).state_root()


def _same_path(weft: Weft) -> str:
    """The file behind a Weft's connection (sqlite's own `database_list`)."""
    row = weft.db.execute("PRAGMA database_list").fetchone()
    return str(row[2])


def test_concurrent_appends_and_ingests_keep_the_log_closed_and_stable() -> None:
    """Appends racing an ingest of foreign rows: the union stays a closed, verifiable
    DAG, and the fold is stable across a reconstruction."""
    src, kr, author = _fresh()
    wire = [
        (ev.id, _payload_text(src, ev.id), ev.author, ev.sig)
        for ev in [_note(src, author, f"foreign-{i}") for i in range(4)]
    ]

    dst = Weft(_db(), kr)
    barrier = threading.Barrier(2)
    statuses: list[str] = []
    lock = threading.Lock()

    def ingester() -> None:
        barrier.wait()
        for row in wire:  # in order: parents-first, so each is accepted
            status = dst.ingest(row)
            with lock:
                statuses.append(status)

    def appender() -> None:
        barrier.wait()
        for i in range(PER_THREAD):
            _note(dst, author, f"local-{i}")

    threads = [threading.Thread(target=ingester), threading.Thread(target=appender)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    accepted = [s for s in statuses if s == "ingested"]
    # An ingest whose parent has not landed yet defers ("orphan"), never a hard reject.
    assert set(statuses) <= {"ingested", "orphan", "duplicate"}
    assert len(accepted) >= 1
    events = list(dst.events())  # every row verifies
    assert dst.count() == len(events) == PER_THREAD + len(accepted)
    present = {ev.id for ev in events}
    for ev in events:  # closed DAG: no dangling causal edge
        for parent in ev.parents:
            assert parent in present
    root = Weave.fold(dst).state_root()
    assert Weave.fold(Weft(_same_path(dst), kr)).state_root() == root


def _payload_text(weft: Weft, eid: str) -> str:
    row = weft.db.execute("SELECT payload FROM events WHERE id=?", (eid,)).fetchone()
    return str(row[0])


def test_racing_ingest_of_one_row_unions_it_exactly_once() -> None:
    src, kr, author = _fresh()
    ev = _note(src, author, "one-row")
    row = (ev.id, _payload_text(src, ev.id), ev.author, ev.sig)

    dst = Weft(_db(), kr)
    barrier = threading.Barrier(THREADS)

    def worker(_w: int) -> str:
        barrier.wait()
        return dst.ingest(row)

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        statuses = list(pool.map(worker, range(THREADS)))

    assert statuses.count("ingested") == 1
    assert statuses.count("duplicate") == THREADS - 1
    assert dst.count() == 1
    assert [e.id for e in dst.events()] == [ev.id]


# ── 4. a read in flight is a pinned, verified prefix ─────────────────────────


def test_a_read_in_flight_is_pinned_and_ignores_a_later_append() -> None:
    """Deterministic, single-threaded proof of the snapshot property the chunked read
    gives: the window is fixed when the read starts, so an event appended midway
    through iteration is NOT yielded by that iterator (it appears in the next read)."""
    weft, _kr, author = _fresh()
    first = [_note(weft, author, f"pre-{i}").id for i in range(3)]

    stream = weft.events()
    seen = [next(stream).id]
    later = _note(weft, author, "appended-mid-read").id  # append DURING the iteration
    seen += [ev.id for ev in stream]

    assert seen == first  # pinned prefix: the later event is not in this read
    assert later not in seen
    assert [ev.id for ev in weft.events()] == first + [later]  # the next read sees it


def test_concurrent_readers_each_see_a_prefix_of_the_final_log() -> None:
    weft, _kr, author = _fresh()
    _note(weft, author, "seed")
    readers = 4
    barrier = threading.Barrier(readers + 1)

    def reader(_i: int) -> list[str]:
        barrier.wait()
        return [ev.id for ev in weft.events()]  # raises WeftError on any bad row

    def writer() -> None:
        barrier.wait()
        for i in range(PER_THREAD):
            _note(weft, author, f"during-read-{i}")

    with ThreadPoolExecutor(max_workers=readers + 1) as pool:
        futures = [pool.submit(reader, i) for i in range(readers)]
        writer_future = pool.submit(writer)
        writer_future.result()
        reads = [f.result() for f in futures]

    final = [ev.id for ev in weft.events()]
    assert len(final) == 1 + PER_THREAD
    for read in reads:
        assert read, "a reader saw an empty log although one event predates every writer"
        assert read == final[: len(read)], "a read was torn or reordered by a writer"


# ── 5. read windows, re-entrancy, and the exact defect this closes ───────────


def test_windowed_reads_are_unchanged_by_the_chunked_scan() -> None:
    """`upto_seq`/`from_seq` windows still mean exactly what they meant."""
    weft, _kr, author = _fresh()
    ids = [_note(weft, author, f"n{i}").id for i in range(5)]
    assert [ev.id for ev in weft.events()] == ids
    assert [ev.id for ev in weft.events(upto_seq=3)] == ids[:3]
    assert [ev.id for ev in weft.events(from_seq=2)] == ids[2:]
    assert [ev.id for ev in weft.events(upto_seq=4, from_seq=1)] == ids[1:4]
    assert list(weft.events(upto_seq=0)) == []
    assert list(weft.events(from_seq=99)) == []
    assert list(Weft(_db(), Keyring(seed=bytes(32))).events()) == []  # empty log


def test_lock_is_reentrant_so_callers_can_group_operations() -> None:
    """The documented multi-call atomic section: `with weft.lock:` around several store
    calls must not self-deadlock (the lock is re-entrant, and `append` takes it too)."""
    weft, _kr, author = _fresh()
    with weft.lock:
        before = weft.count()
        ev = _note(weft, author, "grouped")
        assert weft.count() == before + 1
        assert [e.id for e in weft.events(from_seq=before)] == [ev.id]


def test_connection_is_not_thread_bound_but_a_plain_one_still_is() -> None:
    """Documents the exact defect this closes: a default `sqlite3.connect` refuses a
    cross-thread call, which is why the API/Shell hosts had to serve single-threaded."""
    path = _db()
    Weft(path, Keyring(seed=bytes(32)))  # stamps the store
    plain = sqlite3.connect(path)
    err: list[Exception] = []

    def touch() -> None:
        try:
            plain.execute("SELECT COUNT(*) FROM events").fetchone()
        except Exception as exc:  # recorded here, asserted on by type below
            err.append(exc)

    thread = threading.Thread(target=touch)
    thread.start()
    thread.join()
    assert err and isinstance(err[0], sqlite3.ProgrammingError)

    # The Weft's own connection, opened check_same_thread=False, does not raise.
    weft = Weft(path, Keyring(seed=bytes(32)))
    out: list[int] = []
    t2 = threading.Thread(target=lambda: out.append(weft.count()))
    t2.start()
    t2.join()
    assert out == [0]
