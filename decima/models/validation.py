"""Structured-proposal validation — reject invalid model output, never repair it.

A model may PROPOSE a structured action (a dict). Before anything is done with it,
the proposal is validated against an EXPLICIT schema. Invalid proposals are REJECTED
and recorded as MODEL ERRORS — they are never repaired by arbitrary `eval`/`exec`
(there is none in this module) and never silently coerced into an effect. A bounded
re-prompt path lets a caller ask the model again up to N times; if it never produces
a valid proposal, the caller gets an exhausted result and NOTHING is executed.

This is the enforcement of invariant 4 at the data layer: a model's structured
output is a PROPOSAL (DATA). Passing validation makes it *well-formed*, NOT
*authorized* — turning a valid proposal into an effect still requires the kernel's
authorization + approval + receipt chain, which lives outside this package. The
`ProposedAction` this module yields is inert: no execute/invoke/authorize method,
`instruction_eligible=False` (invariant 5).
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.models.providers import (
    ModelRequest,
    ModelResponse,
    ProposedAction,
)

# ── a tiny declarative schema ─────────────────────────────────────────────────
# schema = {
#   "action": "send_email",                 # optional: required literal action name
#   "fields": {
#       "to":   {"type": "string", "required": True},
#       "n":    {"type": "int", "min": 0, "max": 10},
#       "mode": {"type": "string", "enum": ["draft", "send"]},
#   },
# }
_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "int": (int,),
    "bool": (bool,),
    "list": (list,),
    "dict": (dict,),
}


@dataclass(frozen=True)
class ValidationResult:
    """The verdict on one proposal — DATA. `errors` lists every schema violation
    (in a deterministic order). `proposal`, when valid, is the inert `ProposedAction`
    ready for the SEPARATE authorization chain; when invalid it is None."""

    valid: bool
    errors: tuple[str, ...] = ()
    proposal: ProposedAction | None = None
    raw: dict | None = None

    def to_content(self) -> dict:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "raw": dict(self.raw) if self.raw is not None else None,
        }


def _field_errors(name: str, spec: dict, value) -> list[str]:
    errs: list[str] = []
    tname = spec.get("type", "string")
    allowed = _TYPES.get(tname, (str,))
    # bool is a subtype of int in Python — reject it where an int is required.
    if tname == "int" and isinstance(value, bool):
        return [f"{name}: expected int, got bool"]
    if not isinstance(value, allowed):
        return [f"{name}: expected {tname}, got {type(value).__name__}"]
    if "enum" in spec and value not in spec["enum"]:
        errs.append(f"{name}: {value!r} not in enum {spec['enum']}")
    if tname == "int":
        if "min" in spec and value < spec["min"]:
            errs.append(f"{name}: {value} < min {spec['min']}")
        if "max" in spec and value > spec["max"]:
            errs.append(f"{name}: {value} > max {spec['max']}")
    return errs


def validate_proposal(proposal: dict | None, schema: dict) -> ValidationResult:
    """Validate a raw proposal dict against `schema`. Never repairs — it either
    accepts (returning an inert `ProposedAction`) or REJECTS with explicit errors.
    Deterministic; pure."""
    if proposal is None or not isinstance(proposal, dict):
        return ValidationResult(False, ("proposal is not a dict",), None, None)

    errors: list[str] = []

    want_action = schema.get("action")
    got_action = proposal.get("action", want_action)
    if want_action is not None and got_action != want_action:
        errors.append(f"action: expected {want_action!r}, got {got_action!r}")

    fields = schema.get("fields", {})
    for name, spec in fields.items():
        if not isinstance(spec, dict):
            spec = {"type": "string"}
        if name not in proposal:
            if spec.get("required"):
                errors.append(f"{name}: required field missing")
            continue
        errors.extend(_field_errors(name, spec, proposal[name]))

    if schema.get("strict"):
        known = set(fields) | {"action"}
        for key in proposal:
            if key not in known:
                errors.append(f"{key}: unexpected field (strict schema)")

    if errors:
        return ValidationResult(False, tuple(errors), None, dict(proposal))

    action = ProposedAction(
        action=str(proposal.get("action", want_action or "")),
        params={k: v for k, v in proposal.items() if k != "action"},
        source_model="",
        instruction_eligible=False,
    )
    return ValidationResult(True, (), action, dict(proposal))


def validate_response(response: ModelResponse, schema: dict) -> ValidationResult:
    """Validate the structured proposal on a `ModelResponse`. A refusal/failure or a
    missing structured payload is itself a validation failure (nothing to execute)."""
    if response.refused:
        return ValidationResult(False, ("model refused",), None, None)
    if response.failed:
        return ValidationResult(False, (f"model error: {response.error}",), None, None)
    result = validate_proposal(response.structured, schema)
    if result.valid and result.proposal is not None:
        # stamp provenance of which model proposed it (still inert data)
        object.__setattr__(result.proposal, "source_model", response.model)
    return result


@dataclass(frozen=True)
class RepromptResult:
    """The outcome of a bounded re-prompt loop — DATA. `result` is the first VALID
    `ValidationResult`, or the last invalid one if every attempt failed. `errors`
    collects each rejected attempt's errors (recorded as model errors, never
    repaired). Nothing here executes anything."""

    ok: bool
    result: ValidationResult
    attempts: int
    rejected: tuple[ValidationResult, ...] = ()


def validate_with_reprompt(
    provider,
    request: ModelRequest,
    schema: dict,
    *,
    max_attempts: int = 3,
) -> RepromptResult:
    """Ask `provider.complete(request)` for a schema-valid proposal, RE-PROMPTING up
    to `max_attempts` times. Each invalid proposal is REJECTED and collected as a
    model error (never eval-repaired). Returns the first valid result, or an
    exhausted result after the bound — in which case NOTHING is executed. The schema
    is attached to the request so the provider knows to propose structured output."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    from dataclasses import replace

    req = (
        request
        if request.structured_schema is not None
        else replace(request, structured_schema=schema)
    )
    rejected: list[ValidationResult] = []
    last: ValidationResult | None = None
    for attempt in range(1, max_attempts + 1):
        response = provider.complete(req)
        result = validate_response(response, schema)
        last = result
        if result.valid:
            return RepromptResult(True, result, attempt, tuple(rejected))
        rejected.append(result)
    assert last is not None
    return RepromptResult(False, last, max_attempts, tuple(rejected))


# ── recording a rejected proposal as a model error on the Weft ────────────────
MODEL_ERROR = "model_error"


def record_rejection(k, result: ValidationResult, *, model: str, author=None, provenance=None):
    """Fold a REJECTED proposal onto the Weft as a `model_error` Cell (audit,
    invariant 1). Records the errors and the raw proposal as DATA — it does NOT
    repair or execute it, and mints no authority. `provenance`, if given, links the
    error to the request Cell (a `rejects` edge)."""
    from decima.kernel.hashing import content_id
    from decima.kernel.model import assert_content, assert_edge

    if result.valid:
        raise ValueError("record_rejection is only for invalid proposals")
    author = author or k.decima_agent_id
    cid = content_id(
        {
            "model_error": model,
            "errors": list(result.errors),
            "lamport": k.weft.lamport,
        }
    )
    content = {
        "model": model,
        "errors": list(result.errors),
        "raw": dict(result.raw) if result.raw is not None else None,
        "instruction_eligible": False,
    }
    assert_content(k.weft, author, cid, MODEL_ERROR, content)
    if provenance is not None:
        assert_edge(k.weft, author, cid, "rejects", provenance)
    return cid
