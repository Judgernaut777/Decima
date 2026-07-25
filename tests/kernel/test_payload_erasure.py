"""PHYSICAL byte-erasure for REDACT — encrypted payloads + key destruction (FOLD §10.3).

Before this, REDACT erased the payload from every PROJECTION but the plaintext stayed in
the SQLite file forever ("physical byte-erasure deferred"). Now a redactable payload is
sealed at rest under a per-payload data key held outside the log, and REDACT DESTROYS the
key. The load-bearing claim these tests pin:

  * the plaintext is NEVER in the store — not before the redaction, not after (the
    counterfactual test proves an UNSEALED payload *is* in the file, so this assertion has
    teeth);
  * after erasure the event id still RECOMPUTES and the signature still VERIFIES — the
    whole log reads clean through `weft.events()`, and the skeleton (assert + redact)
    remains, so tamper-evidence is untouched;
  * the payload is unrecoverable by REPLAY: a fresh Weft over the same DB + the same vault
    folds the cell as a content-free tombstone;
  * ciphertext without its key is undecryptable, and a wrong key never yields content.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3

import pytest

from decima.kernel import lifecycle, model, sealing
from decima.kernel.crypto import Keyring
from decima.kernel.hashing import content_id
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft, WeftError

_SEED = bytes(32)
# A fixed data key makes the sealed append byte-reproducible (a fresh random key is the
# default; determinism here is what lets us assert on exact stored bytes).
_KEY = bytes(range(32))
SECRET = "patient-42-diagnosis-confidential"


def _weft(tmp_path: pathlib.Path) -> tuple[Weft, str, str, sealing.DirectoryPayloadVault]:
    kr = Keyring(seed=_SEED)
    vault = sealing.DirectoryPayloadVault(str(tmp_path / "payload-keys"))
    db = str(tmp_path / "weft.db")
    weft = Weft(db, kr, vault=vault)
    return weft, weft.keyring.mint("human").id, db, vault


def _db_bytes(db: str) -> bytes:
    with open(db, "rb") as fh:
        return fh.read()


# ── the plaintext never reaches the store ─────────────────────────────────────
def test_sealed_payload_plaintext_is_never_in_the_db_file(tmp_path: pathlib.Path) -> None:
    weft, human, db, _vault = _weft(tmp_path)
    model.assert_sealed(weft, human, "cell:secret", "secret", {"dx": SECRET}, key=_KEY)
    assert SECRET.encode() not in _db_bytes(db)
    # ...while the fold still reads it perfectly (encryption is transparent to consumers).
    cell = Weave.fold(weft).get("cell:secret")
    assert cell is not None and cell.content == {"dx": SECRET}


def test_counterfactual_unsealed_payload_is_in_the_db_file(tmp_path: pathlib.Path) -> None:
    """Teeth for the assertion above: without sealing the plaintext IS on disk, and a
    projection-only REDACT leaves it there — the exact gap this item closes."""
    weft, human, db, _vault = _weft(tmp_path)
    model.assert_content(weft, human, "cell:plain", "secret", {"dx": SECRET})
    assert SECRET.encode() in _db_bytes(db)
    lifecycle.redact(weft, human, "cell:plain")
    assert SECRET.encode() in _db_bytes(db)  # projection erasure ≠ byte erasure


# ── erasure: the key is gone, the log still verifies ──────────────────────────
def test_redact_destroys_the_key_and_the_log_still_verifies(tmp_path: pathlib.Path) -> None:
    weft, human, db, vault = _weft(tmp_path)
    ev = model.assert_sealed(weft, human, "cell:secret", "secret", {"dx": SECRET}, key=_KEY)
    ref = sealing.key_ref_of(json.loads(_stored_payload(db, ev.id))["body"]["content"])
    assert ref is not None and vault.get(ref) == _KEY

    destroyed = lifecycle.redact(weft, human, "cell:secret")
    assert weft.redacted_cells() == ["cell:secret"]
    assert vault.get(ref) is None  # the data key is GONE
    assert not os.path.exists(tmp_path / "payload-keys" / f"{ref}.key")
    assert _KEY not in _db_bytes(db)  # the key never lived in the store either
    assert destroyed.verb == "RETRACT"

    # VERIFY-ON-READ STILL PASSES: `events()` recomputes every content id and checks every
    # signature against the STORED bytes, which erasure did not touch.
    events = list(weft.events())
    assert len(events) == 2  # the skeleton remains: the assert AND the redact
    rows = sqlite3.connect(db).execute("SELECT id, payload FROM events")
    stored = {row[0]: row[1] for row in rows}
    for e in events:
        assert content_id(json.loads(stored[e.id]), kind="event") == e.id
        assert weft.keyring.verify(e.author, e.id, e.sig)


def test_erased_payload_is_unrecoverable_by_replay(tmp_path: pathlib.Path) -> None:
    weft, human, db, vault = _weft(tmp_path)
    model.assert_sealed(weft, human, "cell:secret", "secret", {"dx": SECRET}, key=_KEY)
    lifecycle.redact(weft, human, "cell:secret")

    # A brand-new Weft over the same DB and the same vault — a full replay from genesis.
    reopened = Weft(db, Keyring(seed=_SEED), vault=vault)
    cell = Weave.fold(reopened).get("cell:secret")
    assert cell is not None
    assert cell.content == {} and cell.redacted and cell.retracted
    # Even the raw ASSERT body (before the RETRACT folds) carries no payload any more.
    assert_ev = next(e for e in reopened.events() if e.verb == "ASSERT")
    assert assert_ev.body["content"] == {} and assert_ev.body["erased"] == 1
    assert SECRET.encode() not in _db_bytes(db)


def test_state_root_after_redaction_is_reached_identically_sealed_or_not(
    tmp_path: pathlib.Path,
) -> None:
    """Sealing changes WHERE the bytes are, never what the fold means: a redacted cell's
    post-redaction logical state (hence the whole `state_root` leaf shape) is the same
    content-free tombstone whether or not the payload was sealed."""
    sealed_weft, human, _db, _vault = _weft(tmp_path / "sealed")
    model.assert_sealed(sealed_weft, human, "c", "secret", {"dx": SECRET}, key=_KEY)
    lifecycle.redact(sealed_weft, human, "c")
    plain_weft, human2, _db2, _v2 = _weft(tmp_path / "plain")
    model.assert_content(plain_weft, human2, "c", "secret", {"dx": SECRET})
    lifecycle.redact(plain_weft, human2, "c")

    a = Weave.fold(sealed_weft).get("c")
    b = Weave.fold(plain_weft).get("c")
    assert a is not None and b is not None
    assert (a.content, a.redacted, a.retracted, a.version) == (
        b.content,
        b.redacted,
        b.retracted,
        b.version,
    )


def test_erasure_is_idempotent_and_never_freelance(tmp_path: pathlib.Path) -> None:
    weft, human, _db, _vault = _weft(tmp_path)
    model.assert_sealed(weft, human, "cell:secret", "secret", {"dx": SECRET}, key=_KEY)
    with pytest.raises(WeftError, match="no effective REDACT"):
        weft.erase_redacted("cell:secret")  # eligibility #1: erasure needs the log's say-so
    lifecycle.redact(weft, human, "cell:secret", erase=False)
    first = weft.erase_redacted("cell:secret")
    assert len(first) == 1
    assert weft.erase_redacted("cell:secret") == []  # idempotent — nothing left to destroy


def test_unsealed_cells_are_untouched_by_the_sweep(tmp_path: pathlib.Path) -> None:
    """Regression guard: `redact` now sweeps keys, and for every existing (unsealed)
    payload that sweep must be a pure no-op."""
    weft, human, _db, _vault = _weft(tmp_path)
    model.assert_content(weft, human, "cell:plain", "note", {"text": "hi"})
    lifecycle.redact(weft, human, "cell:plain")
    assert weft.sealed_key_refs("cell:plain") == []
    cell = Weave.fold(weft).get("cell:plain")
    assert cell is not None and cell.content == {} and cell.redacted


# ── fail-closed properties of the envelope itself ─────────────────────────────
def test_ciphertext_without_the_key_is_undecryptable(tmp_path: pathlib.Path) -> None:
    env, key = sealing.seal({"dx": SECRET}, _KEY)
    assert SECRET not in json.dumps(env)
    with pytest.raises(sealing.SealError, match="authentication"):
        sealing.unseal(env, bytes(32))  # wrong key
    body = env[sealing.SEALED]
    tampered = {sealing.SEALED: {**body, "ct": body["ct"][:-2] + "aa"}}
    with pytest.raises(sealing.SealError):
        sealing.unseal(tampered, key)
    assert sealing.unseal(env, key) == {"dx": SECRET}  # the honest path still opens


def test_sealing_is_refused_for_kernel_critical_types_and_without_a_vault(
    tmp_path: pathlib.Path,
) -> None:
    weft, human, _db, _vault = _weft(tmp_path)
    with pytest.raises(WeftError, match="never be sealed"):
        model.assert_sealed(weft, human, "cap:1", "capability", {"caveats": {}})
    novault = Weft(str(tmp_path / "nv.db"), Keyring(seed=_SEED))
    with pytest.raises(WeftError, match="payload vault"):
        model.assert_sealed(novault, human, "c", "secret", {"dx": SECRET})
    assert novault.count() == 0  # fail closed: nothing was recorded


def test_fresh_keys_mean_no_cross_payload_dedup(tmp_path: pathlib.Path) -> None:
    """FOLD §10.3 forbids dedup across security realms by default: two seals of the SAME
    plaintext with fresh keys share no ciphertext, so no erasure domain is shared."""
    weft, human, _db, _vault = _weft(tmp_path)
    model.assert_sealed(weft, human, "a", "secret", {"dx": SECRET})
    model.assert_sealed(weft, human, "b", "secret", {"dx": SECRET})
    refs = weft.sealed_key_refs()
    assert len(refs) == 2 and refs[0] != refs[1]
    lifecycle.redact(weft, human, "a")
    fold = Weave.fold(weft)
    a, b = fold.get("a"), fold.get("b")
    assert a is not None and b is not None
    assert a.content == {} and b.content == {"dx": SECRET}  # erasing a leaves b readable


def test_a_peer_without_the_key_holds_a_verifiable_skeleton(tmp_path: pathlib.Path) -> None:
    """FOLD §9 / SYNC.md: "a peer lacking a body may retain an event skeleton and encrypted
    blob reference". A sealed event syncs as ciphertext; the peer's §2 acceptance gate
    accepts it (the id recomputes), and it folds as a content-free skeleton there."""
    src, human, db, _vault = _weft(tmp_path / "src")
    model.assert_sealed(src, human, "cell:secret", "secret", {"dx": SECRET}, key=_KEY)
    rows = list(
        sqlite3.connect(db).execute("SELECT id, payload, author, sig FROM events ORDER BY seq")
    )

    peer = Weft(str(tmp_path / "peer.db"), Keyring(seed=_SEED))  # no vault at all
    for row in rows:
        assert peer.ingest(tuple(row)) == "ingested"
    cell = Weave.fold(peer).get("cell:secret")
    assert cell is not None and cell.content == {}
    assert SECRET.encode() not in _db_bytes(str(tmp_path / "peer.db"))


def test_vault_primitives(tmp_path: pathlib.Path) -> None:
    mem = sealing.MemoryPayloadVault()
    mem.put("r", _KEY)
    assert mem.get("r") == _KEY
    assert mem.destroy("r") is True and mem.get("r") is None and mem.destroy("r") is False
    base = sealing.PayloadVault()
    for call in (lambda: base.put("r", _KEY), lambda: base.get("r"), lambda: base.destroy("r")):
        with pytest.raises(NotImplementedError):
            call()
    disk = sealing.DirectoryPayloadVault(str(tmp_path / "v"))
    assert oct(os.stat(tmp_path / "v").st_mode)[-3:] == "700"
    disk.put("r", _KEY)
    assert oct(os.stat(tmp_path / "v" / "r.key").st_mode)[-3:] == "600"
    assert disk.get("r") == _KEY and disk.get("nope") is None
    with pytest.raises(sealing.SealError, match="unsafe key ref"):
        disk.get("../../escape")
    with pytest.raises(sealing.SealError, match="32 bytes"):
        sealing.seal({"a": 1}, b"short")
    with pytest.raises(sealing.SealError, match="not a sealed envelope"):
        sealing.unseal({"a": 1}, _KEY)


def _stored_payload(db: str, eid: str) -> str:
    row = sqlite3.connect(db).execute("SELECT payload FROM events WHERE id=?", (eid,)).fetchone()
    assert row is not None
    return str(row[0])
