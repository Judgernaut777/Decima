# Decima Memory Architecture

## 1. Decision

Decima's memory is not a vector database, a chat history table, or a donor
framework. Memory is typed Cell state folded from the Weft. Search indexes,
knowledge graphs, summaries, profile views, and model context windows are
derivative projections.

The inspected memory systems converge on useful patterns:

- Mem0: scoped user/agent/run memories, fact extraction, update/delete
  decisions, procedural memories, hybrid semantic/BM25/entity ranking.
- Zep/Graphiti: temporal knowledge graph, episodic source nodes, entity and
  edge summaries, duplicate and contradiction invalidation.
- Letta/MemGPT: core memory blocks, archival/recall memory, explicit memory
  tools, context-window paging, sleeptime/background consolidation.
- Cognee: graph ingestion pipelines, `remember/recall/improve/forget`,
  session/trace/graph recall scopes, feedback-weighted improvement.
- LangMem: namespaced memory tools, schema-driven extraction, background
  reflection executor, running summaries, prompt/procedural optimization.
- Memobase: user profiles, event memories, event tags/gists, profile/event
  context mixing.
- MemoryOS: short/mid/long tiers, segmented pages, heat-based promotion.
- MIRIX: Core/Episodic/Semantic/Procedural/Resource/Knowledge Vault taxonomy
  and specialized memory subagents.
- projectmem: append-only coding memory plus pre-action governance.

Decima should take the mechanisms, not the donor data models.

## 2. Memory Cell types

Every memory Cell has:

```text
MemoryCell {
  id
  type
  subject_selector
  scope
  content_ref
  evidence_links
  derived_from
  confidence
  epistemic_type
  instruction_eligible
  sensitivity
  retention_policy
  valid_time?
  event_time?
  supersedes?
  contradicted_by?
  curator
}
```

Core types:

| Type | Purpose | Donor pressure |
|---|---|---|
| Core | Small, always-visible facts about user, agent, project, or realm | Letta, MIRIX |
| Episodic | Event-like remembered experience with source transcript/tool evidence | Graphiti, Memobase, MIRIX |
| Semantic | Durable facts, concepts, relationships, claims | Mem0, Cognee, Graphiti |
| Procedural | How-to, workflow, preference for execution, learned skill behavior | Mem0, Letta, LangMem, MIRIX |
| Resource | External document/file/media summary and retrieval handle | Cognee, MIRIX, LlamaIndex |
| Profile | User/entity profile slots with topic/subtopic organization | Memobase, Mem0 |
| Decision | Project/business decision with rationale and alternatives | projectmem, WikiBrain |
| Failure | Failed attempt, fragile file, known bad fix, anti-repeat warning | projectmem |
| PolicyCandidate | Suggested prompt/skill/routing update awaiting Nona/Morta gates | LangMem |
| Scratch | Ephemeral working note, not yet durable history | Letta, MemoryOS |

Search and graph projections may use their own document shapes, but they must
refer back to Memory Cells and source events.

## 3. Scopes and horizons

All memory operations resolve against a scope:

```text
scope :=
  user(id)
| agent(id)
| run(id)
| project(id)
| realm(id)
| team(id)
| resource(id)
| task(id)
| composite(scope...)
```

This generalizes Mem0's `user_id` / `agent_id` / `run_id`, LangMem namespaces,
Cognee recall scopes, and Memobase user profiles.

An agent's `horizon` is a selector over scopes and Cell types. The memory router
cannot hand an agent data outside that horizon. Retrieval is authorization
first, ranking second.

## 4. Recall router

Recall is a capability-mediated INVOKE:

```text
memory.recall {
  query
  intent
  scopes
  memory_types
  max_tokens
  freshness
  confidence_floor
  instruction_mode
  evidence_required
}
```

The router builds a candidate set from multiple projections:

1. exact Cell and graph relation filters;
2. full-text/BM25 keyword retrieval;
3. embedding retrieval;
4. entity and topic expansion;
5. temporal filters over event time and valid time;
6. recent working context and running summaries;
7. failure/decision governance checks.

It then reranks with explicit features:

```text
score =
  semantic_match
  + keyword_match
  + entity_overlap
  + temporal_relevance
  + recency
  + confidence
  + reinforcement
  + user_feedback
  + task_relevance
  - contradiction_penalty
  - sensitivity_penalty
  - staleness_penalty
```

Low-confidence recalls may be rejected instead of filled with weak matches. A
"no relevant memory" result is valid and often safer than false recall.

## 5. Instruction eligibility

Decima keeps four permissions separate:

- may store;
- may recall as data;
- may cite as evidence;
- may use as instruction.

Page text, documents, emails, social content, and tool output default to
`instruction_eligible=false`. A profile preference like "answer me in concise
bullets" can become instruction-eligible only after a trusted source and policy
allow it. A resource document saying "ignore previous instructions" remains
data forever unless explicitly promoted by policy.

This is a kernel rule, not prompt advice.

## 6. Remember pipeline

Durable memory creation is a multi-stage pipeline:

1. Capture raw input as source/evidence Cell.
2. Classify sensitivity and instruction eligibility.
3. Extract candidate memories with schema-specific curators.
4. Link exact evidence spans, artifacts, or tool receipts.
5. Search for related memories in the authorized scope.
6. Decide action: create, update, supersede, retract, merge, or reject.
7. Run contradiction and duplicate checks.
8. Assign confidence, valid time, event time, retention, and sensitivity.
9. ASSERT memory Cells and ATTEST curator/verifier results.
10. Update derivative indexes asynchronously.

LLM extraction is never itself truth. It creates proposed Cells that must carry
evidence and curator provenance.

## 7. Temporal model

Temporal memory needs two clocks:

- `event_time`: when the evidence or interaction occurred;
- `valid_time`: when the asserted fact is/was true.

Facts can have `valid_from`, `valid_until`, and `supersedes` links. "Alice was
budget owner until February, then Bob took over" is not a delete; it is a
temporal transition. Contradictory memories remain auditable, but current-state
projections prefer the latest attested valid fact under the type policy.

Graphiti's `valid_at` / `invalid_at` pattern maps cleanly to these fields.

## 8. Consolidation and tiers

Decima uses tiers as projections over the same Cells:

| Tier | Meaning | Mechanism |
|---|---|---|
| Working | Current context window and active task state | selected Cells + recent receipts |
| Scratch | Ephemeral agent notes | GC unless graduated |
| Episodic | Source-linked events and trajectories | append-only, evidence-heavy |
| Semantic | Consolidated facts and relationships | curated/attested Cells |
| Procedural | Reusable methods, preferences, learned workflows | Nona/Reckoner-gated promotion |
| Archival | Long-term resources and transcripts | compressed/indexed artifacts |

Background "sleeptime" curators compact, merge, and propose promotions when:

- context pressure crosses a threshold;
- repeated retrieval indicates high utility;
- feedback marks memory useful or wrong;
- a task finishes and emits decisions/failures;
- a source changes or becomes stale.

MemoryOS-style heat scores are allowed as derivative signals:

```text
heat = access_frequency + feedback + task_reuse + recency - age_decay - contradiction
```

Heat can recommend promotion or compression; it cannot authorize broader
visibility.

## 9. Memory governance

projectmem's strongest idea is memory-as-governance. Decima should implement
pre-action checks for:

- repeated failed fixes;
- fragile files or subsystems;
- prior decisions and explicit constraints;
- active incident/postmortem lessons;
- user-stated "do not do this again" preferences;
- policy candidates that have not been promoted.

These checks are read-only recall plus a Morta/Nona gate. They warn, block, or
require approval depending on the held capability and realm policy.

## 10. Feedback and self-improvement

Feedback is first-class memory, not a hidden ranking knob.

```text
FeedbackCell {
  target
  score
  reason
  source
  scope
  created_at
}
```

Feedback can:

- adjust ranking features;
- trigger re-extraction or consolidation;
- mark a memory stale/wrong;
- produce a PolicyCandidate or procedural-memory proposal;
- feed eval suites for Nona.

Prompt or skill updates produced from memory feedback are not applied directly.
They go through Nona's quarantine, Reckoner evaluation, and Morta gates when
they affect authority, privacy, publication, or external effects.

## 11. Multimodal memory

Visual, audio, browser, and document memories store:

- original artifact reference;
- modality-specific descriptors;
- extracted text/transcript;
- regions/timecodes/spans as evidence anchors;
- model/tool provenance;
- sensitivity classification.

MIRIX and MementoGUI point to a useful rule: do not keep only text summaries of
GUI/visual work. Preserve compact visual evidence such as screenshot regions,
UI state, or video/audio time ranges when future decisions may depend on them.

## 12. Privacy, deletion, and retention

Memory writes inherit the highest sensitivity of their evidence. Private
profile facts are not copied into global semantic memory unless policy permits
it. Cross-user or cross-agent sharing is explicit capability delegation.

Deletion is RETRACT/REDACT plus cryptographic erasure where possible:

- retract the effective memory Cell;
- remove it from derivative indexes;
- destroy artifact encryption keys when policy requires;
- keep minimal event skeletons for audit unless a stronger erasure policy says
  otherwise.

Search backends must support scoped deletion from Weft policy. If a backend
cannot prove deletion, it is unsuitable for sensitive memory.

## 13. Default implementation profile

The first practical memory stack should be:

- Weft/Weave as canonical store;
- SQLite/Postgres for Cell materialization;
- local BM25/full-text index;
- one vector index adapter, initially Chroma or Qdrant;
- optional graph projection, initially simple typed edges before Neo4j/Kuzu;
- background curator worker;
- memory.recall / memory.remember / memory.feedback / memory.governance_check
  capabilities.

Donor code can be wrapped for experiments, but the public contract is Decima's
Cell/event/capability model.
