# Morta Capability and Caveat Language

## 1. Capability model

```text
Capability {
  effect: EffectRef
  target: Selector
  caveats: Predicate[]
  delegable: bool
  max_delegation_depth: uint
  implementation: BlobId?
  issuer_policy: CellId
}
```

A grant binds a capability to a principal. A capability ID is public metadata, not a bearer token. Use requires a signed possession proof and a live grant path.

Authorization decision:

```text
allow iff
  grant_path_valid
  and holder_signature_valid
  and requested_effect <= capability.effect
  and requested_target subset_of capability.target
  and every caveat evaluates TRUE
  and no applicable deny/kill/revocation exists
```

Predicates are three-valued: `TRUE`, `FALSE`, `UNKNOWN`. `UNKNOWN` fails closed.

## 2. Selector grammar

Selectors are declarative, canonical ASTs—not arbitrary code.

```text
selector :=
  none
| cell(CellId)
| subtree(CellId, EdgeType, max_depth)
| type(CellId)
| owner(PrincipalId)
| realm(RealmId)
| resource(Scheme, Authority, PathPattern)
| label(CellId)
| relation(EdgeType, selector)
| union(selector...)
| intersect(selector...)
| difference(selector, selector)
```

Constraints:

- Path patterns are segment-based; no regex in the kernel profile.
- Resource schemes are registered Type Cells: `file`, `https`, `git`, `model`, `social`, `email`, `calendar`, `shell`, etc.
- Selectors evaluate over a stated causal frontier.
- Dynamic selectors with unbounded graph traversal cannot authorize irreversible effects.

## 3. Core caveats

| Family | Predicate examples |
|---|---|
| Time | `not_before`, `expires_at`, `max_duration` |
| Quantity | `max_invocations`, `rate`, `concurrency` |
| Cost | `max_cost`, `max_tokens`, `max_gpu_seconds`, `currency` |
| Data | `max_classification`, `no_export`, `no_training`, `retention`, `allowed_regions` |
| Execution | `sandbox`, `network_allow`, `filesystem`, `cpu`, `memory`, `timeout` |
| Effect | `read_only`, `reversible_only`, `no_outward_effects`, `no_financial`, `no_identity_change` |
| Approval | `requires_approval(policy, approvers, quorum, freshness)` |
| Evidence | `requires_attestation(predicate, trust_policy)` |
| Model | `allowed_models`, `local_only`, `provider_allow`, `max_context` |
| Delegation | `nondelegable`, `max_depth`, `delegatee_selector` |
| Human factors | `presence_required`, `device_bound`, `session_bound` |
| Content | `moderation_policy`, `audience`, `brand_policy`, `license_policy` |
| Reliability | `idempotency_required`, `compensation_required`, `health_threshold` |

Budgets are reservations backed by a ledger. Two agents cannot each spend the same remaining dollar because both saw an old projection.

## 4. Morta permanent gates

Realm constitution policies define effect classes whose minimum caveats cannot be removed by ordinary attenuation or Nona promotion:

- Financial transfer or trade
- Public posting or communication as the user
- Production deployment
- Destructive deletion outside a recoverable sandbox
- Identity, authentication, or policy changes
- Secret export
- Voice/likeness cloning or publication
- Physical-device actuation above configured risk

Changing the constitution requires a distinct constitutional capability, delayed activation, strong reauthentication, and an attested event. There is no magical unchangeable bit; there is a deliberately difficult, visible governance path.

## 5. Attenuation

Define authority as a set of permitted invocations. Child `C` is valid only if:

```text
Allowed(C) ⊆ Allowed(P)
```

The kernel proves this structurally:

- Child effect equals or specializes parent effect.
- Child target selector is provably a subset of parent selector.
- Child includes every parent caveat unchanged or strengthened.
- Child may add caveats.
- Numeric maxima can only decrease.
- Expiry can only move earlier.
- Allowlists can only shrink; denylists can only grow.
- Approval quorum/trust/freshness can only strengthen.
- `delegable=false` cannot become true.
- Delegation depth decreases.
- Sandbox/network/filesystem privileges can only shrink.

If selector implication cannot be proven by the decidable kernel grammar, attenuation is rejected. An attestation cannot substitute for a proof of authority narrowing.

## 6. Approval binding

Approval attests to:

```text
digest(invocation_body, selected capability, resolved target,
       budget reservation, displayed human summary, expiry)
```

Changing arguments, target, cost ceiling, or content invalidates approval. Broad “approve whatever this agent does today” grants are ordinary capabilities and displayed as such.

## 7. Powerbox

The powerbox is a trusted broker agent with narrowly scoped capability-discovery and grant-proposal authority.

Flow:

1. Agent asserts a capability request with purpose, target, duration, and minimum scope.
2. Powerbox searches existing compatible grants.
3. It proposes the narrowest attenuation.
4. Policy auto-approves low-risk requests or routes a clear human approval.
5. Grant event binds holder and caveats.
6. Use is audited; idle grants expire automatically.

The powerbox never receives secrets. It issues broker handles that cause the secrets service to inject credentials directly into an authorized executor.

## 8. Revocation and races

- Revocation is evaluated at invocation acceptance and again at executor claim.
- Long-running effects use renewable leases. Revocation prevents renewal and triggers cancellation/compensation.
- Irreversible effects require a final preflight immediately before commit.
- Distributed partitions fail closed for effects whose policy requires fresh authority.
- Revocation cannot recall an already completed external effect; receipts and compensation represent reality honestly.

## 9. Example

```text
Parent:
  effect: social.post
  target: resource("social", "mastodon.example", "/accounts/mini")
  caveats:
    max_invocations(20/day)
    requires_approval(public_post, [mini], 1, 10m)
    brand_policy(brand_v7)

Child:
  effect: social.post
  same target
  caveats:
    max_invocations(2/day)
    requires_approval(public_post, [mini], 1, 5m)
    brand_policy(brand_v7)
    expires_at(2026-06-24T00:00:00Z)
```

The child is mechanically narrower. Removing approval or changing the target to all accounts is rejected.
