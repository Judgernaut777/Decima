"""Per-principal key custody — the DEFAULT signing posture for every REAL run (T1.1).

`decima.kernel.crypto.Keyring()` still defaults to `DerivedKeyStore`, so a bare library or
test construction stays reproducible (one master seed derives every principal's key). That
is DEV-ONLY: it fuses every identity under a single secret, and whoever holds that secret
can sign as *every* principal — which collapses split custody and with it the ocap + Morta
trust model (SECURITY.md, "Key custody"). So every real path — the API daemon
(`services.api.server.build_application`), first-run provisioning
(`services.provision.first_run`), and the operations CLI (`cli.main`) — builds its Keyring
HERE instead, over a `DirectoryKeyStore`: one 32-byte Ed25519 seed per principal, minted
with `os.urandom`, persisted 0600 inside a 0700 directory. Consequences:

  * compromising one principal's key yields NO other principal's key;
  * a restart re-loads the SAME keys from disk (warm start), so the Weft a previous run
    signed still verifies and the fold still reaches the same `state_root`;
  * a principal with no provisioned key FAILS CLOSED — `sign`/`public_key` raise KeyError,
    which `Keyring.verify` turns into False rather than a silent derive-and-accept;
  * the master seed remains a secret but is no longer a signing key: it seeds only the
    non-signing derivations (the loopback pairing secret, `Keyring.mint_keyed`).

NOTHING HERE TOUCHES SIGNED OR HASHED CONTENT. A private key is never Weft content, and a
signature is NOT part of an event id (`weft.Event.hashed_payload` excludes `sig`; the
checkpoint/snapshot signers attach the signature after computing the id). Content ids,
canonical bytes, and the folded `state_root` are therefore unchanged by this flip, and
replay determinism holds: the same events fold to the same `state_root`.

CUSTODY LOCATION. For the documented install layout (`<base>/weft/weft.db`) the keys live
in `<base>/keys/principals/` — inside the SECRETS partition, which is excluded from every
backup and support bundle (`data_layout.EXCLUDED_FROM_BACKUP`). For an ad-hoc database
path (a temp db in a test) they live in `<db>.keys.d` beside it. A backup consequently
carries no signing key: on restore the operator re-places the whole `keys/` custody from
their own keeping, exactly as they already do for the master seed.

MIGRATION of an install whose history was signed under the derived custodian: a pid has
exactly ONE key, so handing an existing author a fresh random key would make its recorded
events unverifiable. `adopt_legacy_authors` therefore imports the DERIVED seed into
per-principal custody for exactly those authors whose recorded signature VERIFIES under
it — proven by a signature check, never assumed — and leaves every other principal without
a key (fail closed). Adopted keys are per-principal files that were originally derived
from the master seed; rotate them (`kernel.rotation`) to reach full split custody. Newly
minted principals always get fresh random keys.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import warnings
from collections.abc import Iterable

from decima.kernel.crypto import Keyring
from decima.kernel.keystore import DirectoryKeyStore, derive_seed, derived_public_key
from decima.services.data_layout import KEYS, WEFT, WEFT_DB

# The per-principal custody subdirectory inside the install's secrets partition.
PRINCIPALS = "principals"


def custody_dir(db_path: str) -> str:
    """Where the per-principal signing keys for the install owning `db_path` live.

    Standard layout (`<base>/weft/weft.db`) → `<base>/keys/principals` (the secrets
    partition: 0700, never backed up, never in a support bundle). Any other database path
    (an ad-hoc or temp db) → `<db>.keys.d` beside it. Pure path arithmetic; creates
    nothing."""
    abs_db = os.path.abspath(db_path)
    weft_dir, name = os.path.split(abs_db)
    parent, leaf = os.path.split(weft_dir)
    if name == WEFT_DB and leaf == WEFT:
        return os.path.join(parent, KEYS, PRINCIPALS)
    return abs_db + ".keys.d"


def open_custody(db_path: str) -> DirectoryKeyStore:
    """The install's custodian, creating its 0700 directory on first use. The directory
    mode is re-asserted defensively: an install provisioned before per-principal custody
    may have a `keys/` tree created under a laxer umask, and signing keys must never be
    group/other-readable."""
    path = custody_dir(db_path)
    store = DirectoryKeyStore(path)
    with contextlib.suppress(OSError):  # unusual filesystem; the store still works
        os.chmod(path, 0o700)
    return store


def install_keyring(db_path: str, *, seed: bytes | None = None) -> Keyring:
    """A Keyring in the DEFAULT production custody posture for the install at `db_path`:
    per-principal 0600 keys held by a `DirectoryKeyStore`, NOT the DEV-ONLY derived store
    (so no `UserWarning` fires on this path). `seed` is the master seed for the remaining
    non-signing derivations (the loopback pairing secret, `mint_keyed`); it derives no
    signing key here. A legacy derived-custody history is adopted on open so it keeps
    verifying — see `adopt_legacy_authors`."""
    keyring = Keyring(seed=seed, custodian=open_custody(db_path))
    adopt_legacy_authors(db_path, keyring)
    return keyring


def ensure_custody(keyring: Keyring, pids: Iterable[str]) -> list[str]:
    """Provision, ONCE, a per-principal signing key for each pid — the step
    `Keyring.mint` deliberately does not take (minting an identity confers nothing, and a
    custodian owns keys, not the Keyring). Warm start: an existing 0600 seed file is
    reused untouched, so a restart reproduces the same keys and prior signatures still
    verify. Returns the pids newly minted into custody.

    A caller that supplied its own non-directory custodian (a test, the heartbeat profile)
    is left alone — this is a no-op there, keeping `Keyring`'s library default backward
    compatible."""
    store = keyring.custodian
    if not isinstance(store, DirectoryKeyStore):
        return []
    minted: list[str] = []
    for pid in pids:
        if not store.has(pid):
            store.create(pid)  # fresh os.urandom seed, persisted 0600; never returned
            minted.append(pid)
    return minted


def _latest_signature_per_author(db_path: str) -> list[tuple[str, str, str]]:
    """`(author, event_id, sig)` for each author's MOST RECENT event — a read-only peek at
    the store, no verification and no fold (that is the caller's business). The NEWEST
    signature is the right probe: it was made with the key the author signs with NOW, so a
    rotated author (whose current key is not the derived one) is correctly not adopted."""
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT author, id, sig FROM events WHERE seq IN "
            "(SELECT MAX(seq) FROM events GROUP BY author) ORDER BY seq"
        ).fetchall()
    except sqlite3.DatabaseError:  # not a Weft store (or unreadable) — nothing to adopt
        return []
    finally:
        con.close()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def adopt_legacy_authors(db_path: str, keyring: Keyring) -> list[str]:
    """One-time custody MIGRATION for an install whose history was signed under the
    DEV-ONLY derived custodian. Returns the pids adopted (empty for a fresh install, the
    normal case).

    Why it is needed: a pid has exactly ONE key. Giving an author that already signed
    events a fresh random key would make its recorded events fail verification
    (`Weft.events` raises), i.e. the flip would break an existing install. Why it is safe:
    a derived key is only imported for an author whose OWN recorded signature verifies
    under that key — checked with the PUBLIC key first, through the keybook, so nothing
    secret is assumed. Any other author (a foreign peer, a rotated principal, a
    fresh-custody principal) is left with no key, and verification fails closed."""
    store = keyring.custodian
    if not isinstance(store, DirectoryKeyStore):
        return []
    adopted: list[str] = []
    for author, eid, sig in _latest_signature_per_author(db_path):
        if store.has(author):
            continue  # already in per-principal custody — nothing to migrate
        keyring.trust(author, derived_public_key(keyring.master, author))
        derived_signed = keyring.verify(author, eid, sig)
        # The keybook entry was a probe, not trust we want to keep: drop it either way.
        # (Adopted authors are verified from custody; unknown ones must fail closed.)
        keyring.keybook.pop(author, None)
        if derived_signed:
            store.adopt(author, derive_seed(keyring.master, author))
            adopted.append(author)
    if adopted:
        warnings.warn(
            f"migrated {len(adopted)} principal(s) from the DEV-ONLY derived custodian into "
            f"per-principal custody at {custody_dir(db_path)} (their history keeps "
            "verifying). Those keys were originally derived from the master seed — rotate "
            "them (decima.kernel.rotation) to reach full split custody.",
            stacklevel=2,
        )
    return adopted
