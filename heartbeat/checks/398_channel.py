"""SYNC CHANNEL — confidentiality + peer authentication (Phase 1 Enforcement).

Weft events were always Ed25519-signed, but the sync wire was PLAINTEXT and the
channel UNAUTHENTICATED: anyone on the wire could read every event and any process
could speak the protocol. `decima/sync.py` now enforces a `SecureChannel` on every
socket-crossing sync — mutual authentication (each peer Ed25519-signs the handshake
transcript, binding ephemeral X25519 keys to a SELF-CERTIFYING identity, pid =
blake2b(pubkey)), then SecretBox encryption + MAC on every frame with a strict
monotonic INT counter as the nonce. This check proves the boundary is ENFORCED, with
REAL crypto over in-memory socketpairs — fully offline, no mocks of the crypto:

  (a) honest peers with real keys complete the handshake and sync events as before —
      and the RAW BYTES observed on the wire never contain the event plaintext
      (confidentiality watched at the socket, not asserted from a flag); every synced
      event is provenance-stamped with the AUTHENTICATED peer identity;
  (b) a peer that cannot prove the expected identity is REJECTED before any event
      flows: a pinned `expected_peer` mismatch; a claimed pid whose presented key
      hashes elsewhere (self-certification); a transcript signature made with the
      WRONG key (possession of the private key is what authenticates);
  (c) a TAMPERED ciphertext frame is refused (MAC failure);
  (d) a REPLAYED frame (exact wire bytes resent) is refused BEFORE decryption
      (strict monotonic counter);
  (e) a PLAINTEXT/legacy frame is refused outright — both a legacy peer's first
      message and a plaintext frame injected mid-session. The plaintext path is GONE.

Contract: run(k, line). Fail loud (assert / expected ChannelError).
"""
import os
import socket
import tempfile
import threading

import nacl.public

from decima import sync
from decima.crypto import Keyring
from decima.weft import Weft, ASSERT
from decima.weave import Weave


class _Tap:
    """A socket wrapper that RECORDS every byte crossing it (both directions) —
    the observer that proves confidentiality on the actual wire."""

    def __init__(self, sock, log: bytearray):
        self._sock, self._log = sock, log

    def sendall(self, data):
        self._log.extend(data)
        return self._sock.sendall(data)

    def recv(self, n):
        data = self._sock.recv(n)
        self._log.extend(data)
        return data


def _weft(seed: bytes):
    kr = Keyring(seed=seed)
    return Weft(os.path.join(tempfile.mkdtemp(), "w.db"), kr), kr


def _round(A, B, *, tap=None, client_kw=None, server_kw=None) -> dict:
    """One client(A)↔server(B) sync round over a socketpair; B serves in a thread
    (its own SQLite connection). Returns whatever happened on both sides — results
    AND errors — so refusal scenarios can be asserted precisely."""
    a_sock, b_sock = socket.socketpair()
    b_path, b_kr = sync._db_path(B), B.keyring
    box = {}

    def _srv():
        w = None
        try:
            w = sync._reopen(b_path, b_kr)
            box["server"] = sync.serve_once(w, b_sock, **(server_kw or {}))
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller thread
            box["server_error"] = exc
        finally:
            b_sock.close()
            if w is not None:
                w.db.close()

    t = threading.Thread(target=_srv, name="chan-serve")
    t.start()
    conn = _Tap(a_sock, tap) if tap is not None else a_sock
    try:
        try:
            box["client"] = sync.sync_socket(A, conn, **(client_kw or {}))
        except Exception as exc:  # noqa: BLE001
            box["client_error"] = exc
    finally:
        a_sock.close()
        t.join(timeout=10)
    return box


def _channel_pair(kr_a, kr_b):
    """An established SecureChannel pair over a socketpair (server side handshakes
    in a thread). Returns (client_channel, server_channel, client_raw_socket)."""
    a_sock, b_sock = socket.socketpair()
    box = {}

    def _srv():
        try:
            box["ch"] = sync.accept_channel(b_sock, kr_b)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
            b_sock.close()

    t = threading.Thread(target=_srv, name="chan-accept")
    t.start()
    ch_a = sync.connect_channel(a_sock, kr_a)
    t.join(timeout=10)
    assert "ch" in box, f"server handshake failed: {box.get('error')}"
    return ch_a, box["ch"], a_sock


def run(k, line):
    line("\n== SYNC CHANNEL — mutual authentication + confidentiality on the wire ==")

    # ── (a) honest peers: real keys, real handshake, events flow — ENCRYPTED ──────
    A, krA = _weft(b"chanA-seed-32-bytes-exactly!!!!!")
    B, krB = _weft(b"chanB-seed-32-bytes-exactly!!!!!")
    a_author = krA.mint_keyed("chan-author")           # self-certifying event authors
    b_author = krB.mint_keyed("chan-author")           # same name, different pid (keyed)
    MARK_A, MARK_B = "wire-secret-alpha-7Q", "wire-secret-beta-9Z"
    ev_a = A.append(a_author.id, ASSERT,
                    {"cell": "a-note", "type": "note", "content": {"t": MARK_A}})
    ev_b = B.append(b_author.id, ASSERT,
                    {"cell": "b-note", "type": "note", "content": {"t": MARK_B}})
    # control: the markers really are in the serialized feeds that must cross the wire
    assert MARK_A in sync.feed(A, []) and MARK_B in sync.feed(B, [])

    wire = bytearray()                                  # every byte that crosses, taped
    rep = _round(A, B, tap=wire)
    assert "client" in rep and "server" in rep, rep
    assert rep["client"]["ingested"] == 1 and rep["server"]["ingested"] == 1, rep
    assert sync.event_ids(A) == sync.event_ids(B), "have-sets equal after channel sync"
    assert Weave.fold(A).state_root() == Weave.fold(B).state_root(), "one state_root"
    got = Weave.fold(B).get("a-note")
    assert got is not None and got.content["t"] == MARK_A, "the event arrived INTACT"
    line("  honest peers: handshake + encrypted round → converged, events intact ✓")

    # confidentiality, observed at the socket: the event plaintext NEVER crossed.
    assert len(wire) > 0, "the tap must have seen traffic"
    assert MARK_A.encode() not in bytes(wire), "event plaintext leaked on the wire!"
    assert MARK_B.encode() not in bytes(wire), "event plaintext leaked on the wire!"
    assert b'"feed"' not in bytes(wire) and b'"have"' not in bytes(wire), \
        "protocol structure leaked in plaintext"
    line(f"  confidentiality: {len(wire)} wire bytes taped; event payloads + protocol "
         "frames appear NOWHERE in them ✓")

    # provenance: each synced event is stamped with the AUTHENTICATED peer identity.
    chanA, chanB = sync.channel_identity(krA), sync.channel_identity(krB)
    assert chanA != chanB
    assert Keyring.keyed_pid(krA.public_key(chanA)) == chanA, "channel pid self-certifies"
    pb = sync.peer_provenance(B, ev_a.id)               # a's event, as B received it
    pa = sync.peer_provenance(A, ev_b.id)               # b's event, as A received it
    assert pb and pb["peer"] == chanA, f"B must record A's authenticated identity: {pb}"
    assert pa and pa["peer"] == chanB, f"A must record B's authenticated identity: {pa}"
    assert pa["session"] == pb["session"], "one handshake session, recorded on both sides"
    assert sync.peer_provenance(A, ev_a.id) is None, "a LOCAL event carries no peer"
    line(f"  provenance: synced events stamped with the authenticated peer "
         f"(B←{pb['peer'][:8]}, A←{pa['peer'][:8]}; session {pa['session'][:8]}…) ✓")

    # ── (b) a peer that cannot prove the expected identity is REJECTED ────────────
    b_count = B.count()
    A.append(a_author.id, ASSERT,
             {"cell": "a-2", "type": "note", "content": {"t": "post-pin"}})

    # b1. server PINS an identity; an honest-but-different peer is refused.
    other = Keyring(seed=b"other-seed-32-bytes-exactly!!!!!")
    pinned = sync.channel_identity(other)               # B will only talk to `other`
    rej = _round(A, B, server_kw={"expected_peer": pinned})
    assert isinstance(rej.get("server_error"), sync.ChannelError), rej
    assert "client" not in rej, "the client round must NOT have completed"
    assert B.count() == b_count, "no event may flow past a refused handshake"
    line("  pinned expected_peer: a different (real, honest) identity is refused ✓")

    # b2. client pins too — a server with the wrong identity is refused symmetrically.
    rej = _round(A, B, client_kw={"expected_peer": pinned})
    assert isinstance(rej.get("client_error"), sync.ChannelError), rej
    assert B.count() == b_count
    line("  client-side pinning: the wrong SERVER identity is refused too ✓")

    # b3. IMPERSONATION: an attacker claims A's channel pid. Two attempts:
    #     (i) its own key (pid != blake2b(key) → self-certification fails);
    #     (ii) A's real public key but a signature made with its OWN key
    #          (possession of the private key is what authenticates).
    evil = Keyring(seed=b"evil!-seed-32-bytes-exactly!!!!!")
    evil_pid = sync.channel_identity(evil)
    for label, claim_key, note in (
            ("own key under A's pid", evil.public_key(evil_pid), "self-cert fails"),
            ("A's key, wrong signer", krA.public_key(chanA), "signature fails")):
        a_sock, b_sock = socket.socketpair()
        b_path = sync._db_path(B)
        box = {}

        def _srv():
            w = None
            try:
                w = sync._reopen(b_path, B.keyring)
                box["server"] = sync.serve_once(w, b_sock, expected_peer=chanA)
            except Exception as exc:  # noqa: BLE001
                box["server_error"] = exc
            finally:
                b_sock.close()
                if w is not None:
                    w.db.close()

        t = threading.Thread(target=_srv)
        t.start()
        refused_before_reply = False
        try:
            eph = nacl.public.PrivateKey.generate()
            hello = {"proto": sync.PROTO, "pid": chanA, "identity_key": claim_key,
                     "eph": eph.public_key.encode().hex()}
            sync._send_json(a_sock, hello)
            server_hello = sync._recv_json(a_sock)
            transcript = sync._transcript(hello, server_hello)
            sync._send_json(a_sock, {"sig": evil.sign(evil_pid, transcript)})
            try:
                sync._recv_json(a_sock)                 # server must refuse + close
            except (ConnectionError, OSError):
                refused_before_reply = True
        finally:
            a_sock.close()
            t.join(timeout=10)
        assert isinstance(box.get("server_error"), sync.ChannelError), (label, box)
        assert refused_before_reply, f"{label}: server must refuse BEFORE its sig reply"
        assert B.count() == b_count, f"{label}: no event may flow"
        line(f"  impersonation ({label}) → refused before any reply ({note}) ✓")

    # ── (c)/(d)/(e-mid) frame discipline on an established channel ────────────────
    ch_a, ch_b, a_raw = _channel_pair(krA, krB)
    ch_a.send({"probe": 1})
    assert ch_b.recv() == {"probe": 1}, "honest frame crosses the channel"

    # (d) REPLAY: resend the EXACT sealed bytes → refused before decryption.
    frame = ch_a.seal({"pay": "once"})                 # counter 2 — real ciphertext
    a_raw.sendall(frame)
    assert ch_b.recv() == {"pay": "once"}
    a_raw.sendall(frame)                               # the very same wire bytes again
    try:
        ch_b.recv()
        assert False, "a REPLAYED frame must be refused"
    except sync.ChannelError as e:
        assert "replayed" in str(e).lower(), e
    line("  replayed frame (exact bytes resent) → refused by the counter, pre-decrypt ✓")

    # (c) TAMPER: flip one ciphertext byte → MAC failure.
    frame = bytearray(ch_a.seal({"amount": 100}))      # ints, not floats, as ever
    frame[-1] ^= 0x01
    a_raw.sendall(bytes(frame))
    try:
        ch_b.recv()
        assert False, "a TAMPERED frame must be refused"
    except sync.ChannelError as e:
        assert "tamper" in str(e).lower(), e
    line("  tampered ciphertext (one bit flipped) → refused by the MAC ✓")

    # (e-mid) a PLAINTEXT frame injected into a live channel never parses as sealed.
    sync._send_json(a_raw, {"feed": "[]", "have": []})  # the OLD wire format
    try:
        ch_b.recv()
        assert False, "a plaintext frame must be refused"
    except sync.ChannelError:
        pass
    line("  plaintext frame injected mid-session → refused (never parses as sealed) ✓")

    # ── (e) a LEGACY peer — first message is the old plaintext protocol ────────────
    a_sock, b_sock = socket.socketpair()
    b_path = sync._db_path(B)
    box = {}

    def _srv2():
        w = None
        try:
            w = sync._reopen(b_path, B.keyring)
            box["server"] = sync.serve_once(w, b_sock)
        except Exception as exc:  # noqa: BLE001
            box["server_error"] = exc
        finally:
            b_sock.close()
            if w is not None:
                w.db.close()

    t = threading.Thread(target=_srv2)
    t.start()
    got_feed = None
    try:
        sync._send_json(a_sock, {"have": [], "keybook": {}})   # speak the DEAD protocol
        try:
            got_feed = sync._recv_json(a_sock)                 # must NEVER be answered
        except (ConnectionError, OSError):
            pass
    finally:
        a_sock.close()
        t.join(timeout=10)
    err = box.get("server_error")
    assert isinstance(err, sync.ChannelError) and "plaintext/legacy" in str(err), box
    assert got_feed is None, f"a legacy peer must get NOTHING back, got: {got_feed}"
    assert B.count() == b_count, "no event may flow to/from a plaintext peer"
    line("  legacy plaintext peer → refused outright; no feed, no events — "
         "the plaintext path does not exist ✓")

    line("  → sync channel: peers prove Ed25519 identity over a signed transcript, "
         "frames are encrypted+MACed with strict int counters, and plaintext / replay / "
         "tamper / wrong-identity all die at the boundary — before any event flows.")
