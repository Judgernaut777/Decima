"""First-run provisioning for a local Decima install (handoff §12-13, deploy flow).

`first_run` stands up a usable, fully LOCAL install with NO network: it creates the
data layout, mints the box's root identity (custodying the master seed 0600 under
`keys/`, the ONE secret — never in config, never in a backup), initializes an empty
canonical Weft, and writes PUBLIC defaults (budgets, an identity fingerprint) as config
files. It mints NO authority: a budget/config default is data, not a capability grant,
so nothing here confers power (Law 2 / invariant 3). The master seed is generated with
`os.urandom` — that is a private key, never recorded Weft content, so the determinism
rule (ints-not-floats, no unseeded random in the Log) is untouched; pass `seed=` for a
reproducible install.

Runnable as `python3 -m decima.services.provision <base>` from `deploy/install.sh`.
"""
from __future__ import annotations

import json
import os
import sys

from decima.kernel.crypto import Keyring
from decima.kernel.weft import Weft
from decima.services.data_layout import CONFIG, DataDir

# Public, conservative defaults. Ints only (Weft-content-grade), no wall-clock.
DEFAULT_TOKEN_BUDGET = 100_000
DEFAULT_MONETARY_BUDGET = 0  # microcents; a fresh box spends no money until configured


def first_run(
    base: str,
    *,
    seed: bytes | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    monetary_budget: int = DEFAULT_MONETARY_BUDGET,
    root_name: str = "decima",
) -> dict:
    """Provision a fresh install under `base` (idempotent on the layout; refuses to
    clobber an existing seed). Returns a public summary — NEVER the seed.

    No network is ever touched. The only secret written is the master seed, 0600 under
    `keys/`; everything else (budgets, identity fingerprint) is public config."""
    dd = DataDir(base).ensure()

    if os.path.exists(dd.master_seed):
        raise FileExistsError(
            f"an identity already exists at {dd.master_seed} — refusing to overwrite it")
    seed = seed if seed is not None else os.urandom(32)
    if len(seed) != 32:
        raise ValueError("master seed must be 32 bytes")
    fd = os.open(dd.master_seed, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, seed)
    finally:
        os.close(fd)

    keyring = Keyring(seed=seed)
    root = keyring.mint(root_name, "root")

    # Initialize the empty canonical Weft (genesis happens on the first real assert).
    Weft(dd.weft_db, keyring)

    # PUBLIC config: default budgets (ints) + an identity fingerprint (public key only).
    budgets = {
        "principal": root.id,
        "token_budget": int(token_budget),
        "monetary_budget_microcents": int(monetary_budget),
    }
    identity = {
        "principal": root.id,
        "name": root_name,
        "public_key": keyring.public_key(root.id),
    }
    _write_config(dd, "budgets.json", budgets)
    _write_config(dd, "identity.json", identity)

    return {
        "base": base,
        "principal": root.id,
        "public_key": identity["public_key"],
        "weft_db": dd.weft_db,
        "token_budget": budgets["token_budget"],
        "monetary_budget_microcents": budgets["monetary_budget_microcents"],
        "network": "none",
    }


def _write_config(dd: DataDir, name: str, payload: dict) -> None:
    path = dd.path(CONFIG, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python3 -m decima.services.provision <base-dir>", file=sys.stderr)
        return 2
    try:
        summary = first_run(argv[0])
    except (FileExistsError, ValueError) as exc:
        print(f"first-run refused: {exc}", file=sys.stderr)
        return 1
    print(f"provisioned decima at {summary['base']}")
    print(f"  principal:  {summary['principal']}")
    print(f"  public key: {summary['public_key']}")
    print(f"  weft:       {summary['weft_db']}")
    print(f"  budgets:    tokens={summary['token_budget']} "
          f"monetary_microcents={summary['monetary_budget_microcents']}")
    print("  network:    none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
