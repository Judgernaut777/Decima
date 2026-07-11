"""The on-disk data layout of a local Decima install (handoff §12-13).

One base directory holds every durable byte a running install owns, partitioned by
what each kind of data IS with respect to Law 5:

    <base>/
      weft/          the Weft — the SOLE canonical store (weft.db).
      artifacts/     content-addressed blobs referenced from the Weft; filename == digest.
      checkpoints/   signed integrity commitments over fold frontiers (disposable evidence).
      config/        PUBLIC configuration only (budgets, identity fingerprint). NO secrets.
      projections/   DISPOSABLE read-models — rebuildable from the fold; never canonical.
      logs/          operational logs — disposable; only redacted tails ever leave the box.
      keys/          SECRETS (the master seed). NEVER copied into a backup or support bundle.

A backup captures exactly {weft, artifacts, checkpoints, config}: the canonical log
plus the durable byte-artifacts attached to it. Projections and logs are rebuildable
/ disposable and are excluded; keys are secret and are excluded — a plaintext key in a
backup would be a second, unprotected place authority could leak from.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from decima.kernel.hashing import blob_id

# Subdirectory names.
WEFT = "weft"
ARTIFACTS = "artifacts"
CHECKPOINTS = "checkpoints"
CONFIG = "config"
PROJECTIONS = "projections"
LOGS = "logs"
KEYS = "keys"

WEFT_DB = "weft.db"
MASTER_SEED = "master.seed"

# What a backup captures. Ordered, so a manifest lists file categories deterministically.
BACKUP_DIRS: tuple[str, ...] = (ARTIFACTS, CHECKPOINTS, CONFIG)
# What NEVER enters a backup or a support bundle (rebuildable, disposable, or secret).
EXCLUDED_FROM_BACKUP: tuple[str, ...] = (PROJECTIONS, LOGS, KEYS)
# Every subdirectory a fully provisioned base owns.
ALL_DIRS: tuple[str, ...] = (
    WEFT, ARTIFACTS, CHECKPOINTS, CONFIG, PROJECTIONS, LOGS, KEYS,
)


def file_digest(path: str) -> str:
    """The content-address of a file's bytes — the same domain-separated digest the
    kernel uses for any blob. Two identical files hash identically; a single flipped
    byte changes the digest, which is how `backup_verify`/`doctor` detect tampering."""
    with open(path, "rb") as fh:
        return blob_id(fh.read())


@dataclass(frozen=True)
class DataDir:
    """A typed handle over a base data directory. Pure path arithmetic + directory
    creation; it reads and writes NO canonical state itself."""

    base: str

    def path(self, *parts: str) -> str:
        return os.path.join(self.base, *parts)

    @property
    def weft_db(self) -> str:
        return self.path(WEFT, WEFT_DB)

    @property
    def master_seed(self) -> str:
        return self.path(KEYS, MASTER_SEED)

    def subdir(self, name: str) -> str:
        return self.path(name)

    def ensure(self) -> DataDir:
        """Create the full layout (idempotent). `keys/` is created 0700 — a private
        directory for the master seed; the rest default to the umask."""
        os.makedirs(self.base, exist_ok=True)
        for name in ALL_DIRS:
            mode = 0o700 if name == KEYS else 0o777
            os.makedirs(self.path(name), mode=mode, exist_ok=True)
        return self

    def list_files(self, name: str) -> list[str]:
        """Sorted regular-file names under a subdirectory (deterministic order)."""
        d = self.path(name)
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
