# Decima Donor Matrix

Status vocabulary:

- **Adopt** — dependency or code suitable for direct use behind a Decima contract.
- **Wrap** — preserve as an external engine or worker; do not expose its native model publicly.
- **Port** — selectively reuse permissively licensed implementation code.
- **Reimplement** — reproduce the concept against Decima primitives; do not copy code.
- **Inspiration** — product, research, or interaction reference only.
- **Reject** — insufficient value for the architecture.

License classifications are engineering triage, not legal advice. Every copied component and model requires a file-level review.

| Project | Disposition | License posture | Decima destination | What survives | Primary risk |
|---|---|---|---|---|---|
| DeerFlow | Reimplement | Check component licenses | Decima, Shell | Hierarchical agent roles, research workflow, usable agent UI | Application-shell coupling |
| Hermes Agent | Port/Wrap | Permissive; verify files | Decima, executor | Agent loop, tool execution, terminal integration | Runtime assumptions and overlapping orchestration |
| OpenAI Codex | Wrap | Apache-2.0 repository; service terms separate | executor, Shell | Managed coding-worker protocol and session UX | Product/API coupling |
| LangGraph | Adopt selectively | MIT | Decima, Weft | Checkpointed graph execution and interrupts | Its graph must not become Decima’s domain model |
| LangChain | Adopt adapters only | MIT | capability bus | Provider/tool integrations | Abstraction churn and excessive framework surface |
| PydanticAI | Adopt selectively | MIT | agent envelope, capability bus | Typed tools, dependency injection, structured outputs | Python/runtime coupling |
| open-multi-agent | Inspiration | Verify | Decima | Multi-agent topology patterns | Immaturity |
| mythos-router | Inspiration/Port | Verify | model router | Routing concepts | Evaluation evidence and maintenance |
| OpenMythos | Inspiration | Verify | Decima, Weave | Agent mythology/organization ideas | Unproven integration model |
| distro-core | Inspiration | Verify | executor, extension system | Distribution and component packaging ideas | Project maturity |
| claudecode | Inspiration | No clear root license observed | executor, Shell | CLI coding UX patterns | Unclear provenance/licensing |
| open-claude-code | Inspiration/Port | Verify | executor | Open coding-agent patterns | Compatibility and maturity |
| opencode | Wrap/Port | MIT | executor, Shell | Provider-neutral coding sessions and TUI patterns | Duplicating its application shell |
| Vercel agent-browser | Adopt behind a Decima browser-worker contract | Apache-2.0 | browser executor, Shell, Morta | Rust daemon and JSON protocol, isolated sessions, accessibility refs, screenshots, CDP/WebDriver providers, action policy, domain filtering, recording, auth/provider plugins | Not a host sandbox; permissive policy defaults; project config can inject plugins/extensions/scripts; hostname allowlists do not prevent DNS rebinding; raw evaluation/CDP/profile access is highly privileged |
| OpenClaudia skills | Reimplement | Verify | Nona, skill registry | Skill packaging and discovery | Trust and supply chain |
| NVIDIA SkillSpector | Adopt as a quarantined scanner worker; port integration contracts | Apache-2.0 | Nona, Reckoner, Morta, skill registry | Static pattern families, Python AST and taint analysis, YARA, OSV checks, MCP least-privilege/tool-poisoning checks, semantic analyzers, SARIF | Static-only; LLM mode exports source to a provider; Python-centric behavioral coverage; oversized/binary/runtime blind spots; score is evidence, not an authorization decision |
| tmux | Inspiration | ISC in upstream project; verify tree | executor | PTY/session semantics, detach/attach, panes | Native tmux dependency and terminal complexity |
| ghost-in-the-loop | Inspiration | Verify | Morta, Shell | Human intervention patterns | Research maturity |
| ASI-Evolve | Inspiration | Verify | Nona, Reckoner | Evolution/search loop ideas | Unsafe autonomous modification |
| ShinkaEvolve | Inspiration/Wrap experiments | Apache-style; verify | Nona, Reckoner | Program evolution with evaluators | Benchmark overfitting |
| AI-Scientist-v2 | Wrap experiments | Apache-2.0; verify models | Nona, research agents | Automated experiment/review workflow | Reliability of generated science |
| evolutionary-model-merge | Inspiration/Wrap offline | Apache-2.0 | Training Lab | Evolutionary model merging | GPU cost and model-license composition |
| text-to-lora | Wrap offline | Apache-2.0 | Training Lab, Studio | On-demand adapter creation | Data consent and promotion safety |
| continuous-thought-machines | Research only | Check Meta license | model research | Alternative computational architecture | Not production substrate |
| fugu | Research only | License unclear in clone | model research | Inference/reasoning ideas | Availability, license, reproducibility |
| shachi | Research only | Verify | multimodal/research | Specialized research concepts | Unclear production fit |
| ds4 | Inspiration | MIT/verify | local reasoner research | Small-model reasoning experiments | Narrow evidence |
| ponytail | Inspiration | Verify | model/runtime research | Efficient model techniques | Maturity |
| VibeThinker-3B | Wrap as specialist | MIT model; verify dependencies | model router, Reckoner | Cheap candidate generation and verifiable reasoning | Official card says not an autonomous tool agent |
| WikiBrain | Port/rebuild as core | User-owned project | Weave, memory | Claims, evidence, provenance, contradiction handling | Must merge cleanly with Cell semantics |
| RAGFlow | Port algorithms/Wrap parsers | Apache-2.0 | Weave ingestion, retrieval | Parsing, OCR, hybrid retrieval, GraphRAG, RAPTOR, evaluation | Huge coupled platform; chunk-centric model |
| OB1 | Reimplement | FSL-1.1-MIT delayed conversion | Weave, memory | Freshness, confidence, provenance, consolidation, instruction eligibility | Competing-use restriction and weak auth patterns |
| open-notebook | Inspiration/Port selectively | Verify | Shell, Workspace | Source-grounded notebooks, audio/podcast workflows | App-shell duplication |
| Logseq | Reimplement | AGPL-3.0 | Shell, Weave | Blocks, transclusion, backlinks, properties, journals | Copyleft and dual legacy/DB architecture |
| AFFiNE/BlockSuite | Port permissive components selectively | Mostly MIT frontend; backend/native exceptions | Shell, Workspace | Block/edgeless projections, CRDT collaboration, mobile UX | Large monorepo and component-level licensing |
| Reor | Reimplement | AGPL-3.0 | Shell, memory UX | Local knowledge UX, hybrid search controls, related notes | Basic ingestion and copyleft |
| Chroma | Wrap; possible embedded default | Apache-2.0 | derivative index | Embedded dense/sparse/BM25/full-text retrieval | Public model leaking collection semantics |
| Milvus | Wrap at scale | Apache-2.0 | derivative index | Distributed vector indexing and resource isolation | Operational complexity |
| ImageBind | Inspiration/optional external adapter | CC BY-NC-SA | multimodal index | Shared cross-modal embedding concept | Noncommercial ShareAlike license |
| LLaVA-NeXT | Wrap checkpoints/providers | Apache-2.0 code; model licenses vary | visual worker | Vision-tower/projector interfaces, image/video understanding | Research-serving code and GPU requirements |
| PyTorch Lightning/Fabric | Adopt in optional worker | Apache-2.0 | Training Lab, Nona | Training, checkpoints, distributed strategies | Must remain outside control-plane critical path |
| Pipecat | Adopt behind contract | BSD-2-Clause | voice runtime, capability bus | Frame pipelines, WebRTC, interruption, provider adapters | Must not own conversation or memory model |
| OpenAI Realtime/Agents JS | Wrap provider | Service/SDK terms | voice runtime | Low-latency speech-to-speech, tools, handoffs | Hosted dependency and provider-specific events |
| Rapida Voice AI | Inspiration | Modified GPL-2.0 | voice operations | Telephony, provider contracts, tracing, vault patterns | Restrictive license and platform breadth |
| Voice Chat AI | Port concepts/selected MIT code | MIT | voice director | Local TTS/STT adapters, spoken/display filtering | Application-level coupling |
| Axiom Voice Agent | Port selected concepts | Apache-2.0 | local voice worker | Vocabulary correction, local VAD/STT/TTS fast paths | Half-duplex/sequential architecture and weak benchmark evidence |
| baoyu-design | Reimplement/adapt skills | Verify | Studio, Shell | Design-direction workflows and visual critique | Prompt-pack quality and provenance |
| MeiGen AI Design MCP | Port selected code | MIT | Studio capability adapters | Intent classification, references, preferences, provider routing | Hardcoded model policy and hosted-service coupling |
| Open Generative AI | Inspiration | MIT | Studio Shell | Simple/guided studio organization and local model catalogue | Immature execution core |
| Enfugue | Inspiration | GPL-3.0 | Studio | Canvas and deep diffusion controls | Copyleft |
| ilab-gpt-conjure | Reimplement | AGPL-3.0 | Studio jobs | Queue recovery, reference dedupe, prompt fidelity, galleries | Copyleft |
| ComfyUI API | Adopt/Port adapter | MIT | media executor | Workflow validation, queue, interrupt, webhook, polling fallback, storage | Workflow security and custom-node supply chain |
| Stability generative-models | Wrap models; port interfaces cautiously | Code license plus model-specific licenses | image/video executor | Conditioner/denoiser/sampler separation | Model-license fragmentation and research code |
| stable-audio-tools | Wrap/Port | License and bundled third-party licenses vary | audio executor, Training Lab | Audio diffusion, conditioning, inpainting, evaluation | Complex transitive licensing |
| stable-audio-3 | Wrap | Stability model/code terms; verify | audio executor | Long-form generation, LoRA, MLX/TensorRT paths | Hardware and model terms |
| StableSwarmUI | Inspiration | MIT-like; verify | Studio scheduler | Backend pools, queues, model metadata, presets | Authentication/secrets patterns unsuitable |
| AnimateDiff | Wrap legacy/Port concepts | Apache-2.0 | video executor | Motion modules, MotionLoRA, sparse controls | Older architecture |
| stable-diffusion-videos | Port concepts | Apache-2.0 | Studio timeline | Latent interpolation, audio-reactive timing, resumable frames | Obsolete as primary generation method |
| Video-P2P | Research/inspiration | No clear root license observed | video editor | Inversion and attention-based video editing | License, age, hardware |
| Dreamifly | Inspiration only | No root license observed | Studio product | Consumer model selection, community/gallery and usage controls | Hardcoded workflows, unclear license, service-specific business logic |
| Runway | Wrap hosted provider | Commercial service | video executor | Modern hosted video generation/editing | Cost, terms, lock-in |
| FastAPI-Streamlit | Reject | MIT | None | Only demonstrates process separation | Toy implementation |
| Prompt “awesome lists” | Curate as data, never runtime | Per-entry provenance varies | Studio inspiration corpus | Prompt examples and style vocabulary | Copyright, duplicates, model drift, unsafe content |

## Architectural consequences

1. No donor owns a canonical Decima type. Adapters translate into Cells, Events, Capabilities, Receipts, and Attestations.
2. AGPL, FSL, noncommercial, and unclear-license repositories are study-only unless a later legal review explicitly clears a boundary.
3. Engines run as principals with attenuated capabilities. A database, model worker, browser, CLI agent, or media backend never receives ambient access.
4. External state is represented by durable invocation intent plus receipts; the Weft does not pretend an external service is deterministic.
5. The highest-value direct dependencies currently are Pipecat, selected LangGraph/PydanticAI patterns, Chroma or PostgreSQL-derived indexing, PyTorch Lightning workers, and the ComfyUI API adapter. Everything remains replaceable.
