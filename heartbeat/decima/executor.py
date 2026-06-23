"""Executor — turns an authorized INVOKE into a real effect, returning a result.

Effects are deliberately small and safe for the heartbeat. `shell` is an
allowlist with no shell interpolation. Real sandboxing (landlock / bubblewrap /
seatbelt) is the production seam; the contract here — (effect, args) -> result —
does not change when that arrives.
"""
import subprocess

_SHELL_ALLOWLIST = {
    "date": ["date", "+%Y-%m-%d %H:%M:%S"],
    "uname": ["uname", "-sm"],
    "whoami": ["whoami"],
}

_TRANSFORMS = {
    "upper": lambda s: s.upper(),
    "lower": lambda s: s.lower(),
    "reverse": lambda s: s[::-1],
    "wc": lambda s: str(len(s.split())),
}


class ExecError(Exception):
    pass


def execute(effect: str, impl, args: dict) -> dict:
    if effect == "echo":
        return {"out": str(args.get("text", ""))}

    if effect == "transform":
        name = (impl or {}).get("fn")
        fn = _TRANSFORMS.get(name)
        if not fn:
            raise ExecError(f"unknown transform {name!r}")
        return {"out": fn(str(args.get("text", "")))}

    if effect == "shell":
        cmd_key = args.get("cmd")
        argv = _SHELL_ALLOWLIST.get(cmd_key)
        if not argv:
            raise ExecError(f"shell command not on allowlist: {cmd_key!r}")
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        return {"out": proc.stdout.strip(), "code": proc.returncode}

    if effect == "forge":
        # The bootstrap effect is handled by the Reckoner (Nona), not here.
        raise ExecError("forge is realized by the Reckoner, not the executor")

    raise ExecError(f"unknown effect {effect!r}")
