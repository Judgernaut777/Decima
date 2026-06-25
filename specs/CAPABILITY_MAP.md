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

Derived from scanning awesome-selfhosted, awesome-python, awesome-go, awesome-scalability,
sindresorhus/awesome, github topics/awesome, and ruvnet/RuView. Selective and Decima-relevant:
categories of capability, not exhaustive tool dumps. License flags are best-effort and must be
re-verified per component at wrap time; the safe default for copyleft (GPL/AGPL) is
**wrap-as-external-engine** (invoke a sandboxed subprocess; don't link/port source).

### Two laws every catalog item obeys (the scan kept surfacing them)
- **Morta-gate the outward/irreversible verbs uniformly, at the *effect*** (so the gate fires even
  inside automations): send mail/message, post publicly, move money, actuate a device, deploy,
  delete, open remote access, export a secret, egress data to a hosted model, promote a Nona skill.
- **Every inbound channel is untrusted data, never instructions:** incoming mail/chat/tickets,
  fetched pages/RSS, imported docs/media, OCR/ASR output, tool/scan output, RAG-retrieved text,
  sensor readings, model output itself. Enforce by **data-typing** (`instruction_eligible=false`),
  not prompt hygiene.

### Architecture rule from the self-hosted scan: wrap engines & formats, not apps
Most self-hosted projects are full-stack apps (own DB + auth + web UI) — exactly what LOOM / Weave /
Morta / Workspace already provide; wrapping them re-introduces ambient authority. The portable assets
are **(i) open formats** (iCalendar, vCard, EPUB, Markdown+wikilinks, OpenDocument, GEDCOM, OPML,
STIX), **(ii) protocols** (CalDAV/CardDAV, IMAP/SMTP, ActivityPub, XMPP, WireGuard, OIDC), and
**(iii) heavy compute engines** (FFmpeg, Tesseract, Meilisearch, llama.cpp, OSRM, Prometheus).
Three "apps" are actually **substrate, not features**: password/secrets mgmt = the **ocap vault**
`[kernel]`; automation/workflow = the **executor + scheduler** `[kernel]`; identity/SSO = **capability
issuance** `[kernel]`.

## B1 — Personal-OS domains (subsume the apps; built-in unless noted)

| Domain | Decima capability | Tag | Wrap/port candidates (license) | Morta / untrusted notes |
|---|---|---|---|---|
| Notes / knowledge | notes as Cells; the Weave *is* the knowledge graph | [effect]+[worker] index | Outline (BSD); Markdown+wikilink *format* (Trilium/Joplin AGPL = study) | imported clips untrusted |
| Files / sync | content-addressed blobs as Cells; sync = fold-replication | [worker]+[effect] | **Syncthing block-exchange (MPL)**; Filebrowser (Apache) | external sync = Morta |
| Calendar / contacts | events/people as Cells; CalDAV/CardDAV as export | [effect]+[worker] | **SabreDAV (MIT)**; iCalendar/vCard | invites/writes = Morta; .ics untrusted |
| Email | messages as Cells; MTA/IMAP decomposed | [effect] send +[worker] transport | OpenSMTPD (ISC), chasquid (Apache), Dovecot (MIT/LGPL) | **send = canonical Morta gate; inbound = canonical injection channel** |
| Feeds / read-later / archive | subscriptions + snapshots as Cells | [worker]+[skill] | **Wallabag (MIT), ArchiveBox (MIT)** | fetched pages untrusted |
| Bookmarks | links+tags+archive as Cells | [effect] | linkding (MIT), Shaarli (Zlib) | imported metadata untrusted |
| Tasks / projects | tasks/issues as Cells; kanban = projection | [effect] | OpenTodo (MIT); (Vikunja GPL = study) | local; planning is the orchestrator's home |
| Secrets / passwords | **the ocap vault** — secrets are capabilities | [kernel] | libsodium/age crypto (permissive); KeePass/Bitwarden *format* (study) | **export/reveal = Morta + audit**; workers get scoped caps, never raw secrets |
| Identity / SSO | OIDC/SSO = capability issuance | [kernel]+[worker] | **Keycloak / Authelia / Ory (Apache)** | external grant = explicit attenuated cap mint on the Weft |
| Dashboards | the Shell home; widgets = projections | [effect] | Homer/Dashy (Apache/MIT) layout refs | external-data widgets render as data |
| Documents / OCR | scanned docs ingested, classified, searchable | [worker] | **Tesseract (Apache), ocrmypdf (MPL)** + A1 OCR worker | ingested docs untrusted |
| Search | full-text + semantic index over all Cells | [worker] | **Meilisearch (MIT), Tantivy (Apache, Rust-later)** | results carry provenance (RAG boundary) |
| Media / photos | library + transcode + stream; face/scene tags | [worker]+[effect] | **FFmpeg (LGPL build)**, Lychee (MIT) ref; ML via router | external streaming/sharing = Morta |
| Office / docs editing | create/edit docs/sheets/slides as Cells | [worker]+[effect] | ODF/OOXML libs; (OnlyOffice AGPL = study) | external publish/share = Morta |
| Automation / workflow | **native LOOM** — triggers→capability chains | [kernel] | pattern refs Huginn/StackStorm/Kestra (MIT/Apache) | per-effect Morta even inside automations; untrusted triggers can't escalate |
| Messaging / notifications | chat/push/federation as message Cells | [worker]+[effect] | **ntfy/Gotify, Prosody (MIT), FreeSWITCH (MPL)**; ActivityPub *protocol* | **outbound/public post = Morta; all inbound = injection** |
| Finance / budgeting | accounts/transactions/budgets as Cells | [effect]+[worker] | **Actual (MIT)**; ccxt/OpenBB (MIT); (Firefly AGPL = study) | **money movement = strong Morta; irreversible** |
| Home / IoT | devices as Cells, scenes as automations | [worker]+[effect] | **Home Assistant (Apache), Matter/MQTT** | **physical actuation = Morta; telemetry untrusted** |
| Health / maps / CRM / commerce / learning / etc. | per-domain Cell types + model-router insight | [skill]→[effect] | Actual/Corteza/OSRM/Anki-format (permissive); live MCP: Era, Shopify, Canva, Gmail, GCal, Drive, Slack | sensitive PII sealed; outward comms/orders = Morta |
| GenAI / local inference | local model serving = the router's backend | [worker]/[kernel] | **llama.cpp / vLLM / Ollama (MIT/Apache)** | local inference = privacy win; hosted egress = data effect |

## B2 — Engine / building-block layers (mostly `[worker]`/`[kernel]`; Python-now → Rust-later noted)

- **HTTP ingress / outbound fetch** — httpx/aiohttp (now), granian/hyper (Rust-later). Outbound is a *gated egress capability* with target allowlist; inbound bodies are data.
- **API / RPC surface** — FastAPI/Litestar/Starlette (MIT); grpc/connect → **tonic** (Rust-later) for internal worker↔kernel RPC. Each endpoint = a capability invocation; authority from the caller's token, never process identity.
- **Task queues / durable execution** — RQ/huey/Celery (now); **Temporal** (MIT, Go/Rust-later) — its event-sourced determinism mirrors the Weft; a port could share substrate. A scheduled job is a *future authority grant* — fix its capability set at enqueue, Morta-review.
- **Persistence / stores** — DuckDB/SQLite (now); **redb/sled** (Rust) or Go **badger/pebble** (LSM, append-only — matches Weft) for the durable Weft; **immudb/dolt** (versioned) are conceptually adjacent. DB handle = scoped capability; model-emitted SQL parameterized only; DDL = Morta.
- **Caching** — derived projections only, always re-foldable from Weft; per-principal namespacing to prevent cross-tenant poisoning. (cachetools/diskcache now; moka/ristretto Rust/Go-later.)
- **Search / vector** — Tantivy (Rust), Qdrant/LanceDB (Apache, Rust) as derivative indexes; retrieved chunks are data with provenance (the RAG injection boundary lives here).
- **Parsing (HTML/XML/PDF/office/markdown)** — **highest untrusted-input attack surface**: run parsers as confined principals; XML entity expansion off, YAML `safe_load` only, no `pickle` ever; **markitdown/docling/kreuzberg (MIT)** to normalize everything → one Cell format.
- **Scraping / browser automation** — scrapy/crawl4ai (now), **Playwright (Apache)**, browser-use (MIT); the browser is a major Morta surface (any click/submit/buy) and must run in a container/VM (page JS attacks the driver); credentials via scoped capability, never the prompt.
- **Crypto & secrets** `[kernel]` — PyNaCl/cryptography (now) → **RustCrypto/ed25519-dalek** (the natural home for Weft signing); secrets via **OpenBao (MPL)**, not Vault (BUSL). Capability tokens as signed, attenuable **macaroons** realize ocap on the bus without central ACLs.
- **Serialization** `[kernel]` — deterministic/canonical codec for *signed* Cells (canonical CBOR/msgpack); msgspec/orjson at the edge as the validation firewall; serde/Cap'n Proto for a port-stable cross-language Cell format.
- **Concurrency** `[kernel]` — anyio/trio structured concurrency now → **tokio** later; structured concurrency = bounded authority lifetimes (a task can't outlive its capability scope) + deterministic cancellation for replay.
- **Sandboxing / isolation** `[kernel]` — **the linchpin of no-ambient-authority.** Interim: bubblewrap/nsjail/seccomp/landlock. Real answer: **WASM component model (Wasmtime/Wasmer)** — the cleanest realization of "swappable engine behind a Decima contract as a sandboxed principal," plus **gVisor/Firecracker** for heavier isolation.
- **Self-verification** `[skill]` (Nona) — pytest + **Hypothesis** (property-based) + ruff/mypy + **bandit**; fuzz the untrusted-input parsers. This *is* Nona's quality firewall before a forged skill is promoted.

## B3 — Kernel/scale patterns + SRE ops (from awesome-scalability)

**Kernel/substrate `[kernel]` (validate/extend LOOM):** event-sourcing+CQRS *(is* LOOM — adopt multiple specialized Weave projections); WAL/LSM persistence (RocksDB/Pebble/Litestream); **a CRDT-type registry + version vectors** (formalize the merge layer; declare each Cell's lattice); **Merkle-DAG the Weft** (O(log n) sync diff by root-hash descent); **gossip + anti-entropy** (generalize two-Weft sync to N nodes — the path to networked sync at scale; memberlist/serf MPL); **consensus only at the edges** (snapshot-of-record, capability-revocation ordering, singleton election — etcd; keep it off the append hot path); incremental snapshots; sharding/consistent-hashing by Cell-space; **effectively-once for INVOKE/receipts** (dedup-key + receipt log; content-addressing already makes asserts idempotent); MVCC snapshot-isolated reads; backpressure / rate-limit / circuit-breaker / bulkhead wrapping every outward INVOKE; CDC (the Weft is a native change stream — subscribe to build projections without re-folding from genesis).

**Ops capabilities & SRE skills:** observability — metrics (**Prometheus/VictoriaMetrics, Apache**), tracing (**OpenTelemetry/Jaeger** — one trace = one INVOKE causal chain over the Weft DAG), structured logs (**Vector, MPL**); dashboards (**Perses, Apache** — avoid Grafana/Loki/Tempo **AGPL**); health/failover & membership (etcd/serf); autoscaling (KEDA) of agent/worker pools on fold backlog; load balancing (Envoy/NATS); **deploy: blue-green/canary (Argo Rollouts) — Morta-gated**; **chaos engineering (Chaos Mesh)** — fault-inject sandboxed engines to prove CRDT convergence + self-heal; incident response, capacity planning, continuous profiling (Parca/Pyroscope); **backup/DR — replicate Weft+snapshots (Litestream/restic), Morta-gated egress; restore = replay to frontier.** Each becomes an SRE skill Nona can forge ("instrument-and-alert", "safe-rollout", "incident-commander", "chaos-experiments", "capacity-forecast").

## B4 — Additional capability domains (breadth; mostly `[worker]`/`[skill]`)

- **Voice / speech** `[effect]` (core I/O) — whisper.cpp (MIT) STT, **Piper (MIT)** TTS, wake-word/speaker-ID; transcribed audio is a *proposal*, never a kernel verb; speech out = Morta (leaves the box). (Pairs with the existing voice-runtime donors: Pipecat.)
- **Audio/music, video pipeline, image/creative/design** `[worker]` — librosa/sox, **FFmpeg** (sandbox decoders — codec CVEs), ImageMagick/Pillow/resvg; outputs are Weave artifacts; publish/send = Morta. Live MCP: Canva.
- **Geospatial / maps** `[worker]+[effect]` — GDAL, **OSRM (BSD)**, OSM data (ODbL); local routing = worker, live geocoding = gated egress.
- **Data science / viz** `[worker]` — **Polars/DuckDB (MIT)**, Vega-Lite/Matplotlib; sandbox notebook/formula eval (injection).
- **3D/CAD, gaming/sim, science compute** `[worker]/[skill]` — Blender/OpenSCAD (GPL → external process), trimesh/build123d (permissive); **Gymnasium/Godot/Bevy** as a safe training/eval substrate for Nona-forged skills; SciPy/RDKit/Biopython for research.
- **Robotics / IoT / embedded** `[effect]` — **ROS 2 (Apache), Matter, MicroPython/TinyGo/ESP-IDF**; physical actuation & device flashing are irreversible → Morta; signed-firmware ties to Nona promotion + Weft signing.
- **NLP / LLM tooling, knowledge graphs** `[worker]` — sentence-transformers, **DSPy (MIT)**, Ragas evals; **Oxigraph/kuzu (MIT)** graph stores; extracted triples are *untrusted assertions* — provenance/sign before they become facts Decima acts on.
- **Web3 / finance execution** `[effect]` — ethers/web3.py (read = worker); **key-signing / tx broadcast = Morta** (crown-jewel keys, never ambient) — same hardened path as money movement.
- **Accessibility** `[worker]` — axe-core (MPL) audit; output-shaping (captions, alt-text, screen-reader) on the projection layer.
- **RuView (ruvnet, MIT)** — WiFi-CSI **ambient sensing** (presence/vitals/pose/fall, no camera) → an optional `[effect]` sensor engine behind a contract (readings are untrusted data; *reactions* are the gated effects). More valuable as **architecture prior-art**: Ed25519 **witness-chains** (edge append-only signed log ≈ Weft), a **signed-module catalog** (≈ Nona's promote/verify), MCP-exposed sensing (≈ contract boundary). Adopt the patterns even if we never ship the hardware; flag its self-reported accuracy numbers as unverified.

---

# Part C — Cybersecurity: Decima as blue-team AND red-team (flagship)

Design-level taxonomy from Awesome-Hacking + the-book-of-secret-knowledge (+ Red-Teaming-Toolkit,
awesome-threat-intelligence, -incident-response, -cybersecurity-blueteam). For the owner's
**authorized** use: pentest engagements, CTFs, defending one's own systems, homelab SOC, research.

**Why Decima's kernel makes a powerful offensive capability *governable* (the whole point):**

| LOOM primitive | Security-platform role |
|---|---|
| Capability + caveats (ocap) | **Authorization-as-capability** — an engagement is one attenuated cap scoped to in-scope CIDRs/domains, time-boxed, rate-limited. No ambient authority ⇒ an engine literally cannot touch an out-of-scope host (it holds no capability for it). |
| Morta gate | **Go/no-go + kill-switch** — every outward/destructive action (packet out, exploit fired, account locked, host quarantined) carries an unstrippable human-approval caveat; revoking the engagement cap kills live sessions/beacons. |
| Append-only signed Weft | **Tamper-evident SIEM + forensic chain-of-custody + engagement audit log** — one primitive, three jobs; analyst-trustworthy provenance the operating agent cannot rewrite. |
| Untrusted-input = DATA | **Structural prompt-injection immunity** for scan output, captured traffic, target responses, malware strings, honeypot captures. |
| Sandboxed principals | Safe scanning **and malware detonation** under namespaces/seccomp/landlock with only needed caps. |
| Nona forge + test-gate | **Detection-as-code** — Sigma/YARA/Suricata rules & SOAR playbooks can't promote without passing TP/FP tests; a **purple-team loop** where red-team evasions auto-generate blue-team test cases. |

**RED-TEAM (`recon → reporting`)** — each function is a sandboxed `[worker]` tool + a Nona `[skill]`
methodology; the *outward* action is Morta-gated under a valid engagement capability:
recon/OSINT (Amass/SpiderFoot MIT), scanning/enum (nmap/masscan **GPL→wrap**; gobuster Apache),
vuln assessment (**Nuclei MIT** — templates ≈ forged skills; OpenVAS GPL→wrap), exploitation
(**Metasploit → wrap-as-external; every launch individually Morta-gated**; a shell session = a held,
revocable capability Cell), web-app testing (**ZAP Apache**, ffuf MIT; sqlmap GPL→wrap), credential
attacks (**Hashcat MIT** offline = low-gate; online spraying = Morta + lockout-aware rate caveats),
wireless (Aircrack/Kismet GPL→wrap; capture-only default), C2 (**Sliver GPL→wrap**; listener +
each implant task Morta-gated; engagement time-box auto-expires C2), post-ex/privesc (PEASS-ng
MIT; escalation action gated, scoped to the session cap), lateral movement (**Impacket Apache**;
each new host must be in-scope + gated), payload/implant gen (Donut BSD; build local, **delivery**
gated, every artifact tracked on the Weft for clean-up), evasion (feeds the blue detection-eng
purple loop), social-engineering (**Gophish MIT**; send = Morta, recipient-domain scope caveat,
PII sealed), reporting (**the signed Weft IS the evidence chain**; report = projection, export gated).

**BLUE-TEAM (`SIEM → CSPM`)** — mostly inward/read; Morta gates the *response* (quarantine, disable,
block, takedown):
SIEM/log pipeline (**the Weft is a natural tamper-evident SIEM backbone**; Vector MPL collectors;
Wazuh/OpenSearch wrap), SOAR (**playbooks = Nona test-gated skills**; auto-triage free, auto-*response*
Morta-gated to prevent automation self-DoS), IDS/NSM (**Zeek BSD** rich logging, Suricata GPL→wrap;
IPS *blocking* = Morta), EDR/host (**osquery Apache** SQL-over-host — excellent fit; remote actions
gated), threat intel (**OpenCTI Apache** STIX-native; IOCs as provenance Cells; sharing outward gated),
threat hunting (**hunts = forged skills** over Weave telemetry; Hayabusa/Chainsaw wrap), **detection
engineering (the flagship native-fit: Nona forges Sigma/YARA/Suricata rules; the test-gate is the
detection's unit test; purple loop turns red evasions into FP/TP fixtures)**, DFIR (**chain-of-custody
is the Weft's home turf**; Volatility 3/Plaso/Timesketch Apache; acquisition of live hosts gated to
avoid spoliation), malware analysis (**detonation = canonical sandboxed-principal use**; Ghidra/YARA/
CyberChef Apache/BSD; sample gets no outward net; results auto-forge candidate YARA rules), vuln mgmt
(Trivy Apache; authenticated scans use sealed cred caps), hardening/CIS (**audit read-only safe;
*applying* remediation = Morta** — a bad hardening change locks you out), honeypots/deception
(**captured attacker input is DATA by design**; OpenCanary BSD; sacrificial principal, no real-asset
reach), cloud posture (**Prowler Apache**; read-only scoped cloud cap can't mutate even if it tried).

**Wrap discipline:** copyleft (nmap, Metasploit, sqlmap, Suricata, Wazuh, MISP, Hydra, Aircrack,
Sliver, OpenVAS, Hayabusa/Chainsaw, Cuckoo, Lynis, ScoutSuite, Cowrie) → **wrap-as-external-engine**;
permissive (Nuclei, ZAP, ffuf, Hashcat, Impacket, PEASS-ng, Gophish, osquery, Zeek, Prowler,
Volatility, YARA, Sigma, Ghidra, CyberChef, OpenCTI) → portable; payload/knowledge corpora (SecLists,
GTFOBins, PayloadsAllTheThings, ATT&CK, CIS Benchmarks) → **study/DATA-only, never auto-run**.

---

# Part D — Banked product ideas (yours, captured)

## D1. The Constellation — a Skyrim-style skill tree for the skills library `[feature]`
Nona's accreting capabilities already *form a tree*: each forged skill has lineage (what it was built
from), prerequisites (capabilities it composes), and a promotion state (quarantined → test-gated →
promoted → in-use). Render that as the end product's **Constellation** — a Skyrim-style star-map skill
tree in the Shell: capabilities are stars grouped into domain constellations (creative, security,
dev, comms, sysadmin…), prerequisite/lineage edges draw the branches, a star lights up when Nona
promotes it, and "perks down a branch" map to composite skills. It's not just cosmetic: it's an honest
**projection over the Weave** of what Decima can do and how it got there (provenance you can fly
through), and a natural surface for the **Capability Inspector** (A2) and for *approving* a promotion
(Morta) by "unlocking" a node. Placement: Shell (a Weave projection) + Nona (the tree's data) + Morta
(unlock = approval). Build target: post-Rust-port GUI; the data model (skills + lineage + state as
Cells) exists now, so the tree can be a text/graph view immediately and get gorgeous later.

## D2. Agent shorthand / interlingua + token-compression codec `[kernel]`/`[skill]` (research)
A compact internal encoding for agent↔agent communication and data, to cut token cost and tighten
coordination. Strong fit, with guardrails: Cells are already **content-addressed**, so the cheapest
"shorthand" is agents referencing **Cell IDs / capability IDs** instead of re-sending payloads — a
pointer language over the Weave. On top of that, a learned/forged **symbol dictionary** (frequent
concepts/ops → short codes, Nona-promoted like any skill) and a deterministic **codec** (e.g. canonical
CBOR + a shared dictionary) compress inter-agent messages and memory.
**Guardrails (non-negotiable):** it is a *transport/representation optimization over the canonical
Weft*, never a second source of truth — every shorthand message must be **deterministically decodable,
logged decoded on the Weft, and auditable**; the dictionary is versioned and signed (a Cell); and a
compact inbound message from another agent is still **untrusted data** until authorized. The thing to
*avoid* is an opaque, drifting private agent language that defeats provenance — keep it transparent and
reversible. Placement: kernel (canonical codec + Cell-ref addressing) + Nona (the evolving, signed
symbol dictionary as a promotable skill) + an eval that proves round-trip fidelity and measures the
token saving before promotion.

---

## How this feeds the build
Nothing here is a cycle yet — it's the scope catalog. Items graduate into `docs/BACKLOG.md` a few at a
time, each as an `[effect]`/`[worker]`/`[skill]`/`[kernel]` lane behind a Decima contract, with the
two laws (Morta-gate outward; untrusted-in = data) and the wrap/port/study license discipline applied.
High-leverage near-term threads the scan surfaced: the **WASM-component sandbox** (makes "engine as
sandboxed principal" real), **Merkle-DAG + gossip sync** (networked sync at scale), **detection-as-code
via Nona** (the security flagship's cheapest win), and **voice + the Constellation** (what makes it
feel like a livable OS).
