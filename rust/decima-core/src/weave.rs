//! The Weave — the materialized graph, computed by folding the Weft.
//!
//! Law 5: state is a fold; everything you see is a projection. Law 3:
//! everything is a Cell.
//!
//! Port of heartbeat/decima/weave.py, SUBSET (documented in rust/README.md):
//! events are applied in the deterministic total order (lamport, event_id),
//! with the register merge substrate (causal-dominance head tracking, LWW /
//! MV materialization) implemented faithfully. Assertion kinds CONTENT, EDGE,
//! and TYPE_DEF are folded; RETRACT supports the WITHDRAW / REDACT /
//! SUPERSEDE / TERMINATE mode flags and records cascade roots, and the
//! DERIVED_AUTHORITY / LEASE_TREE cascade closure is derived as a pure pass.
//! Milestone 3 added: ATTEST adjudication-collapse for MV/adjudicated cells
//! (MERGE_SEMANTICS §4), trusted tiered promotion (NONA_RECKONER §7 —
//! seq-anchored genesis root, root-authored `promoter` cells only, fail
//! closed), EffectReceipt projections (`receipts_for_idempotency` /
//! `canonical_for_idempotency`), and LEASE1 lease-expiry derivation
//! (`frontier_lamport` + the invoke tally feed `lease_status`; a lapsed
//! lease becomes a DERIVED_AUTHORITY cascade root).
//! NOT yet ported: the or-set/counter/append-log/sequence/map reducers,
//! snapshots, and time-travel windows.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde_json::{json, Value};

use crate::hashing::content_id;
use crate::weft::{Event, Weft, ASSERT, ATTEST, INVOKE, RETRACT};

/// UNKNOWN receipt status (WEFT §8.3, executor.UNKNOWN): the effect may have
/// happened but the outcome is unobservable.
pub const UNKNOWN: &str = "UNKNOWN";
pub const MERGE_LWW: &str = "lww";
pub const MERGE_MV: &str = "mv";
pub const MERGE_ADJUDICATED: &str = "adjudicated";
pub const DEFAULT_MERGE: &str = MERGE_LWW;
/// Classes that preserve concurrent heads until an adjudication collapses them.
const MULTIVALUE: [&str; 2] = [MERGE_MV, MERGE_ADJUDICATED];

#[derive(Debug, Clone, Default)]
pub struct Cell {
    pub id: String,
    /// "thing" when not asserted otherwise.
    pub r#type: String,
    pub content: Value,
    pub version: i64,
    pub provenance: Vec<String>,
    pub attestations: Vec<Value>,
    pub retracted: bool,
    /// {rel, src, dst, event} records; src.edges_out / dst.edges_in share the edge.
    pub edges_out: Vec<Value>,
    pub edges_in: Vec<Value>,
    /// Ordered list of live head values; [content] when resolved.
    pub content_heads: Vec<Value>,
    pub in_conflict: bool,
    pub redacted: bool,
    pub cascade_root: bool,
    pub cascaded: bool,
    pub lease_expired: bool,
    pub superseded_by: Option<String>,
    pub cascade_mode: Option<String>,
}

impl Cell {
    fn new(id: &str, r#type: &str) -> Self {
        Cell {
            id: id.to_string(),
            r#type: r#type.to_string(),
            content: json!({}),
            ..Default::default()
        }
    }
}

/// An INVOKE folded onto the log's effect record (weave.py `Invocation`).
#[derive(Debug, Clone)]
pub struct Invocation {
    /// Event id of the INVOKE.
    pub event: String,
    /// Principal id that signed it.
    pub by: String,
    /// Capability cell id it went through.
    pub cap: String,
    pub args: Value,
}

#[derive(Default)]
pub struct Weave {
    /// Keyed by cell id; BTreeMap keeps iteration ordered for state_root.
    pub cells: BTreeMap<String, Cell>,
    /// Folded INVOKEs, in fold order (weave.py `invocations`).
    pub invocations: Vec<Invocation>,
    /// Type name -> TYPE_DEF cell id (Law 3).
    pub types: HashMap<String, String>,
    /// Type name -> merge class.
    pub merge_classes: HashMap<String, String>,
    /// Per-capability INVOKE tally — the spend side of a max_uses lease,
    /// folded deterministically from the Log (weave.py `_invoke_counts`).
    pub invoke_counts: HashMap<String, i64>,
    /// LEASE1: the max lamport folded so far — "now" for a time-locked
    /// lease; deterministic, never wall-clock (weave.py `frontier_lamport`).
    pub frontier_lamport: i64,
    /// The realm ROOT authority — the author of the CONSTITUTIONAL genesis:
    /// the PARENTLESS event with the SMALLEST `seq` (weave.py
    /// `_genesis_author`). Deliberately NOT anchored on (lamport, event_id):
    /// event ids are content-addressed and grindable, so an attacker could
    /// mint a second parentless event that folds first; `seq` is a local,
    /// unforgeable AUTOINCREMENT.
    pub genesis_author: Option<String>,
    genesis_seq: Option<i64>,
    /// Who asserted each `promoter` cell — a promoter cell NOT asserted by
    /// the root authority is ignored at promote time (fail closed).
    promoter_author: HashMap<String, String>,
    applied: HashSet<String>,
    /// Merge substrate: event id -> its causal ancestor ids.
    ancestors: HashMap<String, HashSet<String>>,
    /// ns -> {assert_eid: (lamport, content)}.
    reg_heads: HashMap<String, HashMap<String, (i64, Value)>>,
    /// ns -> eids dominated by an adjudication.
    reg_superseded: HashMap<String, HashSet<String>>,
}

impl Weave {
    pub fn new() -> Self {
        Self::default()
    }

    /// Fold the Weft into the Weave. Events are applied in the deterministic
    /// total order (lamport, event_id) (FOLD §2 / WEFT §9), NOT arrival order.
    pub fn fold(weft: &Weft) -> Weave {
        Self::fold_events(weft.events().iter())
    }

    /// Fold any verified event stream (in-memory Weft or a SQLite WeftDb's
    /// `events()` output) in the deterministic (lamport, event_id) order.
    pub fn fold_events<'a>(events: impl Iterator<Item = &'a Event>) -> Weave {
        let mut w = Weave::new();
        let mut evs: Vec<&Event> = events.collect();
        evs.sort_by(|a, b| (a.lamport, &a.id).cmp(&(b.lamport, &b.id)));
        for ev in evs {
            w.apply(ev);
        }
        // Derive the DERIVED_AUTHORITY + LEASE cascade NOW, so a consumer
        // that reads `w.cells` directly sees the materialized retraction /
        // lease flags — not just consumers that call a read projection
        // (weave.py `fold` calls `_ensure_cascade` before returning).
        w.cascade_retractions();
        w
    }

    fn ensure(&mut self, cid: &str, r#type: &str) -> &mut Cell {
        self.cells
            .entry(cid.to_string())
            .or_insert_with(|| Cell::new(cid, r#type))
    }

    fn merge_class_of(&self, type_name: &str) -> String {
        self.merge_classes
            .get(type_name)
            .cloned()
            .unwrap_or_else(|| DEFAULT_MERGE.to_string())
    }

    /// Apply ONE event. Idempotent by event id (FOLD §2): a duplicate apply
    /// of an id already folded is a no-op.
    pub fn apply(&mut self, ev: &Event) {
        if self.applied.contains(&ev.id) {
            return;
        }
        self.applied.insert(ev.id.clone());

        // The realm ROOT authority is the author of the CONSTITUTIONAL
        // genesis — the PARENTLESS event with the smallest local `seq`. We
        // MUST NOT anchor on the earliest folded event: fold order is
        // (lamport, event_id) and ids are content-addressed → grindable. An
        // attacker can mint a second parentless (lamport==1) event whose id
        // sorts before the real genesis; under an id-order anchor it would
        // fold FIRST and hijack the root identity. `seq` cannot be forged or
        // ground, so any later parentless event gets a strictly higher seq
        // and can never become the anchor (fail closed). Compared by seq, not
        // application order, so the anchor is the true root even when the
        // impostor's event applies earlier (weave.py `_apply`).
        if ev.parents.is_empty()
            && (self.genesis_seq.is_none() || ev.seq < self.genesis_seq.unwrap())
        {
            self.genesis_seq = Some(ev.seq);
            self.genesis_author = Some(ev.author.clone());
        }
        // Logical frontier time (LEASE1): the max lamport folded so far is
        // "now" for a time-locked lease — deterministic, never wall-clock.
        if ev.lamport > self.frontier_lamport {
            self.frontier_lamport = ev.lamport;
        }

        // Causal ancestors (MERGE_SEMANTICS §2.1). Folding in (lamport,
        // event_id) order guarantees every parent is already applied.
        let mut anc: HashSet<String> = ev.parents.iter().cloned().collect();
        for p in &ev.parents {
            if let Some(pa) = self.ancestors.get(p) {
                anc.extend(pa.iter().cloned());
            }
        }
        self.ancestors.insert(ev.id.clone(), anc.clone());

        let b = &ev.body;
        if ev.verb == ASSERT {
            // Read kind BEFORE "cell" — EDGE bodies have no "cell" key.
            let kind = b.get("kind").and_then(Value::as_str).unwrap_or("CONTENT");

            if kind == "EDGE" {
                let src = b["src"].as_str().expect("EDGE body src").to_string();
                let rel = b["rel"].as_str().expect("EDGE body rel").to_string();
                let dst = b["dst"].as_str().expect("EDGE body dst").to_string();
                let key = (rel.clone(), src.clone(), dst.clone());
                let already = self
                    .cells
                    .get(&src)
                    .map(|s| {
                        s.edges_out.iter().any(|e| {
                            (e["rel"].as_str(), e["src"].as_str(), e["dst"].as_str())
                                == (
                                    Some(key.0.as_str()),
                                    Some(key.1.as_str()),
                                    Some(key.2.as_str()),
                                )
                        })
                    })
                    .unwrap_or(false);
                if !already {
                    let edge = json!({"rel": rel, "src": src, "dst": dst, "event": ev.id});
                    self.ensure(&src, "thing").edges_out.push(edge.clone());
                    self.ensure(&dst, "thing").edges_in.push(edge);
                }
                return;
            }

            let cid = b["cell"].as_str().expect("ASSERT body cell").to_string();
            let type_field = b.get("type").and_then(Value::as_str);
            {
                let cell = self.ensure(&cid, type_field.unwrap_or("thing"));
                if let Some(t) = type_field {
                    cell.r#type = t.to_string();
                }
            }
            let content = b.get("content").cloned().unwrap_or_else(|| json!({}));
            let type_name = {
                let cell = self.cells.get_mut(&cid).unwrap();
                cell.version += 1;
                cell.retracted = false;
                cell.provenance.push(ev.id.clone());
                cell.r#type.clone()
            };
            let mc = self.merge_class_of(&type_name);
            // Only the register reducers are ported (see module docs).
            self.apply_register(&cid, ev, &anc, content, &mc);

            if kind == "TYPE_DEF" {
                let cell = &self.cells[&cid];
                let name = cell
                    .content
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or(&cid)
                    .to_string();
                let mc_declared = cell
                    .content
                    .get("merge_class")
                    .and_then(Value::as_str)
                    .unwrap_or(DEFAULT_MERGE)
                    .to_string();
                self.types.insert(name.clone(), cid.clone());
                self.merge_classes.insert(name, mc_declared);
            }

            if self.cells[&cid].r#type == "promoter" {
                // Record WHO asserted this trusted-promoter anchor. Only the
                // realm ROOT's declaration establishes promotion authority
                // (NONA_RECKONER §7); a principal that self-asserts a
                // `promoter` cell is filtered out at promote time.
                self.promoter_author.insert(cid.clone(), ev.author.clone());
            }
        } else if ev.verb == RETRACT {
            let cid = b["cell"].as_str().expect("RETRACT body cell").to_string();
            if let Some(cell) = self.cells.get_mut(&cid) {
                cell.retracted = true;
                cell.provenance.push(ev.id.clone());
                // Retraction MODE (WEFT §5). WITHDRAW (default) tombstones;
                // REDACT erases the payload from projections; SUPERSEDE records
                // the replacement (no erase, no default cascade); TERMINATE
                // defaults to the LEASE_TREE cascade below.
                let mode = b.get("mode").and_then(Value::as_str).unwrap_or("WITHDRAW");
                if mode == "REDACT" {
                    cell.content = json!({});
                    cell.content_heads = vec![];
                    cell.edges_out = vec![];
                    cell.edges_in = vec![];
                    cell.redacted = true;
                } else if mode == "SUPERSEDE" {
                    cell.superseded_by = b
                        .get("replacement")
                        .and_then(Value::as_str)
                        .map(str::to_string);
                }
                // Retraction CASCADE (WEFT §5 cascade / FOLD §10.2): explicit
                // `cascade` wins; TERMINATE defaults to LEASE_TREE; a
                // non-SUPERSEDE capability RETRACT defaults to
                // DERIVED_AUTHORITY. Both mark a cascade_root; descendant
                // marking is the derived pass in `cascade_retractions`.
                let mut cascade = b.get("cascade").and_then(Value::as_str).map(str::to_string);
                if cascade.is_none() && mode == "TERMINATE" {
                    cascade = Some("LEASE_TREE".to_string());
                }
                if cascade.is_none() && mode != "SUPERSEDE" && cell.r#type == "capability" {
                    cascade = Some("DERIVED_AUTHORITY".to_string());
                }
                if matches!(
                    cascade.as_deref(),
                    Some("DERIVED_AUTHORITY") | Some("LEASE_TREE")
                ) {
                    cell.cascade_root = true;
                    cell.cascade_mode = cascade;
                }
            }
        } else if ev.verb == INVOKE {
            // weave.py INVOKE: record the invocation and bump the
            // per-capability tally (LEASE1 spend side). Authorization was
            // judged at the ORIGIN (kernel.invoke) — the fold never re-gates.
            let cap = b["cap"].as_str().expect("INVOKE body cap").to_string();
            let args = b.get("args").cloned().unwrap_or_else(|| json!({}));
            self.invocations.push(Invocation {
                event: ev.id.clone(),
                by: ev.author.clone(),
                cap: cap.clone(),
                args,
            });
            *self.invoke_counts.entry(cap).or_insert(0) += 1;
        } else if ev.verb == ATTEST {
            // weave.py ATTEST: the attestation folds onto the TARGET cell as
            // {by, claim, event} — evidence any signer may leave; the fold
            // never gates on who signed. Two attestation ROLES then act on
            // the target: adjudication (MERGE_SEMANTICS §4) and trusted
            // promotion (NONA_RECKONER §7).
            let target_id = b.get("target_cell").and_then(Value::as_str);
            if let Some(tid) = target_id {
                if !self.cells.contains_key(tid) {
                    return;
                }
                let claim = b
                    .get("claim")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                self.cells.get_mut(tid).unwrap().attestations.push(json!({
                    "by": ev.author,
                    "claim": claim,
                    "event": ev.id,
                }));

                // Adjudication (MERGE_SEMANTICS §4): an ATTEST with predicate
                // 'adjudicates' collapses preserved heads (MV / adjudicated
                // classes). SELECT supersedes the non-winner heads it names
                // as `evidence`, so they drop out of heads() while staying in
                // history (§4.1, logical not erasure). Resolution binds only
                // the named evidence — a later, unobserved concurrent head
                // re-opens the conflict (§4.3). The reference gates NOTHING
                // on the adjudicator: any principal's signed ATTEST is the
                // authority. Mirrored exactly.
                if b.get("predicate").and_then(Value::as_str) == Some("adjudicates") {
                    if b.get("resolution")
                        .and_then(Value::as_str)
                        .unwrap_or("select")
                        == "select"
                    {
                        let winner = b.get("winner").and_then(Value::as_str);
                        let sup = self.reg_superseded.entry(tid.to_string()).or_default();
                        for eid in b
                            .get("evidence")
                            .and_then(Value::as_array)
                            .into_iter()
                            .flatten()
                        {
                            if let Some(eid) = eid.as_str() {
                                if Some(eid) != winner {
                                    sup.insert(eid.to_string());
                                }
                            }
                        }
                    }
                    let mc = self.merge_class_of(&self.cells[tid].r#type);
                    self.materialize_register(tid, &mc);
                    let target = self.cells.get_mut(tid).unwrap();
                    target.version += 1;
                    target.provenance.push(ev.id.clone());
                }

                // Promotion (NONA_RECKONER §7): a TRUSTED attestation lifts a
                // capability's quarantine — clearing the flag and the
                // sandbox_only caveat — ONLY when the ATTEST author is a
                // trusted promoter for the candidate's declared TIER. An
                // untrusted/forged principal's promote-ATTEST is still
                // recorded as attestation evidence (above) but does NOT lift
                // quarantine — fail closed.
                if b.get("promote").and_then(Value::as_bool).unwrap_or(false)
                    && self.cells[tid].r#type == "capability"
                {
                    let tier = Self::candidate_tier(&self.cells[tid]);
                    if self.is_trusted_promoter(&ev.author, tier.as_deref()) {
                        let target = self.cells.get_mut(tid).unwrap();
                        let mut content = target.content.clone();
                        if let Value::Object(c) = &mut content {
                            let mut caveats = match c.remove("caveats") {
                                Some(Value::Object(cv)) => cv,
                                _ => serde_json::Map::new(),
                            };
                            caveats.remove("sandbox_only");
                            c.insert("caveats".to_string(), Value::Object(caveats));
                            c.insert("quarantined".to_string(), json!(false));
                        }
                        target.content = content.clone();
                        target.content_heads = vec![content]; // resolved head stays in sync
                        target.version += 1;
                        target.provenance.push(ev.id.clone());
                    }
                }
            }
        }
    }

    // -- trusted, tiered promotion (NONA_RECKONER §7) -----------------------

    /// The declared effect-class TIER a capability's promotion is signed
    /// against: `declared_effect_class` top-level or in its caveats; a legacy
    /// cap declares none → None (the pre-cycle lift path, back-compat).
    fn candidate_tier(cell: &Cell) -> Option<String> {
        let t = cell
            .content
            .get("declared_effect_class")
            .and_then(Value::as_str);
        t.or_else(|| {
            cell.content
                .get("caveats")
                .and_then(|c| c.get("declared_effect_class"))
                .and_then(Value::as_str)
        })
        .map(str::to_string)
    }

    /// True iff `principal` may promote a candidate of `tier` (§7). Trust is
    /// DATA on the Weft: live `promoter` cells declare, per principal, which
    /// tiers it may sign — honored ONLY when asserted by the seq-anchored
    /// ROOT authority (`promoter_author[cid] == genesis_author`). A
    /// self-declared or forged promoter cell is filtered out (fail closed);
    /// with no genesis anchored, every promoter is filtered. A capability
    /// with NO declared tier keeps the pre-cycle behavior (any
    /// promote-ATTEST lifts it).
    fn is_trusted_promoter(&self, principal: &str, tier: Option<&str>) -> bool {
        let Some(tier) = tier else {
            return true;
        };
        for (cid, c) in &self.cells {
            if c.r#type != "promoter" || c.retracted {
                continue;
            }
            if self.promoter_author.get(cid) != self.genesis_author.as_ref() {
                continue; // only a ROOT-declared anchor is trusted
            }
            if c.content.get("principal").and_then(Value::as_str) == Some(principal)
                && c.content
                    .get("tiers")
                    .and_then(Value::as_array)
                    .map(|tiers| tiers.iter().any(|t| t.as_str() == Some(tier)))
                    .unwrap_or(false)
            {
                return true;
            }
        }
        false
    }

    // -- register substrate (LWW / MV; MERGE_SEMANTICS §2-3) ----------------

    fn reg_push(&mut self, ns: &str, ev: &Event, anc: &HashSet<String>, content: Value) {
        let heads = self.reg_heads.entry(ns.to_string()).or_default();
        let dominated: Vec<String> = heads.keys().filter(|h| anc.contains(*h)).cloned().collect();
        for h in dominated {
            heads.remove(&h);
        }
        heads.insert(ev.id.clone(), (ev.lamport, content));
    }

    /// Live heads for `ns`, in (lamport, event_id) order, minus adjudicated ones.
    fn reg_live(&self, ns: &str) -> Vec<(String, i64, Value)> {
        let empty = HashMap::new();
        let heads = self.reg_heads.get(ns).unwrap_or(&empty);
        let sup = self.reg_superseded.get(ns);
        let mut live: Vec<(String, i64, Value)> = heads
            .iter()
            .filter(|(eid, _)| !sup.map(|s| s.contains(*eid)).unwrap_or(false))
            .map(|(eid, (lam, val))| (eid.clone(), *lam, val.clone()))
            .collect();
        live.sort_by(|a, b| (a.1, &a.0).cmp(&(b.1, &b.0)));
        live
    }

    /// Project heads → content. LWW resolves to the (lamport, eid) winner;
    /// MV/adjudicated preserve every concurrent head and flag the conflict.
    fn materialize_register(&mut self, cid: &str, mc: &str) {
        let live = self.reg_live(cid);
        if live.is_empty() {
            return;
        }
        let winner = live.last().unwrap().2.clone();
        let cell = self.cells.get_mut(cid).unwrap();
        cell.content = winner.clone();
        if MULTIVALUE.contains(&mc) {
            cell.content_heads = live.into_iter().map(|(_, _, v)| v).collect();
            cell.in_conflict = cell.content_heads.len() > 1;
        } else {
            cell.content_heads = vec![winner];
            cell.in_conflict = false;
        }
    }

    fn apply_register(
        &mut self,
        ns: &str,
        ev: &Event,
        anc: &HashSet<String>,
        content: Value,
        mc: &str,
    ) {
        self.reg_push(ns, ev, anc, content);
        self.materialize_register(ns, mc);
    }

    // -- DERIVED_AUTHORITY / LEASE_TREE cascade (FOLD §10.2) ----------------

    /// The cells `cell`'s authority directly DESCENDS from: content["parent"],
    /// content["derived_from"], and derives_from / leased_from edges_out.
    fn authority_ancestors(&self, cell: &Cell) -> Vec<String> {
        let mut out = Vec::new();
        if let Value::Object(_) = &cell.content {
            for k in ["parent", "derived_from"] {
                if let Some(r) = cell.content.get(k).and_then(Value::as_str) {
                    if !r.is_empty() {
                        out.push(r.to_string());
                    }
                }
            }
        }
        for e in &cell.edges_out {
            let rel = e["rel"].as_str().unwrap_or("");
            if rel == "derives_from" || rel == "leased_from" {
                if let Some(d) = e["dst"].as_str() {
                    out.push(d.to_string());
                }
            }
        }
        out
    }

    /// Derived pass (FOLD §10.2 + LEASE1): fail closed any cell whose
    /// authority descends — transitively — from a cascade_root. Pure function
    /// of the folded graph (cascade roots + the authority-ancestor relation +
    /// the folded frontier/invoke tally), recomputed from scratch each call,
    /// so it is arrival-order independent and idempotent.
    pub fn cascade_retractions(&mut self) {
        // 1. Clear cascade-set retraction so recomputation starts clean. A
        //    cell retracted ONLY by the cascade goes back to live; a cell with
        //    its own RETRACT keeps `retracted`. Lease-expiry roots are also
        //    purely derived — cleared here and re-derived below — but ONLY
        //    when this Weave actually folded events (a Weave reassembled from
        //    snapshot leaves has no frontier/invoke substrate; its leaves
        //    carry the flags already).
        let derive_leases = !self.applied.is_empty();
        for c in self.cells.values_mut() {
            if c.cascaded {
                c.cascaded = false;
                c.retracted = false;
            }
            if derive_leases && c.lease_expired {
                c.lease_expired = false;
                c.cascade_root = false;
                c.retracted = false;
            }
        }

        // 1b. LEASE derivation (LEASE1): a capability whose lease has lapsed
        //     at the current logical frontier — `expires_at` reached, or
        //     `max_uses` spent — fails CLOSED exactly like a revoked grant:
        //     retracted + a DERIVED_AUTHORITY cascade root, so the SAME
        //     cascade machinery fails closed every grant attenuated from it.
        if derive_leases {
            let mut lapsed: Vec<String> = Vec::new();
            for (cid, c) in &self.cells {
                if c.r#type != "capability" || c.retracted {
                    continue;
                }
                let caveats = c
                    .content
                    .get("caveats")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                if caveats.get("expires_at").is_none() && caveats.get("max_uses").is_none() {
                    continue;
                }
                let uses = self.invoke_counts.get(cid).copied().unwrap_or(0);
                let (live, _) =
                    crate::capability::lease_status(&caveats, Some(self.frontier_lamport), uses);
                if !live {
                    lapsed.push(cid.clone());
                }
            }
            for cid in lapsed {
                let c = self.cells.get_mut(&cid).unwrap();
                c.lease_expired = true;
                c.cascade_root = true;
                c.retracted = true;
            }
        }

        // Self-retracted = has its own RETRACT (cascade_root or plain
        // WITHDRAW/REDACT) or a lapsed lease (derived above). This is the
        // ground truth the cascade derives from.
        let self_retracted: HashSet<String> = self
            .cells
            .values()
            .filter(|c| c.retracted)
            .map(|c| c.id.clone())
            .collect();

        // 2. Walk authority-ancestors with memoization; a cell fails closed
        //    iff any ancestor is a cascade_root (or itself descends from one).
        //    Fail closed on ambiguous ancestry: a cycle or a missing ancestor
        //    is never widened back to live here.
        let mut memo: HashMap<String, bool> = HashMap::new();
        let mut stack: HashSet<String> = HashSet::new();
        let mut to_close: Vec<String> = Vec::new();
        for cid in self.cells.keys().cloned().collect::<Vec<_>>() {
            if self_retracted.contains(&cid) {
                continue;
            }
            let ancestors = self.authority_ancestors(&self.cells[&cid]);
            if ancestors
                .iter()
                .any(|a| self.closed(a, &mut memo, &mut stack))
            {
                to_close.push(cid);
            }
        }
        for cid in to_close {
            let cell = self.cells.get_mut(&cid).unwrap();
            cell.retracted = true;
            cell.cascaded = true;
        }
    }

    /// The memoized cascade closure (weave.py `_cascade_retractions.closed`):
    /// true iff `cid` is a cascade_root or descends from one. A cycle fails
    /// closed on ambiguity.
    fn closed(
        &self,
        cid: &str,
        memo: &mut HashMap<String, bool>,
        stack: &mut HashSet<String>,
    ) -> bool {
        if let Some(r) = memo.get(cid) {
            return *r;
        }
        if stack.contains(cid) {
            return true; // cycle guard → fail closed on ambiguity
        }
        let Some(cell) = self.cells.get(cid) else {
            memo.insert(cid.to_string(), false);
            return false;
        };
        if cell.cascade_root {
            memo.insert(cid.to_string(), true);
            return true;
        }
        stack.insert(cid.to_string());
        let ancestors = self.authority_ancestors(cell);
        let res = ancestors.iter().any(|a| self.closed(a, memo, stack));
        stack.remove(cid);
        memo.insert(cid.to_string(), res);
        res
    }

    // -- projections -------------------------------------------------------

    /// A deterministic digest over the folded logical state (FOLD §6): a
    /// content-addressed root over canonical CellState records, sorted by
    /// cell id. Covers logical state, not history.
    pub fn state_root(&mut self) -> String {
        self.cascade_retractions();
        let mut records = Vec::new();
        for (cid, c) in &self.cells {
            let mut edges: Vec<Value> = c
                .edges_out
                .iter()
                .map(|e| json!([e["rel"], e["src"], e["dst"]]))
                .collect();
            edges.sort_by(value_cmp);
            let mut attestations: Vec<Value> = c
                .attestations
                .iter()
                .map(|a| {
                    json!([
                        a["by"],
                        a.get("claim").cloned().unwrap_or_else(|| json!(""))
                    ])
                })
                .collect();
            attestations.sort_by(value_cmp);
            records.push(json!([
                cid,
                c.r#type,
                c.content,
                c.version,
                c.retracted,
                edges,
                attestations,
                c.content_heads,
                c.in_conflict,
                c.redacted,
                c.cascade_root,
                c.cascaded,
                c.lease_expired,
                c.superseded_by,
                c.cascade_mode,
            ]));
        }
        content_id(&json!({"state_root": records}), "snapshot")
    }

    pub fn of_type(&mut self, t: &str) -> Vec<&Cell> {
        self.cascade_retractions();
        self.cells
            .values()
            .filter(|c| c.r#type == t && !c.retracted)
            .collect()
    }

    // -- EffectReceipt reconciliation (WEFT §8) ----------------------------

    /// A deterministic canonical-order key for a `result` receipt: the causal
    /// position of the ASSERT that created it — the creating event's ancestor
    /// count (strictly increasing with lamport on a linear log), with the
    /// creating event id as a stable tiebreak. The SAME (lamport, event_id)
    /// total order the fold itself uses, so "latest" folds identically
    /// regardless of arrival order.
    fn receipt_order_key(&self, cell: &Cell) -> (usize, String) {
        let eid = cell.provenance.first().cloned().unwrap_or_default();
        (self.ancestors.get(&eid).map(HashSet::len).unwrap_or(0), eid)
    }

    /// All live `result` receipts whose `idempotency` == key, in canonical
    /// (fold) order — earliest first, latest last. Pure read; no mutation.
    pub fn receipts_for_idempotency(&mut self, key: &str) -> Vec<&Cell> {
        self.cascade_retractions();
        let mut rs: Vec<((usize, String), &Cell)> = self
            .cells
            .values()
            .filter(|c| {
                c.r#type == "result"
                    && !c.retracted
                    && c.content.get("idempotency").and_then(Value::as_str) == Some(key)
            })
            .map(|c| (self.receipt_order_key(c), c))
            .collect();
        rs.sort_by(|a, b| a.0.cmp(&b.0));
        rs.into_iter().map(|(_, c)| c).collect()
    }

    /// The LATEST DEFINITE receipt for this idempotency key — the one whose
    /// status is NOT UNKNOWN — or None if all are UNKNOWN (the outcome is
    /// still unobserved) or none exist. A later definite receipt
    /// (SUCCEEDED/FAILED) supersedes an earlier UNKNOWN for the same logical
    /// op; "latest" is the fold's canonical order, so the answer is
    /// deterministic and time-travels like all state. Pure read.
    pub fn canonical_for_idempotency(&mut self, key: &str) -> Option<&Cell> {
        self.receipts_for_idempotency(key)
            .into_iter()
            .rfind(|c| c.content.get("status").and_then(Value::as_str) != Some(UNKNOWN))
    }
}

/// Total order over JSON values matching Python's list/string sort for the
/// shapes state_root sorts ([rel,src,dst] / [by,claim] string arrays).
fn value_cmp(a: &Value, b: &Value) -> std::cmp::Ordering {
    match (a, b) {
        (Value::Array(x), Value::Array(y)) => {
            for (xi, yi) in x.iter().zip(y.iter()) {
                let ord = value_cmp(xi, yi);
                if ord != std::cmp::Ordering::Equal {
                    return ord;
                }
            }
            x.len().cmp(&y.len())
        }
        (Value::String(x), Value::String(y)) => x.cmp(y),
        _ => format!("{a}").cmp(&format!("{b}")),
    }
}
