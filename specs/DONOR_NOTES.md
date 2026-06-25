# Donor Deep-Dive Notes

Companion to [`DONOR_MATRIX.md`](DONOR_MATRIX.md). The matrix is the one-line disposition;
this file is the **file-level review** the matrix says every component needs — for each
donor: what it actually is, the exact license reality, **what we can use directly**, **what
we cannot use for legal reasons but is worth emulating clean-room**, the Decima mapping, and
the risks. License triage here is engineering judgment, not legal advice.

The recurring finding across the "AI-OS" donors (CX, Ubuntu Zombie) is a counter-example that
*validates Decima's thesis*: they all implement natural-language→operations as **ambient-
authority shell execution behind a single, often-bypassable confirmation prompt**. Decima's
inversion — the model only *proposes* INVOKEs, object-capabilities authorize, Morta gates
irreversible effects, and a signed append-only Weft is the audit trail — is exactly the part
they get wrong. Port their mechanics; reject their trust architecture.

---

## Baidu Unlimited-OCR — OCR engine (Wrap)

**What it is.** A thin client + serving harness around a **DeepSeek-OCR-family vision-language
model** (Baidu weights). `infer.py` boots a bundled **SGLang** server (custom prerelease wheel),
polls `/health`, and fans out concurrent streaming OpenAI-compatible requests; PDFs are
rasterized at 300 DPI (PyMuPDF) and parsed as long-horizon documents (≤32K tokens) with an
n-gram repetition suppressor. The real model lives off-repo on Hugging Face as `trust_remote_code`
custom code. Hard NVIDIA/CUDA (FlashAttention-3, bf16) — a GPU-server engine, **not** an appliance component.

**License.** Repo code **MIT** (© 2026 Baidu); bundled SGLang wheel **Apache-2.0** (but ships
`.pyc`-only, no NOTICE — pull SGLang from upstream source instead). **Model weights: license
UNCONFIRMED** — not in the repo; DeepSeek-OCR/PaddleOCR lineage. **Treat weights as study-only
until the HF/ModelScope model card is verified.**

**Use directly.** The MIT orchestration glue in `infer.py` — engine-subprocess lifecycle,
`/health` polling, concurrent streaming with retry/backoff, PDF→image rasterization, base64
data-URL encoding. SGLang itself as a sandboxed inference engine (from upstream).

**Emulate / defer.** The *design targets* are clean-room-worth regardless of weights: one-shot
long-horizon document parsing (whole multi-page doc as one decode, preserving tables/reading
order), vision-token-compression OCR, and decode-time n-gram repetition suppression. If the
weights prove unusable, source a permissive OCR model behind the same contract.

**Decima mapping.** A **visual/document-ingestion worker** behind an `ocr.transcribe(blob_ref,
…) -> TranscriptCell[]` contract feeding Weave ingestion. The engine is a sandboxed principal
holding only "read this blob, write text Cells" — no ambient FS/network. **Every OCR Cell is
asserted `instruction_eligible=false`**: a scanned page is recallable DATA, never an instruction.

**Risks.** Weights license (gating); `trust_remote_code=True` = ambient code execution (use the
SGLang serving path, never load remote code in-process); opaque binary wheel (supply chain);
**OCR output is an injection vector** — enforce `instruction_eligible=false` at the boundary;
GPU/Hopper-class, slow startup — keep off the appliance hot path.

---

## Google Fuchsia / FuchsiAware — capability model (Inspiration; small Apache code)

**What it is.** A ~1,100-LOC VS Code extension (`src/provider.ts`) that turns Fuchsia component
URLs (`fuchsia-pkg://…#meta/x.cm`) into bidirectional links to component manifests. It does **not**
parse capability semantics — it reconstructs a name→manifest index by scraping the Ninja build
graph (fragile regexes the author disowns) + `git grep` for references. The deeper donor value is
what it points at: **Fuchsia's component/capability OS**.

**License.** **Apache-2.0** (extension; © Google). Permissive, patent grant. Portable with attribution.

**Use directly.** The small UX/index layer (Apache): the bidirectional `name ↔ artifact ↔
references` map, the "provider trinity" (document-links + terminal-links + find-references from one
index), and URL-linkification of IDs in terminal/log output. **Do NOT port the build-graph
scraping** — Decima has the Weft as authoritative ground truth and never reverse-engineers.

**Emulate clean-room (the real value).** Fuchsia is a shipping, capability-secure OS that embodies
Decima's stance — **no ambient authority; every capability is declared in a manifest and explicitly
routed parent→child**. Adopt the semantics: per-Cell capability manifest (`use`/`offer`/`expose`),
parent-mediated downhill routing (delegation = attenuate + offer), **monikers** (stable instance
identity = position in the Weave fold), and **compile/validate-before-grant** (a natural Morta
checkpoint). Headline emulation: a **capability inspector** Shell view — "show all holders of this
capability + the full delegation chain" — built as an *exact* fold over the Weft (never heuristic).

**Risks.** FuchsiAware is a nav tool, not the OS — don't overweight its code; Fuchsia proper is a
large separate study; its fuzzy name-matching is fine for nav but catastrophic for a capability
inspector (Decima's must be exact); VS Code coupling. Don't conflate Fuchsia (from-scratch
microkernel) with Decima's form factor (adopt Linux, don't write a kernel).

---

## CX Linux `cx-core` — AI terminal (STUDY-ONLY; use upstream WezTerm)

**What it is.** A **WezTerm fork** with a thin AI layer. `cx ask` does **keyword/regex pattern
matching** (confidence ≥ 0.7) → fixed `cx` subcommands, falling back to a provider chain
(daemon → local GGUF → Claude via `curl` → Ollama); results run via `sh -c` behind a Y/n prompt
(`--yes` bypasses). In-terminal "agents" route by keyword and shell out; there is an
`AgentCapability` enum but `traits.rs` is `#[allow(dead_code)]` — **capabilities are declarative
metadata, not enforced**. `cx-daemon` is, despite the framing, just a monitoring/alerting daemon
with **no `ask` handler** — NL→ops executes client-side, not in a privileged broker. The AI delta
is shallow: no planner, no enforcement, no sandbox.

**License.** **BUSL-1.1** (Licensor: AI Venture Holdings LLC; Additional Use Grant: 1 personal
system / internal ops / education / contributing; **Change Date: 6 yrs/release → Apache-2.0**;
v0.3.2 converts 2032-01-15). "Commercial Use" — >1 system, competing product/service, managed
services, resale — is **forbidden**. Decima is a competing OS → **STUDY-ONLY** until conversion.
The CX-authored files (`cx-daemon/`, `cx/`, `wezterm-gui/src/{ai,agents,blocks}`, `cli/ask*.rs`)
are radioactive: clean-room only.

**Use directly (the real portable value is NOT cx).** **Upstream WezTerm (MIT)** — `portable-pty`,
`mux` (Pane/Tab/Window/Domain + notifications), `wezterm-mux-server`/`-client` (attach/detach over
a Unix socket), `term`/`termwiz` (emulation/surface). This is exactly Decima's session-multiplexing
substrate. **Pull from upstream `wez/wezterm`, not this fork** — the fork rewrites crate metadata
and its "MIT" crates may contain non-MIT modifications.

**Emulate clean-room.** The *good ideas* behind Decima contracts: a fast pattern layer that
short-circuits common intents to **structured ops before** falling to the LLM (but the op is an
INVOKE of a capability, never `sh -c`); the agent-capability *taxonomy* (Execute/ReadFile/Write/
PackageManage/ServiceManage/NetworkManage) as real object-capabilities with `require_confirmation`
→ Morta gate; the structured-model-output contract (model emits JSON ops + explicit `refusal`,
never free-form shell); command "blocks" (each run = a Cell with output + exit). Local-first
provider fallback for the appliance.

**Risks.** BUSL contamination; WezTerm-fork provenance (vendor from upstream only); single-vendor;
**NL→`sh -c` with unenforced capabilities + a single Y/n is the exact ambient-authority anti-pattern
Decima eliminates**; CX passes the API key on the `curl` argv (visible in `ps`) — use a header from a
held secret capability.

---

## CX Linux `cx-distro` — AI-shell distro build (STUDY-ONLY; standard tools usable)

**What it is.** Debian/Ubuntu distro engineering for CX Linux. **Doc/impl drift:** the README/Makefile
describe a clean Debian `live-build` pipeline with preseed/SBOM that **largely doesn't exist**; the
real `src/` is a **forked-Ubuntu GNOME desktop remaster** (debootstrap `minbase` → numbered chroot
mods `NN-*/install.sh` → `mksquashfs -comp zstd` → hand-rolled EFI+BIOS hybrid with Secure-Boot shim
→ `xorriso`). A signed apt repo (reprepro/`dpkg-scanpackages`, GPG InRelease, deb822 keyring pkg) on
GitHub Pages. **The "AI shell" is not booted** — it's a separate `cx-core` .deb (`/usr/bin/cx`,
firejail/apparmor at the app layer) that nothing autostarts.

**License.** **BSL-1.1** (same parameters as cx-core) → **STUDY-ONLY**. But the **standard tooling it
orchestrates is ours to use directly** (not CX IP): debootstrap, live-build, squashfs-tools, xorriso,
grub-efi/pc, reprepro, gnupg, shim/signed-grub, syft/cyclonedx, casper/live-boot.

**Emulate clean-room.** The recipe shape (debootstrap → chroot-customize → squashfs → EFI+BIOS hybrid
→ xorriso; multi-arch amd64 hybrid + arm64 EFI-only), the ordered "assembly steps" framework, the
signed-apt-repo trust model + deb822 keyring package, and `SOURCE_DATE_EPOCH` reproducibility.

**Decima mapping & the inversion.** Confirms the toolchain works for Decima's **minimal Linux appliance**
(VM/container/baremetal, x86_64+aarch64). But CX's architecture is the opposite of what we want: a
GNOME desktop with AI bolted on. **Decima inverts it — LOOM is the init target**: a `minbase` rootfs,
no desktop, boot → systemd → a `decima.target` that launches the Decima shell as the session. Bake the
sandbox primitives into the **image/kernel** (namespaces, cgroups v2, seccomp, **landlock**, userns),
not just app-layer firejail. Build subsystem (post-Rust-port) emits signed ISO + VM image + OCI from
one rootfs, with **real** unattended install and mandatory SBOM/signing.

**Risks.** BSL; vaporware gap (validate against `src/`, not the README); **supply-chain anti-patterns to
NOT copy** — `curl|sudo bash` upgrades, a placeholder *empty* keyring shipped when no signing key is
present, hard-coded key id, GitHub-Pages trust anchor. Decima needs end-to-end signing + verified update
bundles (no curl-pipe-bash).

---

## Ubuntu Zombie — AI sysadmin (closest prior art to Morta; MIT, port mechanics)

**What it is.** Adds a root-capable AI sysadmin to Ubuntu LTS. Three principals: the human operator
(key-only SSH), an unprivileged `zombie` account **holding passwordless sudo** that runs a loopback chat
service + the agent loop, and root. Loop (`payload/agent/server.py`): model emits **structured `tool_call`s**
(never free-form shell) over the pi-mono protocol → a **closed tool registry** (~14 tools) validates args →
a **policy classifier** (`policy.py`) tags risk (read_only < user < system < network < destructive) →
read-only runs inline, everything else **queues for human approval** (destructive requires typing an exact
phrase) → execute → **append-only JSONL audit** (`audit.py`) recording tool/class/decision/exit/duration +
SHA-256 of output (never raw) with a secret-redactor. A **TTL kill-switch** writes a durable `dead` tombstone
on expiry. SSH/UFW hardening; `.deb` stages files but mutates nothing until `install` is run.

**License.** **MIT** (© 2026 Eric Mourant). Portable with attribution (the bundled Node `pi-*` agent deps are
separate upstreams — not ported).

**Use directly (clean, dependency-free Python — ideal donor code).** The **audit record shape + secret
redactor + digest-not-payload discipline** (`audit.py`) — nearly a Weft effect-receipt already; the **argv-aware
shell risk classifier** (`policy.py`: top-level split, sudo/env stripping, pipeline-to-interpreter escalation,
`find -delete`) as a Morta risk-tagger; the **proposal/approval representation**; the closed-registry **schema
validator + path allow-list** (`_within`, symlink-resolving); the **TTL tombstone** (a time-bounded capability);
and the **hardening defaults** (SSH drop-in, UFW, loopback-only bind, 0600 secrets with fail-closed startup).

**Emulate but rebuild on Decima primitives.** Approvals → **unstrippable human-approval caveats on the
capability** (not an in-memory dict + `==` phrase check). Audit JSONL → **signed append-only Weft cells** (theirs
is locally mutable by the very account that holds root — no integrity). `tool_call`s → **LOOM INVOKE intents**
(logged Cells with provenance; keep their "AI-initiated vs human-initiated" distinction as a first-class field).
Root execution → an **attenuated, sandboxed principal** (namespaces/seccomp/landlock) holding only Morta-granted
caps — never ambient sudo. Policy YAML → **capability policy as data in the Weave** (keep live-reload ordered rules,
drop the brittle bespoke parser).

**Decima mapping.** policy+approval → **Morta**; audit → **Weft**; tool_call → **INVOKE**; the sudo account →
**sandboxed root-capable worker principal**; loopback chat → the **Decima Shell** operator/approval surface;
install/hardening → **appliance** defaults; TTL tombstone → built-in capability expiry.

**Risks (and a sharp cautionary tale).** **Ambient root, no sandbox — by design** (the project explicitly refuses
systemd confinement); the security boundary is a software check *in the process that holds root*. **The shipped
`payload/etc/policy.yaml` contradicts its own docs AND tests** — it sets `default_class: system_change` and
**auto-approves user/system/network changes** (only `destructive` gates), with budgets 128/32 vs the documented
12/3. So a default install lets the AI `apt install`, `systemctl restart`, edit `/etc`, and touch the firewall
**with no approval**. `shell.run` is an unbounded escape hatch (safety reduces to a regex denylist over shell).
The audit log is self-mutable. Prompt-injection reaches the root agent (it reads `/etc`, `/var/log`, file contents,
and echoes them back). **Lesson: port the mechanics, treat the safe *code* defaults as canonical, discard the
shipped policy, and never ship an in-process gate as the sole boundary.**

---

## Walturn "Best AI Operating Systems" survey — landscape (Inspiration; no code)

**What it is.** An editorial overview naming Steve, Google Fuchsia, Azure Sphere OS, IBM Watson OS,
Ubuntu AI, and Tesla's AI OS, and listing AI-OS design principles. No code; cite, don't copy.

**Worth keeping as a checklist.** The capabilities a credible AI OS is expected to have: a **unified
shared-memory layer for cross-agent collaboration**, an **agent marketplace/discovery** ecosystem,
**multi-agent orchestration**, **self-healing autonomy**, and real-time/edge/multimodal support. Useful
for positioning and as a gap-check against VISION — most map to existing Decima concepts (Weave/memory,
Nona's skill registry, the orchestrator, the self-improvement loop). **Risk:** marketing-depth only;
ideas need grounding in Decima primitives, not adoption as-is.

---

## Cross-cutting lessons

1. **Every shipped "AI OS" here runs NL→shell with ambient authority and weak/bypassable gating.** CX
   declares capabilities but doesn't enforce them; Ubuntu Zombie ships a policy that auto-approves system
   changes. This is the strongest external validation of Decima's design: **the model proposes, capabilities
   authorize, Morta gates, the Weft records — and the root worker is sandboxed, never ambient.**
2. **The audit trail must be unforgeable.** Both AI-sysadmin donors keep local, mutable logs writable by the
   privileged agent. Decima's signed, append-only Weft is precisely the fix — make it a selling point.
3. **Form factor: invert the desktop model.** Don't bolt AI onto a desktop distro; make LOOM the init target on
   a minbase image with sandbox primitives in the kernel. CX-distro is the worked toolchain example; the
   integration is ours to do.
4. **Capability model: Fuchsia is the proof.** A full capability-secure OS already runs on `use`/`offer`/`expose`
   + monikers + compile-before-grant. Adopt the semantics; build a capability-inspector over the Weft.
5. **Untrusted data discipline scales to new modalities.** OCR output (Unlimited-OCR) is the same untrusted-input
   law as web pages and tool output: recallable DATA, `instruction_eligible=false`, never obeyed.
