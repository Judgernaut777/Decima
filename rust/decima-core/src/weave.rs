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
//! NOT yet ported: the ATTEST adjudication-collapse and trusted-promotion
//! branches (the extended vectors exercise only plain attestations), the
//! or-set/counter/append-log/sequence/map reducers, lease-expiry derivation
//! (frontier_lamport/lease_status — the invoke tally itself IS folded),
//! snapshots, and time-travel windows.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde_json::{json, Value};

use crate::hashing::content_id;
use crate::weft::{Event, Weft, ASSERT, ATTEST, INVOKE, RETRACT};

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
            // weave.py ATTEST (the subset the extended vectors exercise): the
            // attestation folds onto the TARGET cell as {by, claim, event} —
            // evidence any signer may leave; the fold never gates on who
            // signed. NOT ported: the 'adjudicates' collapse of MV heads and
            // trusted-promotion quarantine lift (no vector exercises them).
            let target_id = b.get("target_cell").and_then(Value::as_str);
            if let Some(tid) = target_id {
                if let Some(target) = self.cells.get_mut(tid) {
                    let claim = b
                        .get("claim")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    target.attestations.push(json!({
                        "by": ev.author,
                        "claim": claim,
                        "event": ev.id,
                    }));
                }
            }
        }
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

    /// Derived pass: fail closed any cell whose authority descends —
    /// transitively — from a cascade_root. Pure function of the folded graph;
    /// clears its own prior marks first, so it is idempotent.
    pub fn cascade_retractions(&mut self) {
        for c in self.cells.values_mut() {
            if c.cascaded {
                c.cascaded = false;
                c.retracted = false;
            }
        }
        // Reachability closure: descendants of any cascade_root fail closed.
        // (Lease-expiry derivation is not part of the ported subset.)
        let mut changed = true;
        while changed {
            changed = false;
            let snapshot: Vec<(String, bool, Vec<String>)> = self
                .cells
                .values()
                .map(|c| {
                    (
                        c.id.clone(),
                        c.retracted || c.cascade_root,
                        self.authority_ancestors(c),
                    )
                })
                .collect();
            let closed: HashSet<&String> = snapshot
                .iter()
                .filter(|(_, dead, _)| *dead)
                .map(|(id, _, _)| id)
                .collect();
            let roots: HashSet<String> = self
                .cells
                .values()
                .filter(|c| c.cascade_root)
                .map(|c| c.id.clone())
                .collect();
            for (id, _, ancestors) in &snapshot {
                if closed.contains(id) {
                    continue;
                }
                // Fail closed on ambiguous ancestry: a cell whose ancestor is
                // missing or non-live is not widened back to live here.
                if ancestors.iter().any(|a| closed.contains(a)) {
                    let cell = self.cells.get_mut(id).unwrap();
                    cell.retracted = true;
                    if !roots.contains(id) {
                        cell.cascaded = true;
                    }
                    changed = true;
                }
            }
        }
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
