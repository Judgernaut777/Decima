"""The local app identity — a generated, self-certifying principal for the API host.

The backend runs as ONE local application on the user's machine. It has an identity
of its own (the author of the events it appends on the user's behalf) and it holds a
LOCAL pairing secret a browser session must present to authenticate (there is no
remote account — this is a loopback daemon). Minting the identity confers NO authority
(Law 2 / invariant 3): a principal is just a verifiable signer; what it may DO is
decided later, per command, through the authorization/approval path.

The identity is derived deterministically from the host Keyring's master seed (crypto
seam), so a warm restart reproduces the same app + human principals — the Weft its
prior run signed still verifies.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from decima.kernel.crypto import Keyring


@dataclass(frozen=True)
class AppIdentity:
    """The principals the API signs and authorizes under.

    * ``app`` — the local application principal (author of events it records for the
      user, e.g. importing an artifact, enqueuing an approval).
    * ``human`` — the operator principal (approver of record for gated effects; the
      Shell user who holds broad LOCAL authority).
    * ``pairing_secret`` — the loopback pairing token a browser session presents to
      log in and to re-authenticate for a high-risk approval. Deterministic from the
      master seed so a restart keeps existing paired clients valid; it is a SECRET,
      never written to the Weft.
    """

    app: str
    human: str
    pairing_secret: str


def generate_identity(
    keyring: Keyring, *, app_name: str = "decima-local-app", human_name: str = "operator"
) -> AppIdentity:
    """Mint (or reproduce) the local app + human principals on ``keyring`` and derive
    the pairing secret from its master seed. Idempotent for a given seed + names."""
    app = keyring.mint(app_name, "agent")
    human = keyring.mint(human_name, "human")
    secret = hashlib.blake2b(
        keyring.master + app_name.encode(), digest_size=32, person=b"decima:pair"
    ).hexdigest()
    return AppIdentity(app=app.id, human=human.id, pairing_secret=secret)
