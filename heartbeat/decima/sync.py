"""Sync transport — reconcile two real Weft instances by DAG union (SY2, offline).

specs/SYNC.md: peers converge by **union of immutable, signed events** — no peer
overwrites another's history, conflicts surface through the merge reducers (M1/M2),
and authorization is judged at an event's causal frontier, so sync can never
re-authorize a revoked grant. SY1 simulated peers as forks inside one Weft; this
module does the real thing between **two `Weft` objects**.

The protocol, one round, offline and in-process:

  1. **difference** — find the events the target is missing (the causal difference;
     `frontier()` is the bandwidth-optimized handshake a network would use, but the
     reference computes the difference from full have-sets for exactness);
  2. **transfer** — ship those events as raw wire records `(id, payload, author, sig)`;
  3. **verify** — on ingest, recompute the content id and check the signature under
     the **shared keyring** — exactly the checks `Weft.events()` runs on read; a
     tampered or unsignable event is REJECTED, never inserted;
  4. **union** — insert the verified foreign rows (the append-only log only grows);
  5. **converge** — both Wefts now fold to one identical `state_root`.

Trust model: both peers share the keyring (the HMAC profile's symmetric stand-in
for ed25519); under real ed25519 the verifier needs only public keys. Either way a
peer accepts a foreign event **only** if it verifies — possession of the id buys
nothing, and a forged/edited event cannot enter the union.

Acceptance is now the core `Weft.ingest()` — full WEFT §2 validation (integrity +
signature + parents-present + honest lamport), so a foreign event enters the DAG only
if it proves itself, and an out-of-order feed still unions a closed DAG (orphans are
deferred + retried). `sync_over_wire` adds the network-shaped path: peers exchange
have-sets and SERIALIZED feeds (a JSON string is the wire) rather than reading each
other's `.db`. Authorization is judged per-event at ORIGIN, so the union never
re-authorizes a revoked grant — sync is pure event union over signed, §2-valid events.
"""
import json
import socket
import struct
import threading

from decima.hashing import content_id


# ── difference / frontier ────────────────────────────────────────────────────
def event_ids(weft) -> set:
    """Every event id this Weft holds (its 'have' set)."""
    return {r[0] for r in weft.db.execute("SELECT id FROM events")}


def frontier(weft) -> set:
    """The DAG heads: events that are no other event's parent. A real transport
    exchanges these and walks ancestors to discover the difference; the reference
    diffs full have-sets (below), but the frontier is the protocol-faithful handle."""
    ids, parents = set(), set()
    for eid, payload in weft.db.execute("SELECT id, payload FROM events"):
        ids.add(eid)
        parents.update(json.loads(payload).get("parents", []))
    return ids - parents


def _rows(weft):
    """Raw wire records (id, payload, author, sig) in seq order — the offline
    stand-in for a network feed of a peer's events."""
    return weft.db.execute(
        "SELECT id, payload, author, sig FROM events ORDER BY seq").fetchall()


def missing_for(source, target) -> list:
    """The rows `source` holds that `target` lacks — the causal difference —
    topologically ordered. A parent's lamport is always strictly smaller than its
    child's (WEFT §2), so `(lamport, id)` order guarantees parents insert first."""
    have = event_ids(target)
    rows = [r for r in _rows(source) if r[0] not in have]
    rows.sort(key=lambda r: (json.loads(r[1])["lamport"], r[0]))
    return rows


# ── verify + ingest (the union step) ─────────────────────────────────────────
def verify_row(keyring, row) -> bool:
    """A foreign event is acceptable iff its bytes still hash to its id (no payload
    tampering) AND its signature verifies under the shared keyring (authentic
    author). These are exactly the checks `Weft.events()` makes on every read."""
    eid, payload_text, author, sig = row
    try:
        payload = json.loads(payload_text)
    except (ValueError, TypeError):
        return False
    if content_id(payload, kind="event") != eid:
        return False
    return keyring.verify(author, eid, sig)


def ingest(target, rows, *, keyring=None) -> dict:
    """Union foreign rows into `target` through `Weft.ingest` — the core WEFT §2
    ACCEPTANCE gate (integrity + signature + parents-present + honest lamport). An
    "orphan" (a parent not yet present) is DEFERRED and retried until the batch reaches
    a fixpoint, so an OUT-OF-ORDER feed still unions a closed DAG; a row still orphaned
    when no progress remains is truly dangling and REJECTED. A tampered/forged/
    §2-violating row is rejected and never inserted. Returns {ingested, duplicate,
    rejected}. (`keyring` is accepted for call-compat; `Weft.ingest` verifies under the
    target's own keyring — the shared keyring in every caller.)"""
    counts = {"ingested": 0, "duplicate": 0, "rejected": 0}
    pending = list(rows)
    while pending:
        progressed, still = False, []
        for row in pending:
            status = target.ingest(row)
            if status == "orphan":
                still.append(row)                 # parents not here yet — retry a pass
                continue
            progressed = True
            counts["ingested" if status == "ingested"
                   else "duplicate" if status == "duplicate"
                   else "rejected"] += 1
        pending = still
        if not progressed:                        # no forward progress → dangling
            counts["rejected"] += len(pending)
            break
    return counts


def _refresh_head(weft):
    """After a union, refresh the Weft's `head`/`lamport` so a later LOCAL append
    still gets a strictly-greater lamport (causality preserved across the merge).
    `head` is the max-`(lamport, seq)` event — one deterministic frontier head; a
    real multi-parent local append would descend from the whole `frontier()`."""
    best_head, best_key, max_lamport = None, (-1, -1), 0
    for eid, payload, seq in weft.db.execute("SELECT id, payload, seq FROM events"):
        lam = json.loads(payload)["lamport"]
        max_lamport = max(max_lamport, lam)
        if (lam, seq) > best_key:
            best_key, best_head = (lam, seq), eid
    weft.head, weft.lamport = best_head, max_lamport


# ── one-shot reconcile ───────────────────────────────────────────────────────
def pull(source, target, *, keyring=None) -> dict:
    """Transfer source→target the events target is missing (one direction)."""
    return ingest(target, missing_for(source, target), keyring=keyring)


def sync(a, b, *, keyring=None) -> dict:
    """Bidirectional reconcile of two Wefts. Pulls each way, folds both, and reports
    whether they converged to one `state_root`. Order-independent: a then b or b then
    a yields the same union, hence the same fold (M1/M2 arrival-order independence)."""
    from decima.weave import Weave
    a_to_b = pull(a, b, keyring=keyring)
    b_to_a = pull(b, a, keyring=keyring)
    ra = Weave.fold(a).state_root()
    rb = Weave.fold(b).state_root()
    return {"a_to_b": a_to_b, "b_to_a": b_to_a,
            "converged": ra == rb, "state_root": ra if ra == rb else None}


# ── networked wire transport ─────────────────────────────────────────────────
# The functions above read a peer's `.db` directly. A real transport instead crosses
# a byte channel: a peer announces the ids it HAS, the other serializes the events the
# announcer lacks, and those bytes are ingested through `Weft.ingest` (full §2
# validation) on arrival. These functions model exactly that — a JSON string is the
# wire — so the union is transport-decoupled and could ride a socket unchanged.

def authors_of(weft) -> set:
    """The distinct principal ids that authored events in this Weft."""
    return {r[0] for r in weft.db.execute("SELECT DISTINCT author FROM events")}


def keybook_of(weft) -> dict:
    """This peer's KEYBOOK to hand a counterpart: {author pid -> Ed25519 public-key hex}
    for every principal that authored an event here — so the counterpart can VERIFY our
    events without sharing our master seed (multi-party trust). Public keys only; no
    secret ever leaves. This is what makes cross-master sync possible."""
    kr = weft.keyring
    return {pid: kr.public_key(pid) for pid in authors_of(weft)}


def trust_keybook(weft, keybook: dict) -> None:
    """Register a counterpart's keybook (public keys) so this peer can verify their
    events. Public keys confer NO authority — only verifiability; a foreign event still
    passes the full §2 acceptance gate on ingest."""
    for pid, pub in (keybook or {}).items():
        weft.keyring.trust(pid, pub)


def feed(source, have_ids) -> str:
    """`source`'s reply to a peer that already HAS `have_ids`: the events the peer
    lacks, serialized to WIRE BYTES (a JSON string), topologically ordered so parents
    precede children. This is what would cross the socket."""
    have = set(have_ids)
    rows = [list(r) for r in _rows(source) if r[0] not in have]
    rows.sort(key=lambda r: (json.loads(r[1])["lamport"], r[0]))
    return json.dumps(rows)


def apply_feed(target, wire: str, *, keyring=None) -> dict:
    """Ingest a serialized `feed` (wire bytes) into `target` through the §2 acceptance
    gate. Deserialization is part of the boundary — malformed JSON is a rejected feed."""
    try:
        rows = json.loads(wire)
    except (ValueError, TypeError):
        return {"ingested": 0, "duplicate": 0, "rejected": 0, "bad_feed": True}
    return ingest(target, [tuple(r) for r in rows], keyring=keyring)


def sync_over_wire(a, b, *, keyring=None) -> dict:
    """Bidirectional sync across the WIRE (serialized bytes), the network-shaped path:
    each peer announces its have-set, the other returns a serialized `feed`, and the
    feed is ingested through `Weft.ingest` (full §2 validation). Converges to one root —
    the same union as `sync`, but nothing reads the other peer's DB directly."""
    from decima.weave import Weave
    trust_keybook(a, keybook_of(b))                # exchange public keys first, so each
    trust_keybook(b, keybook_of(a))                # peer can VERIFY the other's events
    to_a = apply_feed(a, feed(b, event_ids(a)), keyring=keyring)   # b → wire → a
    to_b = apply_feed(b, feed(a, event_ids(b)), keyring=keyring)   # a → wire → b
    ra, rb = Weave.fold(a).state_root(), Weave.fold(b).state_root()
    return {"to_a": to_a, "to_b": to_b,
            "converged": ra == rb, "state_root": ra if ra == rb else None}


# ── REAL socket transport ────────────────────────────────────────────────────
# `sync_over_wire` proved the union is transport-decoupled: a JSON string is the
# whole wire. The functions below carry that SAME serialized `feed`/`apply_feed`
# across an actual stream socket, so two Wefts in SEPARATE processes/threads
# converge. Nothing new about the protocol — only the byte channel is real. Every
# foreign event still enters through `Weft.ingest` (full WEFT §2 acceptance), so a
# forged/tampered event on the socket is REJECTED exactly as in the in-process path.
#
# WIRE FRAMING: each message is one JSON object, length-prefixed by a 4-byte
# big-endian unsigned length header (`!I`) so the boundary is unambiguous over a
# byte stream (a socket does not preserve write boundaries). A short/closed socket
# mid-message raises `ConnectionError` — a broken peer, handled cleanly by callers.
#
# ROUND (strictly alternating, client speaks first — no deadlock):
#   client → {"have": [ids]}                              (its have-set)
#   server → {"feed": <wire>, "have": [ids]}              (what client lacks + its have-set)
#   client   apply_feed(feed)                             (client unions server's events)
#   client → {"feed": <wire>}                             (what server lacks)
#   server   apply_feed(feed)                             (server unions client's events)
# After the round both sides hold the union and fold to one `state_root`.

def _db_path(weft) -> str:
    """The on-disk path backing a Weft's SQLite connection. A Python `sqlite3`
    connection is bound to the thread that opened it, so a server running in another
    thread must open its OWN connection to the SAME file (below) — this recovers the
    path to do that. (File-backed Wefts only; `:memory:` databases are not shared.)"""
    for _seq, name, path in weft.db.execute("PRAGMA database_list"):
        if name == "main":
            return path
    return ""


def _reopen(path, keyring):
    """A thread-local Weft over the db `path` — a fresh SQLite connection the worker
    thread may legally use (a `sqlite3` connection is bound to its opening thread).
    Committed rows are visible across connections, so the caller's Weft sees the union
    after the worker commits and closes. Resolve `path`/`keyring` in the OWNING thread
    (via `_db_path`) and hand them in — never touch the caller's connection here."""
    from decima.weft import Weft
    return Weft(path, keyring)


def _send_json(sock, obj) -> None:
    """Frame one JSON object onto the socket: 4-byte big-endian length + UTF-8 bytes."""
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack("!I", len(data)) + data)


def _recv_exact(sock, n: int) -> bytes:
    """Read EXACTLY n bytes or raise — a socket `recv` may return short reads, and an
    empty read means the peer closed the connection mid-message (a broken wire)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed the socket mid-message")
        buf.extend(chunk)
    return bytes(buf)


def _recv_json(sock):
    """Read one length-prefixed JSON message framed by `_send_json`."""
    (length,) = struct.unpack("!I", _recv_exact(sock, 4))
    return json.loads(_recv_exact(sock, length).decode("utf-8"))


def serve_once(weft, conn, *, keyring=None) -> dict:
    """SERVER side of one sync round over a connected stream socket. Receives the
    peer's have-set, replies with the serialized `feed` of what the peer lacks (reuse
    `feed`) plus its own have-set, then receives the peer's feed and `apply_feed`s it
    (bidirectional). Returns what THIS side ingested {ingested, duplicate, rejected}.
    Every incoming row still passes through `Weft.ingest` (§2 acceptance)."""
    msg = _recv_json(conn)
    trust_keybook(weft, msg.get("keybook"))          # learn the peer's public keys first
    _send_json(conn, {"feed": feed(weft, msg.get("have", [])),
                      "have": sorted(event_ids(weft)),
                      "keybook": keybook_of(weft)})   # hand over ours so the peer can verify us
    incoming = _recv_json(conn).get("feed", "[]")
    return apply_feed(weft, incoming, keyring=keyring)


def serve(weft, conn, *, keyring=None, rounds=1) -> list:
    """SERVER loop: answer `rounds` sync rounds over one connection (or until the peer
    closes the socket). Returns the per-round ingest reports. A closed/broken socket
    ends the loop cleanly rather than raising past this boundary."""
    reports = []
    for _ in range(rounds):
        try:
            reports.append(serve_once(weft, conn, keyring=keyring))
        except (ConnectionError, OSError, ValueError):
            break
    return reports


def sync_socket(weft, conn, *, keyring=None) -> dict:
    """CLIENT side of one sync round over a connected stream socket: announce our
    have-set, receive + `apply_feed` the server's feed (unioning what we lack), then
    push our own feed of what the server lacks. Returns what WE ingested — converging
    the two Wefts. Foreign rows enter only through `Weft.ingest` (§2 acceptance)."""
    _send_json(conn, {"have": sorted(event_ids(weft)), "keybook": keybook_of(weft)})
    reply = _recv_json(conn)
    trust_keybook(weft, reply.get("keybook"))        # learn the server's public keys
    applied = apply_feed(weft, reply.get("feed", "[]"), keyring=keyring)
    _send_json(conn, {"feed": feed(weft, reply.get("have", []))})
    return applied


def sync_over_socket(a_weft, b_weft, *, keyring=None) -> dict:
    """Converge two Wefts over a REAL connected socket pair (`socket.socketpair()` —
    a kernel-connected pair, no ports/firewall, the most deterministic substrate). `b`
    serves in a thread; `a` drives the client round. Returns {to_a, to_b, converged,
    state_root} like `sync_over_wire`, but the bytes cross an actual socket. Threads
    and sockets are cleaned up in `finally`, even on error."""
    from decima.weave import Weave
    a_sock, b_sock = socket.socketpair()
    b_path, b_keyring = _db_path(b_weft), b_weft.keyring   # resolve in owning thread
    box = {}

    def _server():
        srv_weft = None
        try:
            srv_weft = _reopen(b_path, b_keyring)      # thread-local SQLite connection
            box["result"] = serve_once(srv_weft, b_sock, keyring=keyring)
        except Exception as exc:                       # surface to the caller thread
            box["error"] = exc
        finally:
            b_sock.close()
            if srv_weft is not None:
                srv_weft.db.close()

    t = threading.Thread(target=_server, name="decima-sync-serve")
    t.start()
    try:
        a_applied = sync_socket(a_weft, a_sock, keyring=keyring)
    finally:
        a_sock.close()
        t.join(timeout=10)
    if "error" in box:
        raise box["error"]
    ra, rb = Weave.fold(a_weft).state_root(), Weave.fold(b_weft).state_root()
    return {"to_a": a_applied, "to_b": box.get("result"),
            "converged": ra == rb, "state_root": ra if ra == rb else None}


def serve_tcp(weft, host="127.0.0.1", port=0, *, keyring=None, rounds=1) -> dict:
    """Optional TCP/localhost variant: bind a listening socket (port 0 → an OS-chosen
    free port), accept ONE connection in a background thread, and serve it. Returns
    {port, thread, server} — the caller connects a client to `port`, then joins
    `thread` and closes `server` (do it in a `finally`). Loopback only, no external
    network. `socketpair` remains the primary, most-deterministic path."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    bound_port = srv.getsockname()[1]
    w_path, w_keyring = _db_path(weft), weft.keyring       # resolve in owning thread

    def _accept():
        srv_weft = None
        try:
            conn, _ = srv.accept()
            try:
                srv_weft = _reopen(w_path, w_keyring)   # thread-local SQLite connection
                serve(srv_weft, conn, keyring=keyring, rounds=rounds)
            finally:
                conn.close()
        except OSError:
            pass                                        # server closed before accept
        finally:
            if srv_weft is not None:
                srv_weft.db.close()

    t = threading.Thread(target=_accept, name="decima-sync-tcp")
    t.start()
    return {"port": bound_port, "thread": t, "server": srv}


def sync_over_tcp(a_weft, b_weft, host="127.0.0.1", *, keyring=None) -> dict:
    """Converge two Wefts over a REAL TCP/loopback socket: `b` serves on an OS-chosen
    port, `a` connects and drives the client round. Same convergence contract as
    `sync_over_socket`. Cleans up client socket, server thread and listener in
    `finally`."""
    from decima.weave import Weave
    srv = serve_tcp(b_weft, host, 0, keyring=keyring)
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        cli.connect((host, srv["port"]))
        a_applied = sync_socket(a_weft, cli, keyring=keyring)
    finally:
        cli.close()
        srv["thread"].join(timeout=10)
        srv["server"].close()
    ra, rb = Weave.fold(a_weft).state_root(), Weave.fold(b_weft).state_root()
    return {"to_a": a_applied,
            "converged": ra == rb, "state_root": ra if ra == rb else None}
