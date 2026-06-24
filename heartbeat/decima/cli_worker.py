"""CLI-worker integration seam.

The handler here runs a real subprocess, but keeps the Heartbeat profile safe and
deterministic: no shell, fixed argv, short timeout, captured output. Production
sandboxing slots around the `subprocess.run(...)` call below: landlock/seccomp on
Linux, seatbelt on macOS, or a microVM/container runner for stronger isolation.
"""
import subprocess
import sys

from decima.executor import ExecError


DEFAULT_TIMEOUT = 5

_SCRIPT = (
    "import sys\n"
    "payload = sys.argv[1] if len(sys.argv) > 1 else ''\n"
    "print('codex-shim reviewed: ' + payload)\n"
)


def make_handler(argv: list[str] | None = None, timeout: int = DEFAULT_TIMEOUT):
    """Return an executor handler for a safe deterministic CLI command.

    The handler accepts the normal Decima text payload (`args["text"]`) and passes
    it as a single argv element. That proves real process execution without shell
    interpolation or ambient command choice.
    """
    base_argv = list(argv or [sys.executable, "-c", _SCRIPT])

    def handler(impl, args: dict) -> dict:
        payload = str(args.get("text", ""))
        try:
            proc = subprocess.run(
                [*base_argv, payload],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecError(f"cli worker timed out after {timeout}s") from exc

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if proc.returncode != 0:
            raise ExecError(f"cli worker exited {proc.returncode}: {stderr or stdout}")

        return {
            "out": stdout,
            "stdout": stdout,
            "stderr": stderr,
            "code": proc.returncode,
            "provider_ref": base_argv[0],
            "sandbox": {
                "mode": "subprocess-allowlist",
                "future": "wrap subprocess.run with landlock/seccomp or a microVM",
            },
        }

    return handler


def integrate(k, name: str = "codex-shim") -> str:
    """Register the CLI worker as a capability using Kernel's public API."""
    return k.integrate_tool(name, make_handler(), caveats={
        "budget": 10,
        "effect_class": "READ",
        "sandbox": "subprocess-allowlist",
    })
