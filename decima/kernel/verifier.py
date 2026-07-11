"""Verifier — decide whether to BELIEVE a generated output, deterministically
when a checker exists, else with a judge/critic fallback (C2; VISION "deterministic
verifiers … judge/critic models where deterministic verification is unavailable").

The router decides which tier *generates* a candidate; this module decides whether
the candidate is *trustworthy*. Two regimes:

  • deterministic — code/math/schema/transform outputs have a ground-truth check:
    recompute and compare, match a pattern, validate a schema. Cheap, exact, and
    the thing that makes a small model safe to lean on (VISION's cost multiplier —
    a local model + a deterministic verifier beats a frontier model you can't check).
  • judge / critic — when no deterministic check exists, a critic model scores the
    output. Offline-safe here: a deterministic STUB stands in so the oracle is
    reproducible; the real model call is the documented seam (mirrors
    `agent.ModelBrain` / `agent.live_engine_fn`).

Zero authority, like the router: a `Verdict` is data. Verifying an output grants no
permission to act on it — `capability.authorize` still gates every effect.
"""
import re
from dataclasses import dataclass

# Confidence is an integer in [0, 1_000_000] to match the codebase's fixed-point
# convention (see memory.remember confidence). None for a deterministic verdict —
# a ground-truth check is not a probability.
CONF_MAX = 1_000_000


@dataclass(frozen=True)
class Verdict:
    ok: bool
    method: str                 # "deterministic:<name>" | "judge"
    detail: str = ""
    score: int | None = None    # judge confidence in [0, CONF_MAX]; None when deterministic

    @property
    def deterministic(self) -> bool:
        return self.method.startswith("deterministic:")


# ── deterministic verifiers: (output, spec) -> (ok, detail) ──────────────────
_TRANSFORMS = {
    "upper": str.upper,
    "lower": str.lower,
    "reverse": lambda s: s[::-1],
    "identity": lambda s: s,
}


def _v_equals(output, spec):
    exp = spec.get("expected", "")
    return output == exp, f"output == {exp!r}"


def _v_regex(output, spec):
    pat = spec.get("pattern", "")
    return bool(re.search(pat, output or "")), f"matches /{pat}/"


def _v_nonempty(output, spec):
    return bool((output or "").strip()), "non-empty"


def _v_transform(output, spec):
    """Re-run a known transform on its input and compare — ground truth, no model.
    Ties to NONA's transform caps (upper/lower/reverse): if a small model claims to
    upper-case, we recompute and check. spec = {op, input}."""
    op = spec.get("op", "identity")
    fn = _TRANSFORMS.get(op)
    if fn is None:
        return False, f"unknown transform {op!r}"
    exp = fn(spec.get("input", ""))
    return output == exp, f"{op}({spec.get('input', '')!r}) == {exp!r}"


DETERMINISTIC = {
    "equals": _v_equals,
    "regex": _v_regex,
    "nonempty": _v_nonempty,
    "transform": _v_transform,
}


def has_verifier(name) -> bool:
    """Is there a deterministic checker by this name?"""
    return name in DETERMINISTIC


def default_judge(output, spec=None) -> Verdict:
    """Offline-safe stand-in for a critic model. The REAL judge is a model call (the
    seam) that scores the output for the task; deterministic here so the oracle is
    reproducible. Heuristic: a non-empty answer with no overt failure marker passes,
    with a length-scaled confidence."""
    text = (output or "").strip()
    bad_markers = ("error", "i can't", "i cannot", "unknown", "n/a")
    ok = bool(text) and not any(m in text.lower() for m in bad_markers)
    score = 0 if not ok else min(CONF_MAX, 200_000 + len(text) * 1_000)
    return Verdict(ok, "judge", "critic stub: non-empty & no failure marker", score)


def verify(output, *, verifier=None, spec=None, judge=None) -> Verdict:
    """Check `output`. If `verifier` names a known deterministic checker, run it
    (exact, no model). Otherwise fall back to the judge/critic. `judge` overrides
    the default critic (e.g. a real model call)."""
    spec = spec or {}
    if verifier and verifier in DETERMINISTIC:
        ok, detail = DETERMINISTIC[verifier](output, spec)
        return Verdict(ok, f"deterministic:{verifier}", detail)
    return (judge or default_judge)(output, spec)
