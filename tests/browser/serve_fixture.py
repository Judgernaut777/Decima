"""Real loopback launcher for the browser-qualification harness (WS1).

This starts the REAL Decima backend + trusted Shell over a REAL temporary Weft on an
ephemeral (or fixed) loopback port, exactly as an operator would run the daily driver —
no in-process shortcut, no injected state. Playwright drives the rendered UI against it.

It is deliberately a thin composition of the SAME product seams the shipped entrypoint
uses (``decima.services.api.server.build_application`` + ``decima.shell.serve.build_shell``
+ ``make_http_server``); it adds NO authority and rewrites no command. Two concessions are
made ONLY because the browser talks plain HTTP to loopback in the test rig:

  * ``secure_cookie=False`` — a ``Secure`` cookie is not returned over http://127.0.0.1 in
    every browser build, so the session cookie is minted without the ``Secure`` flag for
    the test origin. This is the same concession the in-process shell test harness makes
    (``tests/shell/conftest.py``). It does NOT touch the Weft or any authority path.
  * a FIXED keyring seed makes the pairing secret reproducible so (a) global-setup can log
    the browser in and (b) a RESTART of this launcher over the SAME db re-derives the SAME
    identity — which is exactly how the durability-across-restart assertion is exercised.

The launcher prints two machine-readable lines to stdout and then serves forever:

    DECIMA_SHELL_PAIRING=<secret>
    DECIMA_SHELL_READY=http://127.0.0.1:<port>/

Reading ``DECIMA_SHELL_READY`` means the socket is accepting connections. On SIGTERM/SIGINT
it shuts the server down cleanly so the harness can restart it on the same db.

Usage:
    python3 -m tests.browser.serve_fixture --db /path/weft.db --port 8991 --seed 00..00
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from typing import cast

from decima.projections.agents import AgentsProjection
from decima.services.api.server import build_application
from decima.shell.serve import build_shell, make_loopback_server


def _parse_seed(raw: str) -> bytes:
    """A 32-byte keyring seed from a hex string (default: all-zero, like the unit tests)."""
    if not raw:
        return bytes(32)
    seed = bytes.fromhex(raw)
    if len(seed) != 32:
        raise ValueError(f"seed must be 32 bytes (64 hex chars), got {len(seed)}")
    return seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the real Decima Shell for browser tests.")
    parser.add_argument("--db", required=True, help="path to the Weft db (persists on restart)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 picks an ephemeral port")
    parser.add_argument("--seed", default="00" * 32, help="32-byte keyring seed as hex")
    parser.add_argument(
        "--seed-agent",
        action="store_true",
        help=(
            "create ONE bounded agent as a precondition, via the canonical kernel path "
            "(cells.create_agent asserts an Agent Cell on the Weft — NOT a projection/SQLite "
            "injection). The Shell itself never spawns agents; the runtime does. This flag "
            "lets the harness stand in for the runtime so the browser can then drive the "
            "gated terminate/revoke -> approval flow through visible controls."
        ),
    )
    args = parser.parse_args(argv)

    seed = _parse_seed(args.seed)
    # secure_cookie=False: the browser talks plain HTTP to loopback in the rig (see module
    # docstring). Everything else is the shipped composition, unchanged.
    backend, identity = build_application(args.db, seed=seed, secure_cookie=False)

    if args.seed_agent:
        from decima.runtime import cells

        # Assert a bounded Agent Cell through the kernel, authored by the app principal —
        # exactly the canonical mutation the runtime performs. Only assert it once (a warm
        # restart over the same db already has it), so restarts stay idempotent.
        agents_now = cast(AgentsProjection, backend.driver.get("agents")).agents()
        if not agents_now:
            agent_id = cells.create_agent(
                backend.weft,
                identity.app,
                objective="bounded fixture agent (harness precondition)",
                principal=identity.app,
                token_budget=1000,
                monetary_budget=5,
                deadline=100,
            )
            backend.driver.update()
        else:
            agent_id = agents_now[0].id
        print(f"DECIMA_SEED_AGENT={agent_id}", flush=True)

    shell = build_shell(backend)
    # Use the shipped Shell loopback server (single-threaded; see make_loopback_server), so
    # the browser qualifies the REAL daily-driver server path, not a bespoke one.
    server = make_loopback_server(shell, host=args.host, port=args.port)
    bound_port = server.server_address[1]

    # Machine-readable handshake for the Playwright global-setup.
    print(f"DECIMA_SHELL_PAIRING={identity.pairing_secret}", flush=True)
    print(f"DECIMA_SHELL_READY=http://{args.host}:{bound_port}/", flush=True)

    stop = threading.Event()

    def _shutdown(_signum, _frame):
        # server.shutdown() must run off the serving thread.
        threading.Thread(target=server.shutdown, daemon=True).start()
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
