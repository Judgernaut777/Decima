"""The runnable organ — building a capability from a candidate, and running it (wave N5).

This is where a promoted candidate stops being a record and starts being a function the
system can call. Everything dangerous about that is concentrated in two bindings and one
refusal, and the module is arranged around them.

BINDING ONE: THE CODE THAT RUNS IS THE CODE THAT WAS EVALUATED. A capability built here
carries `implementation_digest` — `blob_id(nfc(source), kind="blob")`, the exact value
`candidate.implementation_digest` computed and the Reckoner recorded in its
`evaluation_result`. Before anything is dispatched, the source is re-read from the candidate
Cell and re-hashed in that domain; a mismatch is refused (`IMPL_UNBOUND`) and no worker is
spawned. This is what makes "the tests gate the code" a hash equality rather than an
assumption: tamper with the candidate's source after evaluation and the organ stops running,
loudly.

BINDING TWO: THE BYTES HANDED TO THE JAIL ARE THE BYTES THE GRANT NAMES. `run_worker`
enforces its own digest binding, and it hashes in a DIFFERENT domain —
`blob_id(source.encode(), kind="worker-impl")`, raw bytes, no NFC. The two digests can never
agree for the same source, so design §5.6's instruction to set
`WorkerRequest.implementation_digest` to "the candidate's digest" would make every single
invoke raise `DigestMismatch` — a total outage that reads like a mysterious bug rather than
a hash-domain error. The honest fix is to bind BOTH, explicitly: the capability records
`worker_digest` alongside `implementation_digest`, and the dispatch hands the worker the
capability's recorded `worker_digest` — never a digest recomputed from whatever source it
just read. Recomputing it there would make the check tautological. Two digests, two domains,
two independent chances to catch a swap.

THE ID COVERS THE WHOLE GRANT, NOT JUST THE CODE. `capability_cell_id` hashes the organ AND
its terms — grantee, granter, and the merged caveats — because those terms ARE the grant. An
id over (name, digest, tier) alone made "build the same organ twice" mean "reuse the same
Cell" even when the second build named a different grantee or dropped the operator's caveats,
and an ASSERT is last-writer-wins: the Morta gate and the budget ceiling vanished from a LIVE,
already-promoted capability, with no attenuation check on the path and the promotion Cell
still pointing at it. Different terms are now a different Cell, and a same-id build that
differs in any remaining field is REFUSED rather than written.

THE REFUSAL: `network` HAS NO EXECUTOR, AND THAT IS NOT A POLICY. `run_worker` refuses any
network-permitted profile at the primitive, for every caller, because no egress
mediation/redaction seam is wired. So this module maps tiers to profiles with a table that
simply has no entry for `network`: the refusal is the ABSENCE of an executor, reported as
`NO_EXECUTOR`, and it is deliberately distinguishable from the authorization gate's
`APPROVAL_REQUIRED`. Nothing an operator can approve makes a `network` organ run — and
offering them a prompt that cannot help only teaches them to click yes.

WHAT THE CANARY CAN AND CANNOT SEE. `Weave.canary_health` counts a receipt as a failure when
`status == "FAILED"` or `ok is False`. `WorkerResponse` has no `ok` field — that key came
from the reference executor's ad-hoc result dict — so `ok` is computed HERE, against the
candidate's declared `output_schema`. Said plainly, because it bounds what the canary
measures: a crash, a timeout, a refusal and a wrongly-TYPED answer all fold as failures; a
wrong answer of the right type does not. Correctness regressions are the Reckoner's
differential stage, upstream. The receipt records `contract: "declared"` or `"none"` so a
reader can tell which of those two worlds a given `ok` came from instead of guessing.

NOTHING HOST-VARIABLE REACHES THE LOG. The worker's `diagnostics["isolation"]` manifest
carries a `mkdtemp` path, a live `/proc/self/fd` listing, environment keys, rlimit
read-backs, an inner pid and arch-dependent seccomp state. None of it is recorded. What is
recorded is a small boolean projection — did the jail, the network isolation, the
no-new-privs bit and seccomp actually engage — plus a digest of the output rather than the
output, because the child falls back to `repr(out)` on a non-serializable return value and a
`repr` can embed a memory address.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from decima.kernel import capability as cap_mod
from decima.kernel import invoke as kinvoke
from decima.kernel import model, receipts
from decima.kernel.crypto import Keyring
from decima.kernel.hashing import blob_id, content_id, nfc
from decima.kernel.invoke import EffectHandler, EffectOutcome, EffectRequest
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.nona import anchors
from decima.services.nona import candidate as candidate_mod
from decima.workers import (
    PURE,
    DigestMismatch,
    IsolationError,
    LeaseError,
    LeaseGuard,
    WorkerError,
    WorkerProfile,
    WorkerRequest,
    WorkerResponse,
    compute_digest,
    run_worker,
)
from decima.workers import protocol as worker_protocol

CAPABILITY = "capability"

# The one mechanism a promoted organ is realized through. It is not a tier — the TIER is the
# declared effect class, and it is what selects the jail below.
GENERATED_CODE = "generated_code"

# Tier → worker profile. A tier with NO entry has NO executor; see the module docstring.
#
# `read_only` maps to PURE deliberately rather than to WORKSPACE: WORKSPACE's field values are
# identical to PURE's today ("behaves as PURE" until the bind-mount seam lands), so choosing it
# would grant nothing and create a false expectation about what is enforced.
#
# `workspace_write` and `financial` are ABSENT for the same reason `network` is. Mapping
# `workspace_write` to WORKSPACE would promise a bind-mounted subtree that does not exist yet;
# mapping it to PURE would run it with no write access at all, which is not the thing that was
# evaluated and promoted. Either way the operator would be told something untrue about what is
# running. An absent entry says the honest thing — there is no executor for that tier — and it
# says it at the receipt, in a code a UI can render as NOT EXECUTABLE.
TIER_PROFILES: dict[str, WorkerProfile] = {
    anchors.PURE: PURE,
    anchors.READ_ONLY: PURE,
}

# Refusal codes recorded on the receipt. Each names a STRUCTURAL fact about the path, which
# is what makes it different from a denial code (an authorization judgement about a caller).
NO_EXECUTOR = "NO_EXECUTOR"  # this tier has no worker profile — not a policy, an absence
IMPL_UNBOUND = "IMPL_UNBOUND"  # source no longer hashes to the evaluated digest
DIGEST_MISMATCH = "DIGEST_MISMATCH"  # the jail refused the bytes it was handed
LEASE_REFUSED = "LEASE_REFUSED"  # expired / replayed / malformed lease
ISOLATION_REFUSED = "ISOLATION_REFUSED"  # a mandatory confinement layer would not engage
WORKER_REFUSED = "WORKER_REFUSED"  # the worker layer refused to dispatch at all

# The logical window (in lamport ticks) a dispatch lease is valid for. Integer logical time,
# never a wall clock: the lease is validated against `weave.frontier_lamport`.
LEASE_WINDOW = 64

_WORKER_NAME = "nona.organ"

# The declared-contract types an organ's `output_schema` may name. Deliberately tiny: this
# checks SHAPE, not correctness, and pretending otherwise would be the more dangerous lie.
_SCHEMA_TYPES: dict[str, type | tuple[type, ...]] = {
    "int": int,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "null": type(None),
}


class OrganRefused(RuntimeError):
    """An organ could not be built or resolved. Always a statement about the EVIDENCE — a
    missing candidate, a broken digest binding — never about a transient condition."""


Runner = Callable[..., WorkerResponse]


@dataclass(frozen=True)
class Implementation:
    """The digest-bound description of what an organ actually runs."""

    candidate: str
    source: str
    entrypoint: str
    implementation_digest: str  # blob domain — what the Reckoner evaluated
    worker_digest: str  # worker-impl domain — what the jail binds
    output_schema: dict[str, Any]


# ── building the capability a promotion will expose ──────────────────────────
# The fields that make a capability Cell THE SAME GRANT: what runs (organ name, both digest
# domains, the candidate it resolves through), what it is (effect, tier, target, delegability)
# and WHO holds it under WHAT terms (grantee, granter, caveats). Two builds that agree on all
# of them are the same grant re-asserted; two that disagree are different grants and must not
# share a Cell — see `build_capability`.
_GRANT_TERMS: tuple[str, ...] = (
    "name",
    "effect",
    "target",
    "delegable",
    "declared_effect_class",
    "implementation_digest",
    "worker_digest",
    "candidate",
    "impl",
    "grantee",
    "granter",
)
# Caveats the FOLD owns rather than the builder: promotion strips `sandbox_only` from live
# content (weave.py, promotion arm), so comparing it would report every promoted organ as
# "different terms" the moment it is rebuilt. `quarantined` is derived for the same reason
# and is not a grant term at all.
_DERIVED_CAVEATS = frozenset({"sandbox_only"})


def _grant_terms(content: dict[str, Any]) -> dict[str, Any]:
    """The grant a capability Cell asserts, modulo what the fold derives."""
    caveats = content.get("caveats") or {}
    return {
        **{k: content.get(k) for k in _GRANT_TERMS},
        "caveats": {k: v for k, v in caveats.items() if k not in _DERIVED_CAVEATS},
    }


def capability_cell_id(
    name: str,
    implementation_digest: str,
    tier: str,
    *,
    grantee: str | None,
    granter: str | None,
    caveats: dict[str, Any],
) -> str:
    """Content-addressed over EVERYTHING THE GRANT ASSERTS: the organ (name, digest, tier)
    AND its terms (grantee, granter, caveats). Building the same organ twice on the same
    terms is ONE capability, not two grants to reconcile; a new revision has a new digest and
    is therefore a new capability, which is exactly what `supersede` expects.

    The terms are in the preimage because they ARE the grant. An id over (name, digest, tier)
    alone made "the same organ" mean "the same code", so re-building a promoted organ for a
    different grantee, or with the operator's caveats dropped, landed on the SAME Cell — and
    an ASSERT is last-writer-wins, so it silently widened a live grant: the Morta
    `requires_approval` gate and the budget ceiling gone, the grantee repointed, no
    `attenuation_valid` check anywhere on the path, and the promotion Cell (which keys on the
    capability id) still live, so the widened content inherited an attestation that was
    signed against different terms. Authority widening through a public product function.
    The keyword arguments are REQUIRED and not defaulted, so no caller can omit a term and
    silently reproduce that id.
    """
    return "capability:" + content_id(
        {
            "organ": nfc(name),
            "impl": implementation_digest,
            "tier": tier,
            "grantee": grantee,
            "granter": granter,
            "caveats": dict(caveats),
        },
        kind="cell",
    )


def build_capability(
    weft: Weft,
    weave: Weave,
    author: str,
    *,
    candidate: str,
    tier: str,
    name: str | None = None,
    grantee: str | None = None,
    granter: str | None = None,
    caveats: dict[str, Any] | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Mint the capability Cell for a candidate — BORN QUARANTINED — and edge it to its impl.

    `decima/` has never had this function, which is why nothing could get from a promoted
    capability id back to the source it promises to run: `promotion.promote` takes a cap id
    that something else was supposed to have created, and until now the only creators were
    hand-rolled test fixtures with no `impl` and no `candidate` field. This is that missing
    step, and it is where the two digest domains are pinned side by side.

    The capability is born `quarantined: True` with `sandbox_only`, so it is runnable ONLY by
    a sandbox principal until a trusted promoter signs a live `promotion` Cell for it. Morta's
    floor for the declared effect class is merged in AT BUILD TIME (`with_morta_floor`), so a
    `financial` organ carries `requires_approval` before any attestation exists — the fold's
    lift strips `sandbox_only` and nothing else, so that floor survives promotion.

    REFUSES rather than overwrites. The Cell id covers every term of the grant, so a build on
    different terms is a different Cell; if a Cell with THIS id already exists and asserts
    different terms anyway (a field outside the id, e.g. `limits`, or something squatting the
    id), that is refused (`OrganRefused`). An ASSERT is last-writer-wins and nothing at the
    Weft door re-authorizes it, so "build it again, slightly differently" must never be a way
    to edit a grant that is already live and already promoted. Re-building on IDENTICAL terms
    is still idempotent: same id, same content, one more (harmless) assertion.
    """
    cell = weave.get(candidate)
    if cell is None or cell.type != candidate_mod.CANDIDATE:
        raise OrganRefused(f"no such candidate cell {candidate!r}")
    source = cell.content.get("source")
    if not isinstance(source, str) or not source:
        raise OrganRefused(f"candidate {candidate!r} carries no source")
    recorded = cell.content.get("implementation_digest")
    computed = candidate_mod.implementation_digest(source)
    if recorded != computed:
        raise OrganRefused(
            f"candidate {candidate!r} does not hash to its own recorded digest "
            f"({recorded!r} vs {computed!r}): refusing to build a capability over source "
            "that was altered after it was proposed"
        )
    if tier not in candidate_mod.EFFECT_CLASSES:
        raise OrganRefused(f"unknown tier {tier!r}")
    if granter != author:
        # N7 (design R1): a grant may only be asserted by its own `granter` — you hand on
        # authority in your own name or not at all. The Weft door refuses this anyway
        # (`decima.kernel.authorship`); saying it here turns an opaque WeftError deep in
        # `append` into a legible refusal that names the field the caller got wrong.
        raise OrganRefused(
            f"granter {granter!r} is not the asserting author {author!r}: an organ grant is "
            "asserted by the principal that issues it, and the Weft door refuses any other "
            "shape (authority is not written in someone else's name)"
        )

    entrypoint = str(cell.content.get("entrypoint") or "main")
    organ_name = nfc(name or f"organ:{computed[:16]}")
    impl: dict[str, Any] = {
        # The source rides by reference: the capability names the candidate Cell and the
        # digest, never a second copy of the bytes. Promotion grants an edge to an immutable
        # digest; it never mutates code.
        "candidate": candidate,
        "entrypoint": entrypoint,
        "limits": dict(limits or {"cpu_seconds": 2}),
    }
    merged = cap_mod.with_morta_floor(tier, {**(caveats or {}), "sandbox_only": True})
    content = cap_mod.capability_content(
        name=organ_name,
        effect=GENERATED_CODE,
        caveats=merged,
        impl=impl,
        quarantined=True,
        grantee=grantee,
        granter=granter,
    )
    # The tier is read by the fold (`Weave._candidate_tier`) to decide WHO may sign the
    # promotion, so it is a top-level field, not a caveat that attenuation could reshape.
    content["declared_effect_class"] = tier
    content["implementation_digest"] = computed
    content["worker_digest"] = compute_digest(source)
    content["candidate"] = candidate
    content["lifecycle"] = "BUILT"

    cap_id = capability_cell_id(
        organ_name, computed, tier, grantee=grantee, granter=granter, caveats=merged
    )
    existing = weave.get(cap_id)
    if existing is not None and (
        existing.type != CAPABILITY or _grant_terms(existing.content) != _grant_terms(content)
    ):
        raise OrganRefused(
            f"capability {cap_id!r} already exists with different content: refusing to "
            "overwrite a live grant with a build it was not issued from (an ASSERT is "
            "last-writer-wins and is not authorized at the Weft door, so this would edit "
            "authority in place and inherit any promotion signed against the old terms). "
            "Change the terms — they are part of the id — or supersede the grant."
        )
    model.assert_content(weft, author, cap_id, CAPABILITY, content)
    model.assert_edge(weft, author, cap_id, "impl_of", candidate)
    return {
        "capability": cap_id,
        "candidate": candidate,
        "tier": tier,
        "implementation_digest": computed,
        "worker_digest": content["worker_digest"],
        "quarantined": True,
    }


def resolve_implementation(weave: Weave, cap_id: str) -> Implementation:
    """Walk capability → candidate → source, re-checking the evaluated digest binding.

    Refuses (`OrganRefused`) rather than returning a best guess: an organ whose source no
    longer hashes to the digest its evaluation cited is not the thing that was evaluated, and
    running it would silently break the only claim promotion makes.
    """
    cap = weave.get(cap_id)
    if cap is None or cap.type != CAPABILITY:
        raise OrganRefused(f"no such capability {cap_id!r}")
    candidate_id = cap.content.get("candidate")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise OrganRefused(
            f"capability {cap_id!r} names no candidate: it was not built by "
            "decima.services.nona.executor.build_capability, so there is no source to bind"
        )
    cell = weave.get(candidate_id)
    if cell is None or cell.type != candidate_mod.CANDIDATE:
        raise OrganRefused(f"capability {cap_id!r} points at missing candidate {candidate_id!r}")
    source = cell.content.get("source")
    if not isinstance(source, str) or not source:
        raise OrganRefused(f"candidate {candidate_id!r} carries no source")

    declared = cap.content.get("implementation_digest")
    computed = candidate_mod.implementation_digest(source)
    if declared != computed:
        raise OrganRefused(
            f"implementation digest mismatch for {cap_id!r}: the grant names {declared!r} "
            f"but the candidate's source hashes to {computed!r} — the code that would run "
            "is not the code that was evaluated"
        )
    worker_digest = cap.content.get("worker_digest")
    if not isinstance(worker_digest, str) or not worker_digest:
        raise OrganRefused(
            f"capability {cap_id!r} records no worker_digest: the jail's digest binding "
            "would have nothing to check against"
        )
    impl = cap.content.get("impl") or {}
    entrypoint = str(impl.get("entrypoint") or cell.content.get("entrypoint") or "main")
    schema = cell.content.get("output_schema")
    return Implementation(
        candidate=candidate_id,
        source=source,
        entrypoint=entrypoint,
        implementation_digest=computed,
        worker_digest=worker_digest,
        output_schema=dict(schema) if isinstance(schema, dict) else {},
    )


# ── the declared `generated_code` effect handler ─────────────────────────────
def output_digest(value: object) -> str:
    """A content-address of an untrusted return value — recorded INSTEAD of the value.

    The worker JSON-round-trips whatever the entrypoint returned and falls back to
    `repr(out)` when that fails, so the value can carry a memory address, and generated
    source is free to call `time` or `random` on its own. A digest of its canonical JSON text
    keeps the receipt replayable when the organ is deterministic and makes it obvious when it
    is not, without ever letting a float or a host address into hashed content.
    """
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        text = repr(value)
    return blob_id(text.encode("utf-8"), kind="blob")


def conforms(value: object, schema: Mapping[str, Any]) -> bool | None:
    """Does `value` satisfy the candidate's declared output contract?

    `None` means THERE IS NO CHECKABLE CONTRACT — not "it passed". The caller records that
    distinction on the receipt, because a canary that silently treats "unjudgeable" as
    "healthy" is worse than one that admits it measures crashes only.
    """
    declared = schema.get("type")
    if not isinstance(declared, str) or declared == "any":
        return None
    expected = _SCHEMA_TYPES.get(declared)
    if expected is None:
        return None
    if expected is int and isinstance(value, bool):
        return False  # a bool is an int in Python and never what `type: int` meant
    return isinstance(value, expected)


def _isolation_projection(response: WorkerResponse) -> dict[str, Any]:
    """A STABLE boolean projection of the in-child isolation manifest.

    The manifest itself never enters the log (see the module docstring). What survives is the
    answer to "did each MANDATORY layer actually engage?" — the four assertions a PURE
    worker's soundness rests on, and the only ones that do not vary between honest hosts.

    The best-effort seccomp layer is deliberately EXCLUDED, on the same reasoning
    `reckoner.environment_digest` excludes it: the filter table is aarch64-only, so recording
    it would make an otherwise byte-identical replay of this log differ between an aarch64 and
    an x86_64 host — a host fact masquerading as a fact about the invocation. It stays in the
    worker's unhashed diagnostics where it belongs.
    """
    manifest = response.diagnostics.get("isolation")
    if not isinstance(manifest, dict):
        return {"isolation_reported": False}
    namespaces = manifest.get("namespaces")
    ns = namespaces if isinstance(namespaces, dict) else {}
    return {
        "isolation_reported": True,
        "no_new_privs": bool(manifest.get("no_new_privs")),
        "namespaces": bool(ns.get("engaged")),
        "fs_jail": bool(ns.get("fs_jail")),
        "net_isolated": bool(ns.get("net_isolated")),
    }


def generated_code_effect(
    weft: Weft,
    weave: Weave,
    *,
    run: Runner = run_worker,
    lease_guard: LeaseGuard | None = None,
    timeout: int = 10,
    record_consumption: bool = True,
) -> EffectHandler:
    """The declared effect handler for `generated_code`, reached ONLY through the invoke seam.

    By the time this runs, `capability.verify_proof` has already said yes: the holder proved
    possession, the grant is in its envelope, the delegation path is downhill, the capability
    is not quarantined or revoked, and Morta's approval gate (if any) has been cleared. This
    function therefore never re-decides authority — it decides only whether there is a lawful
    way to RUN what was authorized, and refuses structurally when there is not.

    `run` is injected so tests can drive the mapping without spawning; production is the same
    jail every other untrusted effect uses, with its digest binding and UNKNOWN-on-timeout
    semantics intact.
    """
    guard = lease_guard if lease_guard is not None else LeaseGuard()
    if lease_guard is None:
        # A LeaseGuard is process-local, so "a lease is single-use" would only hold until the
        # next restart. Seed it from the DURABLE fold of prior consumptions so a lease spent
        # in an earlier process is still refused as a replay here.
        for idem, attempt in cells.consumed_lease_keys(weave):
            guard.mark_consumed(idem, attempt)

    def handle(request: EffectRequest) -> EffectOutcome:
        tier = request.cap_content.get("declared_effect_class")
        profile = TIER_PROFILES.get(tier) if isinstance(tier, str) else None
        if profile is None:
            # STRUCTURAL, not a policy toggle: there is no worker profile for this tier, and
            # the only one that would permit egress is refused at the primitive for every
            # caller until egress mediation lands. No approval enables this.
            return EffectOutcome(
                status=receipts.FAILED,
                ok=False,
                refusal=NO_EXECUTOR,
                error=(
                    f"tier {tier!r} has no executor: the only network-permitted worker "
                    "profile is refused at decima/workers/execution.py until an egress "
                    "mediation seam exists, so there is nothing to approve — this is an "
                    "absence of a path, not a withheld permission"
                ),
                provenance={"tier": str(tier), "profile": ""},
            )
        try:
            impl = resolve_implementation(weave, request.cap_id)
        except OrganRefused as exc:
            return EffectOutcome(
                status=receipts.FAILED, ok=False, refusal=IMPL_UNBOUND, error=str(exc)
            )

        lease: dict[str, Any] = {
            "step_id": request.invocation_id,
            "worker": _WORKER_NAME,
            # Logical frontier time, integers only — the lease never sees a wall clock.
            "issued_frontier": int(request.now),
            "expiry": int(request.now) + LEASE_WINDOW,
            "attempt": int(request.attempt),
            "idempotency_key": request.nonce,
        }
        # `cost` is an ocap budget argument, not an effect argument: the child calls
        # `fn(**arguments)`, so passing it would be a TypeError against every honest organ.
        arguments = {k: v for k, v in request.args.items() if k not in ("cost", "idempotency")}
        worker_request = WorkerRequest(
            invocation_id=request.invocation_id,
            job_id=content_id(
                {"job": request.invocation_id, "attempt": int(request.attempt)}, kind="cell"
            ),
            effect=GENERATED_CODE,
            # The digest the GRANT recorded — never one recomputed from the source we just
            # read, which would make the jail's own check tautological.
            implementation_digest=impl.worker_digest,
            arguments=arguments,
            lease=lease,
            capability_proof=dict(request.proof),
        )

        refusal = ""
        error = ""
        response: WorkerResponse | None = None
        try:
            response = run(
                worker_request,
                impl.source,
                impl.entrypoint,
                now=int(request.now),
                profile=profile,
                lease_guard=guard,
                timeout=timeout,
            )
        except DigestMismatch as exc:
            refusal, error = DIGEST_MISMATCH, str(exc)
        except LeaseError as exc:
            refusal, error = LEASE_REFUSED, str(exc)
        except IsolationError as exc:
            # Named distinctly from WORKER_REFUSED below: "a mandatory confinement layer
            # would not engage" is a different fact from "the worker layer would not
            # dispatch", and only the first one means the host is the problem.
            refusal, error = ISOLATION_REFUSED, str(exc)
        except WorkerError as exc:
            refusal, error = WORKER_REFUSED, str(exc)

        if record_consumption and guard.consumed(lease):
            # The lease really was spent (run_worker consumes before it dispatches), so make
            # that durable — otherwise single-use is true only inside this process.
            cells.record_lease_consumption(
                weft,
                request.holder,
                idempotency_key=str(lease["idempotency_key"]),
                attempt=int(lease["attempt"]),
            )

        if response is None:
            return EffectOutcome(
                status=receipts.FAILED,
                ok=False,
                refusal=refusal,
                error=error,
                provenance={"tier": str(tier), "profile": profile.name},
            )

        provenance = {"tier": str(tier), "profile": profile.name, **_isolation_projection(response)}
        if response.status == worker_protocol.UNKNOWN:
            # Honesty rule: a backstop kill is unobservable. UNKNOWN, never a fabricated pass
            # — and `ok=False`, so the canary counts it.
            return EffectOutcome(
                status=receipts.UNKNOWN,
                ok=False,
                error="worker killed by its backstop: the outcome is unobserved",
                provenance={**provenance, "contract": "none"},
            )
        out = response.receipt_data.get("output")
        verdict = conforms(out, impl.output_schema)
        succeeded = response.status == worker_protocol.SUCCEEDED
        return EffectOutcome(
            status=receipts.SUCCEEDED if succeeded else receipts.FAILED,
            # A semantic failure: the organ returned normally and returned the wrong SHAPE.
            # `verdict is None` means the candidate declared no checkable contract, and the
            # receipt says so rather than implying a verdict it did not make.
            ok=succeeded and verdict is not False,
            output_digest=output_digest(out),
            error="" if succeeded else "the organ raised inside the jail",
            provenance={**provenance, "contract": "declared" if verdict is not None else "none"},
        )

    return handle


def organ_effects(
    weft: Weft,
    weave: Weave,
    *,
    run: Runner = run_worker,
    lease_guard: LeaseGuard | None = None,
    timeout: int = 10,
) -> dict[str, EffectHandler]:
    """The effect table Nona injects into the kernel's invoke seam: exactly one entry.

    Nona adds ONE declared effect to the system, not a general escape hatch. Anything else an
    organ's capability might name has no handler and is refused by the seam itself.
    """
    return {
        GENERATED_CODE: generated_code_effect(
            weft, weave, run=run, lease_guard=lease_guard, timeout=timeout
        )
    }


def invoke_organ(
    weft: Weft,
    keyring: Keyring,
    agent_cell: Cell,
    cap_id: str,
    args: dict[str, Any] | None = None,
    *,
    run: Runner = run_worker,
    lease_guard: LeaseGuard | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Invoke a promoted organ through the kernel's authorized seam.

    A thin composition on purpose: this function holds no authority decision of its own. It
    injects the `generated_code` handler and hands everything to `kernel.invoke.invoke`, which
    runs the full ocap spine first. A quarantined, revoked, ungranted or unapproved capability
    is refused there and never reaches the handler at all.

    It takes NO `Weave` and NO approval set, for the same reason the seam itself does not: an
    authority input a caller can choose is not a caveat. The handler's own read of the Log is
    folded here, immediately before the seam folds the frontier it decides on, and it is used
    only for the two digest bindings and the durable lease-replay seed — never to decide
    authority.
    """
    return kinvoke.invoke(
        weft,
        keyring,
        agent_cell,
        cap_id,
        dict(args or {}),
        effects=organ_effects(
            weft, Weave.fold(weft), run=run, lease_guard=lease_guard, timeout=timeout
        ),
    )
