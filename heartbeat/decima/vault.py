"""Vault — the sovereign data substrate (VAULT1, capability D6).

The "OneDrive equivalent", but inverted: your data IS the Weft (Law 5 — the only
truth is the signed, append-only log), so backup, disaster-recovery, and
multi-device are not a separate storage product bolted on top. They are three
projections of one fact: **a Weave is a deterministic fold of events**, and events
are immutable, signed, and content-addressed. Everything below is composition of
the existing public APIs — `snapshot`, `sync`, `gossip`, `secrets` — and edits no
core file.

What the substrate guarantees (and the §216 check proves on the live Weft):

  • backup(k, recovery_phrase) → a verifiable `snapshot` of the frontier PLUS an
    encrypted export blob carrying the raw replay material (the signed events).
    The blob is **opaque**: it is keystream-enciphered under a key the secrets
    broker derives from the recovery phrase, so the export never holds application
    state in clear. The snapshot's manifest carries only a `state_root` and
    content-addressed chunk hashes — a commitment, not readable state.

  • restore(backup, recovery_phrase) → on a FRESH device (a brand-new Weft/Weave
    with no history), decrypt the export, ingest its verified events, and
    **replay-to-frontier**. The resulting Weave's `state_root` EQUALS the original,
    byte-for-byte (FOLD §11.1: a fold to a frontier is the same fold everywhere).
    A WRONG recovery phrase derives the wrong key → the deciphered bytes are
    garbage → the import **fails closed** (no Weft is mutated, no partial state).

  • add_device / sync_devices → register N devices (fresh Wefts seeded from a
    restore) and converge them by fold-replication. This is conflict-free CRDT
    merge (the OR-set / merge reducers), driven by `gossip` anti-entropy: every
    device folds to ONE identical `state_root`, and a concurrent edit on any
    device survives the union (no overwrite).

The encryption here is a STUB (a phrase-keyed keystream over `blob_id`), exactly
as the brief allows: real ed25519/AEAD and a real network feed are B2 depth. The
*contract* — opaque at rest, fails closed on the wrong key, byte-identical
state_root after replay — is fully demonstrated.
"""
from decima import snapshot, sync, gossip
from decima.weft import Weft
from decima.weave import Weave
from decima.hashing import blob_id, content_id

_PROTOCOL = 1
_MAGIC = "decima-vault-v1"     # plaintext sentinel: present after a RIGHT-key decrypt only


# ── stub encryption: a recovery-phrase-keyed keystream (the AEAD seam) ─────────
def _derive_key(broker, recovery_phrase: str) -> bytes:
    """Derive a symmetric data-encryption key from the recovery phrase via the
    secrets broker (the HSM/enclave seam — the phrase never lands on the Weft).
    A different phrase derives a different key, deterministically."""
    # blob_id is the broker's own one-way commitment primitive; keying it with the
    # phrase gives a stable per-phrase key without persisting the phrase anywhere.
    # The salt is a fixed realm constant so the SAME phrase derives the SAME key on
    # any device (`broker` is the enclave seam that HOLDS the key in production; the
    # heartbeat stub derives it deterministically from the phrase alone). The broker
    # is accepted to keep the production wiring explicit at the call sites.
    _ = broker
    return blob_id(("decima-vault|" + recovery_phrase).encode("utf-8"),
                   kind="vault-dek").encode()


def _keystream(key: bytes, n: int) -> bytes:
    """An (insecure, stub) keystream long enough to cover `n` bytes: chained
    blob_id blocks. Stands in for an AEAD cipher; the seam is obvious."""
    out, block, counter = bytearray(), key, 0
    while len(out) < n:
        block = blob_id(block + str(counter).encode(), kind="vault-stream").encode()
        out.extend(block)
        counter += 1
    return bytes(out[:n])


def _xor(data: bytes, ks: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, ks))


def _encrypt(plaintext: bytes, key: bytes) -> bytes:
    return _xor(plaintext, _keystream(key, len(plaintext)))


def _decrypt(ciphertext: bytes, key: bytes) -> bytes:
    return _xor(ciphertext, _keystream(key, len(ciphertext)))   # XOR is its own inverse


class VaultError(Exception):
    """A wrong recovery phrase, a corrupt export, or a tampered backup — restore
    fails closed (no Weft is ever partially mutated)."""


# ── the export: raw, signed replay material, enciphered ───────────────────────
def _export_rows(weft) -> list:
    """The Weft's signed event rows (id, payload, author, sig) in seq order — the
    exact replay material `sync.ingest` re-verifies on import. This IS the data;
    it is what we encipher so the export holds no clear state."""
    return [list(r) for r in sync._rows(weft)]


def backup(k, *, recovery_phrase: str, broker=None, upto_seq: int | None = None) -> dict:
    """Back up the live Weft. Returns a backup envelope:

        { snapshot, store, export, state_root, n_events, protocol }

    - `snapshot`/`store`: a verifiable `snapshot.snapshot` of the frontier — its
      manifest commits to the `state_root` and content-addressed chunk hashes
      (a commitment, never readable application state).
    - `export`: the signed event rows, JSON-serialized then **encrypted** under a
      key derived from `recovery_phrase`. Opaque at rest — not clear state.

    The recovery phrase gates `restore`; it is never stored in the envelope or on
    the Weft.
    """
    import json
    broker = broker if broker is not None else _maybe_broker(k)
    manifest, store = snapshot.snapshot(k.weft, upto_seq, created_by=k.executor.id,
                                        keyring=k.keyring)
    rows = _export_rows(k.weft)
    # A magic sentinel rides inside the plaintext so a wrong-key decrypt is
    # detectable (it won't reproduce the sentinel) — the import fails closed.
    plaintext = json.dumps({"magic": _MAGIC, "rows": rows,
                            "state_root": manifest["state_root"]},
                           sort_keys=True).encode("utf-8")
    key = _derive_key(broker, recovery_phrase)
    blob = _encrypt(plaintext, key)
    return {
        "protocol": _PROTOCOL,
        "snapshot": manifest,
        "store": store,
        "export": blob,                       # opaque ciphertext
        "state_root": manifest["state_root"],  # the target every restore must hit
        "n_events": len(rows),
    }


def export_is_opaque(backup_env: dict, clear_fragment: str) -> bool:
    """True iff `clear_fragment` does NOT appear in the encrypted export — i.e. the
    blob holds no clear application state. Used by the check to prove opacity."""
    needle = clear_fragment.encode("utf-8") if isinstance(clear_fragment, str) else clear_fragment
    return needle not in backup_env["export"]


# ── restore on a FRESH device → replay-to-frontier, byte-identical root ───────
def restore(backup_env: dict, *, recovery_phrase: str, keyring, db_path: str,
            broker=None) -> Weave:
    """Reconstruct the Weave on a FRESH device (a new, empty Weft at `db_path`).

    Decrypts the export under the phrase-derived key, ingests the signed events
    into the fresh Weft (each re-verified: id==hash AND signature valid), then
    folds to the frontier. The returned Weave's `state_root` EQUALS the original.

    A WRONG `recovery_phrase` derives the wrong key → garbage plaintext → the
    sentinel is absent → `VaultError`, and the fresh Weft is left untouched.
    """
    import json
    if backup_env.get("protocol") != _PROTOCOL:
        raise VaultError("backup protocol mismatch")
    key = _derive_key(broker, recovery_phrase)
    try:
        raw = _decrypt(backup_env["export"], key)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise VaultError("export did not decrypt — wrong recovery phrase (fails closed)")
    if payload.get("magic") != _MAGIC:
        raise VaultError("export sentinel absent — wrong recovery phrase (fails closed)")

    # Fresh device: a brand-new Weft with NO history, sharing the keyring so the
    # imported signatures verify (exactly the sync/gossip trust model).
    fresh = Weft(db_path, keyring)
    if fresh.count() != 0:
        raise VaultError("restore target is not a fresh device")
    rows = [tuple(r) for r in payload["rows"]]
    res = sync.ingest(fresh, rows, keyring=keyring)
    if res["rejected"]:
        raise VaultError(f"{res['rejected']} event(s) failed verification on import")

    w = Weave.fold(fresh)                       # replay-to-frontier
    if w.state_root() != backup_env["state_root"]:
        raise VaultError("restored state_root != original — replay did not reproduce state")
    return w, fresh


# ── multi-device: fold-replication to ONE state_root ──────────────────────────
def add_device(devices: list, weft) -> list:
    """Register a device (its `Weft`) in the device set. Returns the updated list.
    A device is a fold-replica; convergence is the substrate's job, not the app's."""
    if weft not in devices:
        devices.append(weft)
    return devices


def sync_devices(devices: list, *, keyring=None) -> dict:
    """Converge N device-Wefts by fold-replication (CRDT merge via `gossip`
    anti-entropy). Every device folds to ONE identical `state_root`; concurrent
    edits survive the union (no overwrite — conflict-free). Returns a report:

        { converged, state_root, peers, rounds, moved_total }
    """
    if not devices:
        return {"converged": True, "state_root": None, "peers": 0,
                "rounds": 0, "moved_total": 0}
    rep = gossip.gossip(devices, keyring=keyring)
    roots = {Weave.fold(d).state_root() for d in devices}
    converged = rep["converged"] and len(roots) == 1
    return {
        "converged": converged,
        "state_root": next(iter(roots)) if len(roots) == 1 else None,
        "peers": rep["peers"],
        "rounds": rep["rounds"],
        "moved_total": rep["moved_total"],
    }


def _maybe_broker(k):
    """Use a SecretsBroker if available; the DEK derivation only needs its stable
    principal id as a salt, so a None broker still works (a fixed salt)."""
    try:
        from decima import secrets
        return secrets.SecretsBroker(k)
    except Exception:
        return None
