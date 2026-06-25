# Decima Capability Map

Decima is an operating system, so it should carry **a vast amount of built-in capability** —
and grow more (Nona). This is the catalog of what Decima can do and intends to do. It is a
scope/intake document, not an implementation plan; items graduate into cycles via
[`../docs/BACKLOG.md`](../docs/BACKLOG.md).

Every capability is one of four kinds, and all four obey the same laws (ocap authority, Morta
gates on irreversible effects, untrusted input is data never instructions, everything on the Weft):

- **`[effect]` built-in capability** — a kernel/executor effect invoked through a held capability.
- **`[worker]` wrapped engine** — an external engine (model, browser, DB, OCR…) run as a
  sandboxed principal behind a Decima-owned contract; swappable, never ambient.
- **`[skill]` skills-library entry** — a Nona-forged, test-gated, promotable capability (data, not
  kernel code). Most domain breadth lands here.
- **`[kernel]` refinement** — a change to the kernel/ocap substrate itself.

Licensing discipline (see [`DONOR_MATRIX.md`](DONOR_MATRIX.md) / [`DONOR_NOTES.md`](DONOR_NOTES.md)):
permissive code may be ported behind contracts; AGPL/FSL/BUSL/BSL/noncommercial/unclear are
study-only (reimplement clean-room). No donor owns a canonical Decima type.

---

# Part A — Adoptions from the donor deep-dive (the five)

What the last five donors concretely add to Decima, and — equally useful — what they get wrong,
which sharpens our version. Full analysis in [`DONOR_NOTES.md`](DONOR_NOTES.md).

## A1. Document/visual OCR — `ocr.transcribe` `[worker]`
- **Adds:** a document/image/screenshot OCR worker (Baidu Unlimited-OCR / DeepSeek-OCR-family via
  SGLang) behind `ocr.transcribe(blob_ref, mode, dpi) -> TranscriptCell[]`. Feeds Weave ingestion,
  the workspace ("import this PDF/scan"), and the visual/browser worker.
- **Decima placement:** executor (sandboxed GPU principal, off the appliance hot path), Weave ingestion.
- **Law:** every OCR Cell is `instruction_eligible=false` — a scanned page is recallable DATA, never obeyed.
- **Juxtaposition / reject:** `trust_remote_code` (ambient code-exec) — use the serving path; model
  **weights are license-unconfirmed** → study-only until verified; OCR output is an injection vector.

## A2. Capability Inspector + a sharper ocap model — `[feature]` + `[kernel]`
- **Adds (feature):** a **Capability Inspector** in the Shell — from any capability, show *every holder*
  and the *full delegation chain* (who granted it, attenuated how, to whom); from any Cell, show its
  capability surface. Built as an **exact fold over the Weft** (never heuristic). Plus: linkify
  capability/Cell/event IDs in logs and terminal output → click to jump to the grant/holder.
- **Adds (kernel refinements, from Fuchsia's shipping ocap OS):** adopt **`use`/`offer`/`expose`**
  routing semantics for a Cell's capability surface; **monikers** (stable instance identity =
  position in the Weave fold); **compile/validate-before-grant** (a Morta checkpoint that rejects a
  grant that isn't well-formed/attenuating *before* it enters the Weft).
- **Decima placement:** Shell, kernel ocap, Morta.
- **Juxtaposition / reject:** FuchsiAware reverse-engineers a build graph and fuzzy-matches names —
  fine for navigation, catastrophic for a security inspector. Decima has the Weft as ground truth;
  resolution must be exact.

## A3. Built-in terminals + session multiplexing — `[effect]` (engine: upstream WezTerm, MIT)
- **Adds:** real built-in terminals and **process/session Cells** (PTY, attach/detach, replay)
  using **upstream WezTerm** (`portable-pty`, `mux`, `wezterm-mux-server`) — MIT, pulled from
  `wez/wezterm`, not the cx fork. This is the engine for the D2 session work and "drop any CLI
  tool/agent into a terminal as a sandboxed principal."
- **Decima placement:** executor (terminal/pty effect), Shell (built-in terminals, command-blocks-as-Cells).
- **Pattern to emulate:** NL→**structured ops** fast path (pattern-match common intents to known ops
  *before* the LLM); the model emits proposed INVOKEs + explicit refusals, never free-form shell.
- **Juxtaposition / reject (cx-core, BUSL study-only):** NL→`sh -c` behind one Y/n, capabilities
  declared but `#[allow(dead_code)]` (unenforced), API key on the argv. The whole anti-pattern Decima
  inverts: model proposes, capabilities authorize, Morta gates, Weft records.

## A4. AI sysadmin — "Decima operates the machine" — `[effect]`/`[skill]` (flagship)
- **Adds:** Decima can diagnose/configure/repair/operate its own host — a **sandboxed, root-capable
  worker principal** holding only Morta-granted, attenuated capabilities per approved effect, with
  full signed receipts. (ubuntu-zombie, MIT — port the mechanics.)
- **Port directly (MIT):** the **audit record shape + secret redactor** → a Weft effect-receipt
  sanitizer; the **argv-aware shell risk classifier** → a Morta risk-tagger that picks which caveat a
  proposed effect needs; the **schema validator + symlink-resolving path allow-list** → capability-scoped
  fs effects; the **TTL kill-switch / durable tombstone** → a time-bounded capability; the **hardening
  defaults** (key-only SSH, UFW, loopback binds, 0600 secrets, fail-closed startup) → appliance defaults.
- **Decima placement:** executor (sandboxed sysadmin principal), Morta (approval caveats + risk classes),
  Weft (signed audit/provenance), Shell (operator chat/approval surface).
- **Juxtaposition / reject:** ambient root via passwordless sudo with **no sandbox by design**; a shipped
  `policy.yaml` that **auto-approves system/network changes** (contradicting its own docs/tests); a
  generic `shell.run` escape hatch; a **self-mutable** audit log. Decima's fixes are structural: no
  ambient sudo, OS-enforced sandbox (namespaces/seccomp/landlock), no generic shell effect, and a
  signed append-only Weft the agent cannot rewrite.

## A5. The Linux appliance build — `[form-factor]` (post-Rust-port phase)
- **Adds:** a concrete recipe for the **minimal Linux appliance that boots into the Decima shell** —
  debootstrap `minbase` → ordered assembly steps → `mksquashfs` → EFI+BIOS hybrid → `xorriso`; signed
  apt repo (reprepro, deb822 keyring); multi-arch (x86_64 hybrid + aarch64 EFI). Standard tooling is
  ours to use directly; the recipe is reimplemented clean-room (cx-distro is BSL study-only).
- **The inversion (the key lesson):** cx-distro bolts AI onto a GNOME desktop that never boots into it.
  Decima makes **LOOM the init target** — minbase, no desktop, boot → systemd → `decima.target` →
  the Decima shell as the session — with **sandbox primitives baked into the kernel/image**
  (namespaces, cgroups v2, seccomp, landlock, userns), not app-layer firejail.
- **Juxtaposition / reject:** `curl | sudo bash` upgrades, a placeholder *empty* signing keyring,
  GitHub-Pages trust anchor. Decima requires end-to-end signing + verified update bundles + SBOM.

## Cross-cutting takeaway
Every shipped "AI OS" we studied runs **natural-language → shell with ambient authority behind a
bypassable prompt**, with **local mutable audit logs**. That is external validation by counter-example:
Decima's **propose → authorize (ocap) → Morta-gate → signed-Weft-receipt**, with the privileged worker
**sandboxed not ambient**, is precisely the differentiator. Make it a stated product guarantee.

---

# Part B — Ecosystem capability catalog

> *(Incoming — derived from the awesome-* ecosystem scan: self-hosted app domains, Python/Go engine
> libraries, systems-design/scalability patterns, and the cybersecurity blue/red-team capability set.
> Built next; this section will group capabilities by domain and tag each feature / worker / skill.)*
