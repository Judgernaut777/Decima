#!/usr/bin/env python3
"""Launch the Decima heartbeat shell.

    python3 run.py            # warm start (reuses weft.db)
    python3 run.py --fresh    # start from genesis

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

    sh = Shell(fresh="--fresh" in sys.argv)
    for line in golive.boot(sh.k):      # [] when no provider secret is present
        print("   " + line)
    for line in resume_loop(sh.k):      # [] when the loop has never beaten
        print("   " + line)
    sh.cmdloop()


if __name__ == "__main__":
    main()
