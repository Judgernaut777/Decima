"""Per-user identity and credentials for the local daemon (T3.2).

The API stops being a single-operator surface here: a REAL user has a name, a credential,
and a Decima principal. Three commitments shape this module.

1. IDENTITY IS A PRINCIPAL, NOT A ROLE. A user's principal is minted on the host Keyring
   exactly the way the app/operator principals are (``identity.generate_identity``), so it
   is the domain-separated BLAKE3-256 digest of a stable name and it reproduces across
   restarts — the Weft a prior run signed still verifies. Minting confers NO authority
   (Law 2 / invariant 3): a principal is a verifiable signer, and what it may DO is
   decided later, per request, per command. There is deliberately NO admin/superuser
   record and no field that could grant one; a user's authority is the capability envelope
   reachable from that user's OWN store (see ``tenancy``).

2. CREDENTIALS ARE HASHED, SALTED, AND NEVER PLAINTEXT. Each user gets a fresh 16-byte
   ``secrets`` salt and a ``hashlib.scrypt`` (memory-hard, stdlib) derivation; only the
   salt and the derived hash are stored, and comparison is ``hmac.compare_digest``.
   Refusals are indistinguishable — unknown user, wrong password, and disabled user all
   spend the SAME scrypt work and return the same answer, so this is not a user
   enumeration oracle.

3. NOTHING CREDENTIAL-SHAPED TOUCHES THE WEFT. The directory is a ``0600`` file inside a
   ``0700`` directory beside the store (the discipline ``_wsgi_util.write_pairing_secret``
   already uses), written via a temp file + ``os.replace`` so a crash never leaves a
   half-written directory. Because the per-user salt is random, keeping it OUT of recorded
   content is what preserves invariant 6 (no unseeded random in signed/hashed content) —
   and no id, hash, or canonical encoding anywhere in the protocol changes.

PROVISIONING IS A HOST-SIDE ACT. ``provision_user`` is called by someone with filesystem
access to the store, never by an HTTP request: an endpoint that could mint users or reset
other people's passwords would be precisely the ambient authority Law 2 forbids. The only
self-service mutation the API exposes is a user rotating their OWN password.

This module is stdlib-only (``hashlib``/``hmac``/``secrets``/``json``/``os``/``re``) plus
``decima.kernel.crypto``; it lives in ``services``, outside the kernel TCB, so the
import-boundary guard's ALLOWED_THIRD_PARTY set is untouched.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass

from decima.kernel.crypto import Keyring

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "USERS_FILE",
    "UserDirectory",
    "UserError",
    "UserRecord",
    "principal_name",
    "provision_user",
    "users_path",
]

USERS_FILE = "users.json"
DIRECTORY_VERSION = 1

# Conservative, boring username grammar: lowercase, 2..32 chars, no path separators, no
# whitespace, no shell-significant characters. A username becomes a principal NAME and
# (via its principal id) a filename, so the safe set is chosen at the front door.
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")

MIN_PASSWORD_LENGTH = 12

# scrypt parameters: ~16 MiB and ~50 ms per derivation on a normal laptop — enough to make
# offline cracking of a leaked directory expensive, cheap enough for an interactive login.
# They are STORED PER RECORD so they can be raised later without invalidating old hashes.
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_SALT_BYTES = 16

# A fixed decoy salt: an unknown or disabled username still pays for one derivation, so a
# caller cannot learn who exists by timing the refusal.
_DECOY_SALT = bytes(_SALT_BYTES)


class UserError(Exception):
    """A fail-closed refusal from the user directory (bad name, weak password, duplicate
    user, unreadable or inconsistent directory file)."""


def users_path(db_path: str) -> str:
    """The user directory file beside the Weft at ``db_path``."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", USERS_FILE)


def principal_name(username: str) -> str:
    """The Keyring principal NAME for a user. Namespaced with ``user:`` so a username can
    never collide with the daemon's own principals (``decima-local-app``/``operator``)."""
    return f"user:{username}"


def _validate_username(username: object) -> str:
    if not isinstance(username, str) or not _USERNAME_RE.match(username):
        raise UserError(
            "username must be 2-32 characters of lowercase letters, digits, '.', '_' or '-' "
            "and start with a letter or digit"
        )
    return username


def _validate_password(password: object) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return password


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    ).hex()


@dataclass(frozen=True)
class UserRecord:
    """One user: a name, the Decima ``principal`` they act as, and the credential
    VERIFIER (salt + scrypt parameters + derived hash). There is no field here that grants
    authority — no role, no group, no 'is_admin'. ``disabled`` only takes authentication
    away."""

    username: str
    principal: str
    salt: str
    hash: str
    n: int = SCRYPT_N
    r: int = SCRYPT_R
    p: int = SCRYPT_P
    disabled: bool = False

    def as_public(self) -> dict:
        """The record MINUS every piece of credential material — the only shape safe to
        hand to a caller or a log."""
        return {
            "username": self.username,
            "principal": self.principal,
            "disabled": self.disabled,
        }

    def as_stored(self) -> dict:
        return {
            "username": self.username,
            "principal": self.principal,
            "salt": self.salt,
            "hash": self.hash,
            "n": self.n,
            "r": self.r,
            "p": self.p,
            "disabled": self.disabled,
        }


class UserDirectory:
    """The provisioned users of one install, persisted beside the Weft.

    Construction LOADS the file (an absent file is an empty directory, not an error) and
    re-mints every user's principal on ``keyring`` so the daemon can sign as them after a
    restart. If a stored principal disagrees with what the keyring mints for that
    username, construction FAILS CLOSED: a tampered or mismatched directory must never be
    used to hand somebody a store they cannot prove title to.
    """

    def __init__(self, path: str, keyring: Keyring) -> None:
        self.path = path
        self.keyring = keyring
        self._records: dict[str, UserRecord] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            raw = {"version": DIRECTORY_VERSION, "users": []}
        except (OSError, ValueError) as exc:
            raise UserError(f"unreadable user directory {self.path!r}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("users", []), list):
            raise UserError(f"malformed user directory {self.path!r}")
        for entry in raw.get("users", []):
            if not isinstance(entry, dict):
                raise UserError(f"malformed user entry in {self.path!r}")
            try:
                record = UserRecord(
                    username=_validate_username(entry["username"]),
                    principal=str(entry["principal"]),
                    salt=str(entry["salt"]),
                    hash=str(entry["hash"]),
                    n=int(entry.get("n", SCRYPT_N)),
                    r=int(entry.get("r", SCRYPT_R)),
                    p=int(entry.get("p", SCRYPT_P)),
                    disabled=bool(entry.get("disabled", False)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise UserError(f"malformed user entry in {self.path!r}: {exc}") from exc
            self._records[record.username] = record
        self._adopt_principals()

    def _adopt_principals(self) -> None:
        """Register every user's principal on the keyring (so the daemon can sign for them
        this run) and CHECK the stored id against it. A mismatch means the file was edited
        or belongs to another install — refuse rather than guess."""
        for record in self._records.values():
            minted = self.keyring.mint(principal_name(record.username), "human")
            if not hmac.compare_digest(minted.id, record.principal):
                raise UserError(
                    f"principal mismatch for user {record.username!r}: directory says "
                    f"{record.principal!r}, this keyring mints {minted.id!r}"
                )

    def _save(self) -> None:
        """Persist the directory as a ``0600`` file in a ``0700`` directory, atomically:
        write a fresh temp file created at 0600 (never briefly world-readable) then
        ``os.replace`` it over the target, so a crash leaves the previous directory intact
        rather than a truncated one."""
        payload = {
            "version": DIRECTORY_VERSION,
            "users": [self._records[name].as_stored() for name in sorted(self._records)],
        }
        parent = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(parent, mode=0o700, exist_ok=True)
        temp = self.path + ".tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)

    # -- queries -----------------------------------------------------------
    def count(self) -> int:
        return len(self._records)

    def usernames(self) -> list[str]:
        return sorted(self._records)

    def public_records(self) -> list[dict]:
        """Every user MINUS credential material, deterministically ordered."""
        return [self._records[name].as_public() for name in sorted(self._records)]

    def principal_of(self, username: str | None) -> str | None:
        """The principal for an ENABLED user, else None. A disabled user resolves to
        nothing, so a live session belonging to one stops resolving to a store."""
        record = self._records.get(username or "")
        if record is None or record.disabled:
            return None
        return record.principal

    def username_of(self, principal: str) -> str | None:
        for name in sorted(self._records):
            record = self._records[name]
            if not record.disabled and hmac.compare_digest(record.principal, principal):
                return name
        return None

    def principals(self) -> frozenset[str]:
        return frozenset(r.principal for r in self._records.values() if not r.disabled)

    # -- host-side mutations (never reachable from a route) ----------------
    def create(self, username: str, password: str) -> UserRecord:
        """Provision a user: validate the name and password strength, mint the principal,
        derive a salted hash, persist. Refuses a duplicate rather than overwriting — an
        overwrite would silently re-point an existing principal's credential."""
        username = _validate_username(username)
        _validate_password(password)
        if username in self._records:
            raise UserError(f"user {username!r} already exists")
        salt = secrets.token_bytes(_SALT_BYTES)
        principal = self.keyring.mint(principal_name(username), "human").id
        record = UserRecord(
            username=username,
            principal=principal,
            salt=salt.hex(),
            hash=_derive(password, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P),
        )
        self._records[username] = record
        self._save()
        return record

    def set_password(self, username: str, new_password: str) -> UserRecord:
        """Rotate a user's password with a FRESH salt (so the new verifier shares nothing
        with the old one). The caller is responsible for having proved the right to do
        this — the API only calls it for the session's OWN user, after re-verifying the
        current password."""
        record = self._records.get(username)
        if record is None:
            raise UserError(f"no such user {username!r}")
        _validate_password(new_password)
        salt = secrets.token_bytes(_SALT_BYTES)
        rotated = UserRecord(
            username=record.username,
            principal=record.principal,
            salt=salt.hex(),
            hash=_derive(new_password, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P),
            disabled=record.disabled,
        )
        self._records[username] = rotated
        self._save()
        return rotated

    def set_disabled(self, username: str, disabled: bool = True) -> UserRecord:
        """Take authentication away from (or give it back to) a user. This destroys
        nothing: their store and their signed events remain, and their principal keeps
        verifying — being unable to log in is not being erased."""
        record = self._records.get(username)
        if record is None:
            raise UserError(f"no such user {username!r}")
        updated = UserRecord(
            username=record.username,
            principal=record.principal,
            salt=record.salt,
            hash=record.hash,
            n=record.n,
            r=record.r,
            p=record.p,
            disabled=bool(disabled),
        )
        self._records[username] = updated
        self._save()
        return updated

    # -- authentication ----------------------------------------------------
    def verify_password(self, username: str | None, password: str | None) -> bool:
        """Constant-time-compared credential check. An unknown or disabled user spends the
        SAME scrypt work against a decoy salt and returns False, so the answer leaks
        nothing about who exists. Never raises on bad input."""
        record = self._records.get(username or "")
        candidate_password = password or ""
        if record is None or record.disabled:
            _derive(candidate_password, _DECOY_SALT, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
            return False
        try:
            salt = bytes.fromhex(record.salt)
        except ValueError:  # a corrupted salt authenticates nobody
            return False
        candidate = _derive(candidate_password, salt, n=record.n, r=record.r, p=record.p)
        return hmac.compare_digest(candidate, record.hash)

    def authenticate(self, username: str | None, password: str | None) -> UserRecord | None:
        """The record for a user whose password checks out, else None (one refusal for
        every cause)."""
        if not self.verify_password(username, password):
            return None
        return self._records.get(username or "")


def provision_user(db_path: str, keyring: Keyring, username: str, password: str) -> UserRecord:
    """Host-side provisioning: add a user to the directory beside the Weft at ``db_path``.

    Deliberately a FUNCTION, not an endpoint. Whoever runs this already has filesystem
    access to the store; granting the same power over HTTP would create an ambient admin
    authority (Law 2). A ``decima users add`` console command is the natural front end and
    is left for the release that can also update the packaging metadata the drift guard
    reconciles.
    """
    return UserDirectory(users_path(db_path), keyring).create(username, password)
