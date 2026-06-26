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

## D3. Sovereign access — auto-router · credential powerbox · private inference · gated payments `[feature]`/`[worker]`/`[kernel]`
The cluster that makes Decima both **easy to start** and **sovereign** — "give it a payment
method and it just works," while the sensitive/refused work stays on infra you control. Four
parts, all behind Decima contracts:

1. **Auto-router (token optimization + intelligent model switching)** `[skill]`/`[kernel]` —
   grow C1/C2 into a router that auto-switches per task on cost, latency, **privacy**, context
   size, reasoning need, modality, **and capability/refusal**, spending the frontier only when a
   cheap/local model can't do it; SH1's shorthand (D2) trims tokens on top. Token efficiency is
   a first-class objective, not an afterthought.
2. **Credential & billing powerbox (zero-setup access)** `[worker]`/`[kernel]` — the onboarding
   you described: give Decima a payment method and it provisions access for you — either a unified
   gateway (OpenRouter-style: one balance, many models) or by creating provider accounts on your
   behalf, each with a **per-service privacy email alias** (Apple Hide My Email / Proton /
   SimpleLogin) and strong unique credentials. Aliasing per service is a **security feature** —
   compartmentalization and breach isolation — and the credentials are held as **scoped,
   attenuable capabilities** via the powerbox (E1) + secrets broker, never ambient. Frictionless
   to start, sovereign in storage: this is the "platform capture through ease + security" path.
3. **Self-hosted / private inference (rent-a-GPU, open weights)** `[worker]` — for (a) **privacy**:
   route sensitive/private data to a model on the user's own infra, **no egress**; and (b)
   **authorized security work**, where hosted models routinely refuse *legitimate* red-team tasks —
   the user's compute + open weights + authorized use. The governance reframe that makes this
   safe: **a model's refusals are NOT Decima's safety layer — the kernel is.** Authorization-as-
   capability (scoped, time-boxed), Morta gates on every outward/irreversible/destructive effect,
   and the signed-Weft audit keep even an unfiltered local model governable. Routing around a
   *provider's* RLHF on legitimate authorized work is not bypassing Decima's policy; genuinely
   harmful/illegal requests are still refused by the **kernel**, regardless of which engine runs.
4. **Morta-gated payments rail — fiat + crypto, providers wrapped (never reinvented)** `[effect]`/`[kernel]`
   — programmatic capital access for agents: the canonical irreversible effect, and what *funds*
   parts 2–3. Hard spend caps, per-transaction human approval (or policy-bounded autonomy with
   explicit limits), credentials via the secrets broker, full provenance on the Weft. The detail:

   - **Decima already IS the zero-trust blueprint.** The standard agent-payment pattern decouples a
     non-deterministic Reasoning Layer from a deterministic Execution Layer via a **Policy-Guard
     Gateway** (hard limits + human-approval check) before any provider call. In Decima that gateway is
     **not a bolt-on proxy — it's the kernel**: the brain only *proposes* an INVOKE;
     `capability.authorize` (ocap) + Morta (`requires_approval` + spend-cap caveats) + the executor
     boundary are the deterministic guard; the signed Weft is the **state-attribution audit** — every
     payment receipt is causally linked to the exact INVOKE + AuthorizationProof that induced it
     (stronger than a side log: it's content-addressed and tamper-evident).
   - **Wrap providers; never build a processor (they absorb the liability).** The payment effect is a
     *contract* with pluggable real rails behind it; Decima holds a **scoped capability** and the
     **secrets broker** holds provider creds — Decima never touches a raw PAN or a private key:
     - **Fiat — Stripe Issuing for agents.** A purchase mints an **ephemeral, single-use virtual card**
       with hard `spending_limit` + `allowed_categories` (merchant lock) bound at creation — i.e. an
       **attenuated, single-use, auto-revoked capability** in ocap terms: *blast-radius reduction is
       the capability being the limit.* The real-time authorization webhook (the 2-second
       `approve:true/false`) is the synchronous **Morta gate** at the instant money moves. Restricted
       keys (`rk_`) scoped to `issuing_cards:write` / `authorizations:read` only.
     - **Crypto — Coinbase AgentKit / CDP.** Agent smart-contract wallets on Base, gasless USDC
       transfers, **time-locked wallets** (≤ N per epoch) — the same contract, a different rail.
       Key-signing / tx-broadcast = Morta (crown-jewel keys never ambient; per B4's Web3 line).
     - **Brokerage — wrap a regulated agentic broker (don't reinvent execution or custody).** Decima
       opens/funds an **isolated agentic sub-account** and trades through a wrapped broker rail —
       **Alpaca** / **Interactive Brokers** (developer API), **Robinhood Agentic Trading Account**,
       **Public.com**, **eToro**, **Coinbase for Agents**, **Bybit AI sub-accounts**. The industry's
       own risk controls map 1:1 onto Decima primitives: *isolated sub-account* = a scoped capability
       (its own envelope, never the main balance); *budget cap / max exposure* = a `budget` caveat;
       *kill switch* = **Morta revocation** (and CASCADE so derived authority dies with it); *manual
       review before execute* = the Morta approval gate; *paper-trading sandbox* = the SB1 sandbox /
       stub-rail before real money. **The brokerage is funded by the Stripe capital rail above** —
       money in via Issuing/ACH, trades out via the broker API, both Morta-gated `FINANCIAL` effects
       on the Weft. (Field validation: Morgan Stanley now opens external-agent access to its equity
       platforms — institutions are wiring agents into core financial systems; the wrap-don't-rebuild
       posture is exactly right.) Many of these expose **MCP** — Decima is the MCP *client*, the
       broker is a wrapped engine behind the contract.
     All rails sit behind **one `FINANCIAL` effect contract**; fiat-vs-crypto-vs-brokerage is a routing
     decision, agent-framework-agnostic (the gateway is the kernel, not LangChain/CrewAI).
   - **Operational braking.** A loop/token counter halts an autonomous loop after N tool calls with no
     definitive outcome (a loop-budget caveat); spend caps + org_policy + the live governance gate
     (LOOP1) bound autonomy; every prompt→tool→tx triple is attributable on the Weft.

   The current `payments.py` / `trading.py` are the **stub rail** proving this contract; making it real
   = wrapping Stripe Issuing + Coinbase AgentKit behind the same `pay()` interface (a make-a-stub-real
   depth task — the ephemeral-card-as-capability mapping is the reason it'll be a thin adapter, not a build).

**Why it matters:** ease (a card → instant access) + sovereignty (self-host the sensitive or
provider-refused work) = exactly the "easy to start, hard to leave, safe by construction" wedge.
**Placement:** model router, powerbox (E1), secrets broker, executor (payment + inference effects),
Morta. **Guardrails baked in:** privacy aliases yes / multi-account-ToS-evasion no; the kernel —
not model RLHF — is the governance boundary; payments hard-gated and audited.

## D4. Orientation, Disposition & the Wager/Verdict loop `[feature]`/`[skill]`/`[kernel]`
From the "OODA loop / Infinite Brain" analysis. Most of that piece describes the harness Decima
already *is* (a typed Cell graph, a tiered model router, governance, the observe→decide→act loop —
its 8 components map onto Decima Cell types). These are the parts genuinely **net-new** to us:

1. **Orientation — "the Big O"** `[feature]` — generic AI is strong at Observe/Decide/Act and weak
   at **Orientation**: the filter of the user's values, context, and constraints that *interprets*
   data before deciding ("fast noise" without it). Decima has the ingredients — profile memory, B4
   governance/rules, the agent `horizon` — but treats them implicitly. D4 names and assembles them
   into an explicit **Orientation lens** consulted before `decide`, so the agent acts from "who you
   are and what you value." (Boyd's real OODA is non-linear: a well-oriented agent acts reflexively
   on known patterns — the fast path — and deliberates only on novel ones.)
2. **Disposition** `[kernel]`/`[skill]` — make "what follows from an intake" first-class: an
   **Intake Event** (an observation ASSERT) resolves to a **disposition** — an INVOKE, a memory
   write, a task, or a policy update — with deterministic filtering (archive noise) split from model
   analysis. Tightens the ingestion→action path (the browser→memory ingestion is one slice of it).
3. **Wager / Verdict loop** `[skill]` — the headline net-new: the scientific method as Cells. Before
   a significant/irreversible action, record a **Wager** — a probabilistic prediction + confidence
   ("this change → +2% ROAS"); after, a **Verdict** measures the actual outcome; the hit/miss folds
   into learned policy and the router's calibration, refining Orientation over time. Receipts say
   *what happened*; the wager/verdict pair says *what we predicted vs. got* — the missing learning
   loop. It complements Nona (which learns *which capabilities* work) by learning *which decisions*
   work, and pairs directly with D3 (a trade or ad spend is a wager, verified against metrics).

**Placement:** brain/agent (Orientation lens), executor + memory (Intake→Disposition), and a
`wager`/`verdict` Cell pair with Morta gating significant wagers + a fold into org policy / router
calibration. **Honest note:** the source is marketing-flavored ("Infinite Brain"/Starmind) and
Decima already has the structural spine; D4 captures only the additive framing.

## D5. The autonomy ladder — per-capability autonomy levels `[kernel]`/`[feature]`
From the agent permission-ladder framing (mindstudio). An agent's autonomy is not a global on/off; it's
a **per-capability rung** that Decima already has the parts to express — make it explicit and first-class:

| Rung | What the agent may do | Decima mechanism |
|---|---|---|
| **1 Read-only** | observe/analyze; no writes/sends | a capability with only `READ`/`PURE` effect_class |
| **2 Draft & suggest** | propose actions; human approves all | DISP1 `invoke`-proposal + a `wager`; nothing executes |
| **3 Supervised + gates** | execute, pausing at checkpoints before irreversible steps | Morta `requires_approval` gated *per effect_class* (REVERSIBLE runs, IRREVERSIBLE/FINANCIAL pauses) |
| **4 Monitored autonomy** | act end-to-end, every action logged + real-time notify | bounded caveats + leases (LEASE1) + the signed Weft audit + NOTIFY1/WEBHOOK1 alerts |
| **5 Full autonomy** | act within scope, periodic review only | scoped caps + budget/lease caveats + LOOP1 governance + org_policy; review = the timeline/audit |

Two things the framework gets right that map cleanly: **(a)** *different steps of one workflow run at
different rungs based on reversibility/stakes* — that **is** Decima's per-effect Morta gating
(`effect_class`); **(b)** *promotion up the ladder is earned by a measurable track record* — that **is**
Nona's promotion gate + WV1 calibration + `org_score`. So an `autonomy_level` is a recorded
per-(agent, capability) caveat, auto-promotable on evidence and instantly demotable (Morta). **Placement:**
a thin `autonomy` layer over capability caveats + Morta + WV1/org_policy; the user can pin/override a rung
manually (like PATTERN1's manual override).

## D6. The sovereign data substrate — "the OneDrive equivalent" (sync · backup · DR · multi-device) `[kernel]`/`[feature]`
*Your data is the Weft.* State is a fold over an append-only, signed, content-addressed log — so the
answers to "what if my machine dies / I get a new device / I want N machines in sync" fall out of the
architecture, and are **stronger** than a file-sync product:

- **Multi-device sync = fold-replication.** GX1 (Merkle-DAG diff + gossip/anti-entropy) converges Wefts
  across N machines; each device folds the same log to identical state. **No conflicts** — the merge layer
  (M1/M2 CRDTs) resolves concurrent edits by type, deterministically.
- **Backup / disaster recovery = replicate the Weft + snapshots, restore = replay to frontier.** Lose the
  machine → pull the (encrypted) Weft + latest snapshot to a new device → fold to the head → *seamless,
  byte-identical state* (incremental fold makes it fast). Egress of the backup is Morta-gated.
- **Secure / encrypted / private by construction.** The Weft is signed (tamper-evident); blobs are
  client-side **encrypted with the user's keys** (held by the CRED1 secrets broker), synced to the user's
  own storage *or* E2E-encrypted through an untrusted relay — sovereignty either way (no provider can read it).
- **Dumb-easy setup.** "Add a device" = enter one **recovery phrase** / scan a pairing code → it pulls +
  folds. One secret to rule recovery; the broker handles the rest. (Pattern refs: Syncthing block-exchange
  MPL, age/libsodium crypto — wrap, don't reinvent.)

This is a flagship sovereignty wedge: **easy to start, impossible to lose, runs anywhere, no one else can
read it.** What it needs to be real (depth): the B2 **crypto layer** (at-rest/in-transit encryption), a
**real networked sync transport** (`Weft.ingest` + transport — GX1 is in-process today), and the
device-pairing UX. *(Augment Code's "Context Engine" + "Organization Knowledge" validate the direction
from the dev-tools side: a structural, shared, relevant-slice-only knowledge substrate beats raw scale —
which is what the Weave already is.)*

---

## How this feeds the build
Nothing here is a cycle yet — it's the scope catalog. Items graduate into `docs/BACKLOG.md` a few at a
time, each as an `[effect]`/`[worker]`/`[skill]`/`[kernel]` lane behind a Decima contract, with the
two laws (Morta-gate outward; untrusted-in = data) and the wrap/port/study license discipline applied.
High-leverage near-term threads the scan surfaced: the **WASM-component sandbox** (makes "engine as
sandboxed principal" real), **Merkle-DAG + gossip sync** (networked sync at scale), **detection-as-code
via Nona** (the security flagship's cheapest win), and **voice + the Constellation** (what makes it
feel like a livable OS).
