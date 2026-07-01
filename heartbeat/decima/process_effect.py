"""Real subprocess `process` effect — wrap an ACTUAL local CLI tool as a gated capability.

This is the REAL, generalized form of the CLI-worker seam (`cli_worker.py` is a fixed
stub): it lets an operator wrap ANY local CLI tool/agent as a Decima capability in ONE
call, behind the SAME spine every effect obeys. It runs the real engine over pure stdlib
`subprocess` (zero pip deps) — real process execution, still pure-stdlib.

POLICY (fail-closed, this is load-bearing):
  - Runs ONLY an operator-ALLOWLISTED argv. The `spec` fixes the exact program
    (`argv[0]`) and the fixed leading args; a caller's `args` can only fill DECLARED
    slots, and each filled token must be a member of that slot's explicit allowlist.
    Anything not on the allowlist — an undeclared slot, a non-string token, a value not
    in the allowlist (any shell metacharacter, path, or arbitrary command) — is REFUSED
    (ExecError → FAILED). A caller can NEVER inject an arbitrary command.
  - NEVER `shell=True`, EVER. `subprocess.run` takes a list argv, so there is no shell to
    interpolate — no metacharacter, glob, or `$(...)` is ever interpreted.
  - Morta-gated + sandboxed. `install_tool` registers the effect with
    `requires_approval` (True by default for an outward process) and a sandbox profile
    pinning the effect to its own allowlist entry with network OFF by default, so an
    unapproved / out-of-profile invoke is refused BEFORE any process runs.
  - Output is UNTRUSTED DATA. The stdout is returned with `instruction_eligible=False`
    and `untrusted=True`; it may be recalled as DATA but is NEVER obeyed as an
    instruction — a wrapped tool cannot steer Decima.

OUTCOME → WEFT §8 status:
  - exit 0            → SUCCEEDED: {out: stdout (untrusted data), code: 0}
  - non-zero exit     → FAILED (ExecError, carrying the exit code) — a definite result
  - a timeout         → UNKNOWN (Ambiguous) — we do NOT know whether it completed, so the
                        outcome is never fabricated as success or failure (FOLD §11 #8)

Pure composition of the public executor / kernel APIs — no core edit.
"""
from decima.executor import ExecError, Ambiguous

PROCESS = "PROCESS"
DEFAULT_TIMEOUT = 30


def _build_argv(spec, args) -> list:
    """Resolve the ALLOWLISTED argv from a fixed `spec` + a caller's `args`.

    `spec` shape:
        {"argv":  [program, *fixed_args],   # argv[0] is the exact program; required
         "slots": [slot_name, ...]?,        # ordered arg slots the caller may fill
         "allow": {slot_name: [allowed_literal, ...]}}   # per-slot value allowlist

    Every appended token must be a declared slot whose value is a member of that slot's
    allowlist. A missing declaration, a non-string value, or an out-of-allowlist value
    raises ExecError BEFORE any process is spawned — injection is refused, not run.
    """
    if not isinstance(spec, dict):
        raise ExecError("process: spec must be a dict fixing the command + allowlist")
    base = spec.get("argv")
    if not (isinstance(base, list) and base and all(isinstance(a, str) for a in base)):
        raise ExecError("process: spec['argv'] must be a non-empty list of str (argv[0] is the program)")

    slots = spec.get("slots") or []
    allow = spec.get("allow") or {}
    if not isinstance(slots, list) or not isinstance(allow, dict):
        raise ExecError("process: spec['slots'] must be a list and spec['allow'] a dict")

    argv = list(base)
    for slot in slots:
        allowed = allow.get(slot)
        if not isinstance(allowed, list):
            raise ExecError(f"process: slot {slot!r} has no allowlist — refusing (fail closed)")
        if slot not in args:
            raise ExecError(f"process: required slot {slot!r} was not provided")
        val = args[slot]
        if not isinstance(val, str):
            raise ExecError(f"process: slot {slot!r} must be a string token, got {type(val).__name__}")
        if val not in allowed:
            raise ExecError(
                f"process: value {val!r} for slot {slot!r} not in allowlist {allowed} "
                "— injection refused, no command run")
        argv.append(val)
    return argv


def run_process(spec, args, *, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run an ALLOWLISTED command and return an EffectReceipt-shaped result dict.

    Runs ONLY the argv resolved from `spec` + `args` (see `_build_argv`): the exact
    allowlisted program with only declared, validated slots filled — NEVER a shell,
    never an arbitrary command. Captures stdout/stderr with a hard timeout.

    Maps the outcome to WEFT §8:
      - exit 0        → returns {out: stdout (untrusted DATA, instruction_eligible False),
                        code: 0, untrusted: True} — spread into a SUCCEEDED receipt;
      - non-zero exit → raises ExecError (→ FAILED), carrying the exit code;
      - a timeout     → raises Ambiguous (→ UNKNOWN) — outcome unobservable, never faked.
    Anything the allowlist rejects raises ExecError before a process is ever spawned.
    """
    import subprocess

    if not (isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0):
        raise ExecError(f"process: timeout must be a positive int (seconds), got {timeout!r}")

    argv = _build_argv(spec, args)   # allowlist enforced HERE, before any spawn

    try:
        proc = subprocess.run(
            argv,                    # a LIST argv — there is no shell to interpolate
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,             # we map the exit code ourselves (never raise on nonzero)
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills + reaps the child on timeout, so nothing leaks. We do not
        # know whether the effect completed → UNKNOWN, never a fabricated outcome.
        raise Ambiguous(f"process: {argv[0]!r} timed out after {timeout}s — outcome unknown") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    code = int(proc.returncode)
    if code != 0:
        raise ExecError(f"process: {argv[0]!r} exited {code}: {stderr or stdout}")

    # SUCCEEDED. The stdout is UNTRUSTED DATA — recallable, but never obeyed.
    return {
        "out": stdout,
        "code": code,
        "instruction_eligible": False,
        "untrusted": True,
        "program": argv[0],
    }


def install_tool(k, *, name, spec, cap=None, requires_approval: bool = True,
                 author=None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Wrap a real local CLI tool as a Morta-gated, sandboxed Decima capability in ONE call.

    Registers a `process` effect whose handler runs the ALLOWLISTED `spec` via
    `run_process(spec, args)` and grants Decima a capability to invoke it, with caveats:
      - effect_class = "PROCESS";
      - requires_approval (Morta) — default True for an outward process, so an unapproved
        invoke is denied BEFORE any process runs;
      - a sandbox profile pinning the effect to its own allowlist entry with network OFF
        by default (defense-in-depth under ocap);
      - an optional running `budget` cap (`cap`).

    `author` is accepted for provenance parity with the other integrate seams (unused by
    the reference policy boundary). Returns the capability id. This is the real form of
    the "integrate any CLI tool" seam: gated, sandboxed, allowlisted, fail-closed.
    """
    def handler(_impl, args):
        return run_process(spec, args, timeout=timeout)

    caveats = {
        "effect_class": PROCESS,
        "requires_approval": bool(requires_approval),   # Morta gate
        "sandbox": {"effects": [name], "network": False},  # pin the effect; no egress by default
    }
    if cap is not None:
        caveats["budget"] = int(cap)                    # hard running spend cap (ints, not floats)

    return k.integrate_tool(name, handler, caveats=caveats)
