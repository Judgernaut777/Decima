#!/usr/bin/env python3
"""Launch the Decima heartbeat shell.

    python3 run.py            # warm start (reuses weft.db)
    python3 run.py --fresh    # start from genesis
    python3 run.py --mcp-serve  # headless: serve Decima as an MCP stdio server
    python3 run.py --api-serve  # headless: serve the gated HTTP API

GO-LIVE (Phase 2): if ANTHROPIC_API_KEY (or any DECIMA_SECRET_<NAME>) is present
at boot, `golive.boot` announces a redacted intake into the SecretsBroker and —
IF a human has already approved an api.anthropic.com egress grant — binds the
ModelBrain to it (every live call still passes the wire gate). With no key in
the environment, boot returns nothing and behavior is identical to before.

ALWAYS-ON (Batch A · beat driver): if the durable run-loop has EVER beaten (a
`loop_checkpoint` folds from the Weft), boot RESUMES it — `daemon.resume`
continues from that durable checkpoint through the current logical frontier, so
a restart CONTINUES the heartbeat: no beat re-fired (the sweep starts strictly
after the checkpoint), no beat skipped (it runs through the frontier). On a
world whose loop has never beaten — including every `--fresh` boot —
`resume_loop` returns [] and boot behavior is byte-identical to before.
"""


def resume_loop(k):
    """Boot-time durable-loop resume (idempotent, additive, fail closed).

    Continue the run-loop from its Weft-folded `loop_checkpoint` through the
    current logical frontier via `daemon.resume` — resuming is CONTINUING,
    never starting: if the loop has never beaten (`checkpoint == NEVER`) this
    does NOTHING, so a keyless/fresh boot is unchanged. The frontier is the
    Weft's own lamport (a logical int — no wall-clock enters the loop). Confers
    no authority: `daemon.resume` drives `reactor.tick`, where every fired lane
    passes its own gates. Returns display lines; [] when there was nothing to
    resume."""
    from decima import daemon
    cp = daemon.checkpoint(k)
    if cp == daemon.NEVER:
        return []                        # never beaten — nothing to CONTINUE
    frontier = int(k.weft.lamport)
    if frontier <= cp:
        return [f"run-loop: checkpoint e{cp} is already current — nothing to resume"]
    out = daemon.resume(k, frontier)
    return [f"run-loop resumed: checkpoint e{out['resumed_from']} → e{out['to']} · "
            f"ticked {len(out['ticked'])} frontier(s) · fired {out['fired']}"
            + (" · quiet" if out["quiet"] else "")]


def main():
    import sys
    from decima.shell import Shell
    from decima import golive

    # A serve flag turns this process into a headless server over the SAME booted
    # kernel instead of the interactive Shell. Boot diagnostics then go to STDERR:
    # `--mcp-serve` speaks JSON-RPC on stdout, which must stay uncorrupted, and a
    # clean stdout is harmless for `--api-serve` too. Serving weakens no gate — each
    # launcher dispatches only through its proven, gated handler (serve_stdio /
    # api.handle_request).
    serving = "--mcp-serve" in sys.argv or "--api-serve" in sys.argv
    diag = sys.stderr if serving else sys.stdout

    sh = Shell(fresh="--fresh" in sys.argv)
    for line in golive.boot(sh.k):      # [] when no provider secret is present
        print("   " + line, file=diag)
    for line in resume_loop(sh.k):      # [] when the loop has never beaten
        print("   " + line, file=diag)

    if "--mcp-serve" in sys.argv:
        from decima import serve_mcp     # MCP stdio server over the booted kernel
        serve_mcp.serve(sh.k)            # default-deny consumer; blocks until EOF
    elif "--api-serve" in sys.argv:
        from decima import serve_api      # HTTP API host over the booted kernel
        serve_api.main(k=sh.k)            # blocks in serve_forever
    else:
        sh.cmdloop()


if __name__ == "__main__":
    main()
