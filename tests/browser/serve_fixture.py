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
from typing import Any, cast

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


def _nona_case_runner(case: dict[str, Any]) -> dict[str, Any]:
    """A deterministic stand-in for the JAILED case runner the Reckoner needs.

    The Nona lane's evaluation host is an INJECTED seam whose default refuses
    (``NOT_AVAILABLE``): nothing in the API process may ever execute a candidate, and an
    unbound host must fail closed rather than run generated code in-process. Production
    binds a closure over ``decima.workers.run_worker`` plus that jail's real containment
    manifest. This launcher binds a stand-in for the same reason it can create a bounded
    Agent Cell for ``--seed-agent``: the browser lane stands in for a component that is not
    the Shell, so the Shell's surface can be driven end to end.

    What it is: honest outcomes computed from the case INPUT — an adversarial case reports
    that the jail HELD, a case with no ``x`` resolves UNKNOWN (never a pass), and a
    deterministic case reports ``x + 1``. Computing rather than echoing the expectation
    matters: an echoing runner would make every case pass by construction, which is the
    vacuous shape that makes an evaluation mean nothing.

    What it is NOT: a jail, and not evidence about the Reckoner. The browser lane qualifies
    the SURFACE — what the operator sees, and what the gated commands do — while the
    soundness of the gate itself is owned by ``tests/nona/`` over the real stages. The
    ``evaluation_result`` Cells this produces live in a throwaway temp Weft for the duration
    of one spec.
    """
    if case.get("adversarial"):
        return {"status": "FAILED", "contained": True}
    args = case.get("in") or {}
    if "x" not in args:
        return {"status": "UNKNOWN"}
    try:
        return {"status": "SUCCEEDED", "output": int(args["x"]) + 1}
    except (TypeError, ValueError):
        return {"status": "FAILED"}


# The containment the stand-in above asserts. It is the manifest of the jail it stands in
# for — declared here, in the harness, so nothing in the product ever claims a layer it did
# not engage. `reckoner.require_host_containment` refuses to record a result whose declared
# containment the host cannot deliver, and that check is the reason this constant is
# explicit rather than inferred.
_NONA_CONTAINMENT: dict[str, Any] = {
    "no_new_privs": True,
    "network_denied": True,
    "chroot": True,
    "namespaces": True,
    "matrix_version": 1,
}


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
    parser.add_argument(
        "--seed-nona",
        action="store_true",
        help=(
            "bind an evaluation host for the self-extension lane so EvaluateCandidate can "
            "run (the lane's host seam defaults to REFUSING, and nothing in the API process "
            "may execute a candidate). This binds a deterministic stand-in for the jailed "
            "runner plus the containment manifest of the jail it stands in for; it seeds NO "
            "Cells, mints no authority, and leaves every command, gate and refusal on the "
            "real product path. The browser then drives propose → evaluate → promote → "
            "rollback through visible controls."
        ),
    )
    args = parser.parse_args(argv)

    seed = _parse_seed(args.seed)
    # secure_cookie=False: the browser talks plain HTTP to loopback in the rig (see module
    # docstring). Everything else is the shipped composition, unchanged.
    backend, identity = build_application(args.db, seed=seed, secure_cookie=False)

    if args.seed_nona:
        from decima.services.api import nona_service

        nona_service.bind_evaluation_host(
            nona_service.EvaluationHost(run=_nona_case_runner, containment=dict(_NONA_CONTAINMENT))
        )
        print("DECIMA_SEED_NONA=1", flush=True)

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
