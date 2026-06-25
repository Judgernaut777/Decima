# Sandboxed Principals — the isolation substrate

**Status:** design + reference seam (SB1). Read with `MORTA_CAPABILITIES.md` (ocap),
`WEFT_PROTOCOL.md` §6/§8 (invoke/receipts), and `CAPABILITY_MAP.md` (where engines plug in).

Decima's object-capability model decides **what a principal MAY do** — which capabilities it
holds. The sandbox decides **what an engine's effect handler MAY TOUCH while doing it** —
network, filesystem, processes, resources. They are different questions, and you need both:
ocap bounds *authority*, the sandbox bounds *footprint*. A capability you legitimately hold
can still be run by a compromised or buggy engine that tries to reach further than the task
needs; the sandbox is the defense-in-depth that stops it. **No ambient authority** is the law;
the sandbox is how an *engine* (model, browser, OCR, CLI tool) is held to it even though it is
foreign code.

## 1. The sandbox profile

A capability may carry a sandbox **profile** in its caveats (`caveats["sandbox"]`):

```text
sandbox {
  effects:  [EffectName]?     // allowlist — if present, only these effects may run
  network:  bool             // may the handler reach the network?  (default true)
  fs_read:  [PathPrefix]?     // if present, reads must be within these prefixes
  fs_write: [PathPrefix]?     // if present, writes must be within these prefixes
  // durable additions: cpu/mem/time budgets, syscall set, device access, env, dns
}
```

What an effect **needs** is the union of a static per-effect map (`browser → network`,
`shell → process`) and the capability's own `impl["requires"]` (e.g. a forged tool declares
`["network"]` or `["fs_read"]`). The durable form derives `needs` from the effect's declared
`effect_class` (WEFT §6: PURE / READ / REVERSIBLE_WRITE / IRREVERSIBLE / COMMUNICATION /
FINANCIAL). A falsy profile is unrestricted in the **reference**; in production the default is
**deny**, and a principal is granted exactly the profile its task requires (attenuated downhill
like any capability — a child's sandbox is never broader than its parent's).

## 2. Enforcement model

Enforcement is at the **executor contract boundary, before dispatch** (`executor.enforce_sandbox`,
called by `execute()`): an effect whose `needs` exceed the profile — a network-denied principal
reaching the network, an fs access outside scope, an effect not in the allowlist — raises
`SandboxViolation` and **never runs**. `kernel.invoke` records the blocked attempt as a **FAILED
EffectReceipt** (status FAILED, `error.code = "sandbox"`) on the signed Weft, so the refusal is
auditable, then surfaces it as a denial. Nothing reached the world (definite no-effect).

This is **policy enforcement at the boundary** — correct and testable, but it trusts the
in-process handler not to bypass it. That is the seam: the same `(profile, effect, args)`
contract drives real OS/VM isolation in the durable build (§3). The boundary check stays as the
cheap first gate even when OS enforcement is present (defense in depth).

## 3. Durable enforcement (the real form)

The reference enforces in-process; the durable build makes the profile **un-bypassable** by
running each engine as an OS- or VM-isolated principal, mapping the profile to mechanism:

| Profile element | Linux mechanism | WASM-component form |
|---|---|---|
| `effects` allowlist, capability scoping | seccomp-bpf syscall filter; capability tokens | component imports — the engine can only call host functions it was given |
| `network` | network namespace (no veth = no network) | no WASI socket import granted |
| `fs_read` / `fs_write` scope | mount namespace + **landlock** path rules; read-only binds | WASI preopens — only the granted dirs are visible |
| cpu/mem/time budgets | cgroups v2 | fuel/epoch limits + memory caps |
| heavy/untrusted isolation | **Firecracker** microVM (e.g. malware detonation, browser) | a fresh instance per invocation |

The **WASM component model** is the cleanest fit for "a swappable engine behind a Decima-owned
contract as a sandboxed principal": the component declares its imports, the host grants only the
ones the profile allows, and isolation is structural rather than policed. Linux primitives
(namespaces/cgroups/seccomp/landlock, user namespaces) cover native engines and the appliance;
Firecracker covers the highest-blast-radius cases (`CAPABILITY_MAP` Part C: malware detonation).

## 4. Relationship to the rest of the kernel

- **ocap is unchanged and primary.** The principal still holds only its envelope; the sandbox is
  a *second* gate after authorization, not a replacement. A held capability + an over-reaching
  handler = blocked by the sandbox.
- **Attenuation applies to profiles.** A delegated capability's sandbox profile is intersected
  with (never broader than) the granter's — downhill, like every caveat.
- **Morta still gates outward/irreversible effects.** The sandbox bounds footprint; Morta gates
  the act. A network-allowed, approved `publish` still needs Morta approval to *send*.
- **Every refusal is on the Weft.** A `SandboxViolation` is a FAILED receipt with provenance — a
  blocked engine is auditable, and a pattern of violations is itself a security signal (DET1).

## 5. Invariants to test

- An in-profile effect runs; an out-of-profile effect (network-denied, fs out of scope, not in
  the allowlist) is **refused before the handler runs** (the handler is never called).
- A refusal records a FAILED receipt (`error.code = "sandbox"`) and no world effect occurs.
- A None/absent profile is unrestricted in the reference (back-compatible) — and the durable
  build flips that default to deny.
- A child's sandbox profile is never broader than its parent's (attenuation).
- The sandbox composes with, and does not weaken, ocap or Morta gates.
