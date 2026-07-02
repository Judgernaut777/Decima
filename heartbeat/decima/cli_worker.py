"""CLI-worker integration seam.

The handler runs a real subprocess, but ONLY through `decima.isolation.spawn_worker`
— the mandatory isolation boundary (resource rlimits, no_new_privs via prctl,
scrubbed minimal env, cwd jail into a scratch dir, closed fds; landlock/seccomp
attempted where the kernel offers them, honestly reported). This module holds NO
raw spawn capability of its own: it never imports a spawn-capable module, and
`isolation.assert_no_raw_spawn` re-verifies that at import time — re-adding a raw
spawn path makes this module refuse to load. The worker's honest confinement
manifest rides the result into the execution receipt (provenance on the Weft).
"""
import sys

from decima import isolation
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
    it as a single argv element. That proves real, CONFINED process execution
    without shell interpolation or ambient command choice: the spawn goes through
    the isolation seam, never a raw subprocess.
    """
    base_argv = list(argv or [sys.executable, "-c", _SCRIPT])

    def handler(impl, args: dict) -> dict:
        payload = str(args.get("text", ""))
        try:
            res = isolation.spawn_worker([*base_argv, payload], timeout=timeout)
        except isolation.WorkerTimeout as exc:
            raise ExecError(f"cli worker timed out after {timeout}s") from exc
        except isolation.IsolationError as exc:
            raise ExecError(f"cli worker refused by isolation seam: {exc}") from exc

        stdout = res["stdout"].strip()
        stderr = res["stderr"].strip()
        if res["code"] != 0:
            raise ExecError(f"cli worker exited {res['code']}: {stderr or stdout}")

        return {
            "out": stdout,
            "stdout": stdout,
            "stderr": stderr,
            "code": res["code"],
            "provider_ref": base_argv[0],
            "sandbox": {
                "mode": "isolation-seam",
                # the HONEST layer report, verified in-child — provenance for
                # the execution record; never claims a layer that did not engage.
                "manifest": res["manifest"],
            },
        }

    return handler


def integrate(k, name: str = "codex-shim") -> str:
    """Register the CLI worker as a capability using Kernel's public API."""
    return k.integrate_tool(name, make_handler(), caveats={
        "budget": 10,
        "effect_class": "READ",
        "sandbox": "isolation-seam",
    })


# The isolation seam is MANDATORY: this module must hold no raw spawn path of its
# own. Verified at import time — re-adding one makes the module refuse to load.
isolation.assert_no_raw_spawn(sys.modules[__name__])
