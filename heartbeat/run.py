#!/usr/bin/env python3
"""Launch the Decima heartbeat shell.

    python3 run.py            # warm start (reuses weft.db)
    python3 run.py --fresh    # start from genesis

GO-LIVE (Phase 2): if ANTHROPIC_API_KEY (or any DECIMA_SECRET_<NAME>) is present
at boot, `golive.boot` announces a redacted intake into the SecretsBroker and —
IF a human has already approved an api.anthropic.com egress grant — binds the
ModelBrain to it (every live call still passes the wire gate). With no key in
the environment, boot returns nothing and behavior is identical to before.
"""


def main():
    import sys
    from decima.shell import Shell
    from decima import golive

    sh = Shell(fresh="--fresh" in sys.argv)
    for line in golive.boot(sh.k):      # [] when no provider secret is present
        print("   " + line)
    sh.cmdloop()


if __name__ == "__main__":
    main()
