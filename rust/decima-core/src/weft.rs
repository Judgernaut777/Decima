//! The Weft — the append-only, signed, content-addressed log (in-memory).
//!
//! Law 1: nothing happens off the Log. Every state change is one Event
//! appended here. There is no UPDATE and no DELETE — only INSERT.
//!
//! Port of heartbeat/decima/weft.py, scoped to what the reference vector
//! script exercises: the linear append path (`parents=None`), Lamport clocks,
//! NFC-on-entry, content-addressed event ids, and Ed25519 author signatures.
//! The reference stores events in SQLite and folds key-rotation succession
//! chains; neither affects the observable bytes of a linear append script, so
//! this port keeps events in memory and defers rotation/ingest/merge forks.

use serde_json::{json, Value};

use crate::crypto::Keyring;
use crate::hashing::{content_id, nfc_deep};

pub const ASSERT: &str = "ASSERT";
pub const RETRACT: &str = "RETRACT";
pub const INVOKE: &str = "INVOKE";
pub const ATTEST: &str = "ATTEST";
pub const VERBS: [&str; 4] = [ASSERT, RETRACT, INVOKE, ATTEST];

#[derive(Debug, Clone)]
pub struct Event {
    /// 1-based append position (SQLite AUTOINCREMENT analogue).
    pub seq: i64,
    pub id: String,
    pub parents: Vec<String>,
    pub author: String,
    pub authorized: Option<String>,
    pub verb: String,
    pub body: Value,
    pub lamport: i64,
    pub sig: String,
}

impl Event {
    /// Everything that defines the event's identity (content + cause). The
    /// signature is NOT part of the id — it attests authorship of the id.
    pub fn hashed_payload(&self) -> Value {
        json!({
            "parents": self.parents,
            "author": self.author,
            "authorized": self.authorized,
            "verb": self.verb,
            "body": self.body,
            "lamport": self.lamport,
        })
    }
}

#[derive(Debug)]
pub enum WeftError {
    UnknownVerb(String),
}

impl std::fmt::Display for WeftError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WeftError::UnknownVerb(v) => write!(f, "unknown verb {v:?}"),
        }
    }
}

impl std::error::Error for WeftError {}

pub struct Weft<'k> {
    pub keyring: &'k Keyring,
    events: Vec<Event>,
    head: Option<String>,
    lamport: i64,
}

impl<'k> Weft<'k> {
    pub fn new(keyring: &'k Keyring) -> Self {
        Weft {
            keyring,
            events: Vec::new(),
            head: None,
            lamport: 0,
        }
    }

    /// Linear append (weft.Weft.append with parents=None): descend from the
    /// current head, lamport = 1 + max(parent lamports, default 0), body is
    /// NFC-normalized on the way in, eid = content_id(payload, "event"), and
    /// the author signs the eid STRING's UTF-8 bytes.
    pub fn append(
        &mut self,
        author_pid: &str,
        verb: &str,
        body: Value,
        authorized: Option<&str>,
    ) -> Result<Event, WeftError> {
        if !VERBS.contains(&verb) {
            return Err(WeftError::UnknownVerb(verb.to_string()));
        }
        let (parents, parent_lamports): (Vec<String>, Vec<i64>) = match &self.head {
            Some(h) => (vec![h.clone()], vec![self.lamport]),
            None => (vec![], vec![]),
        };
        let lamport = 1 + parent_lamports.iter().max().copied().unwrap_or(0);
        let payload = json!({
            "parents": parents,
            "author": author_pid,
            "authorized": authorized,
            "verb": verb,
            "body": nfc_deep(&body),
            "lamport": lamport,
        });
        let eid = content_id(&payload, "event");
        let sig = self.keyring.sign(author_pid, &eid);
        let ev = Event {
            seq: self.events.len() as i64 + 1,
            id: eid,
            parents,
            author: author_pid.to_string(),
            authorized: authorized.map(|s| s.to_string()),
            verb: verb.to_string(),
            body: payload["body"].clone(),
            lamport,
            sig,
        };
        self.head = Some(ev.id.clone());
        self.lamport = ev.lamport;
        self.events.push(ev.clone());
        Ok(ev)
    }

    pub fn events(&self) -> &[Event] {
        &self.events
    }

    pub fn count(&self) -> usize {
        self.events.len()
    }
}
