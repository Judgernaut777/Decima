//! The SQLite-persisted Weft — port of heartbeat/decima/weft.py's storage and
//! on-read verification (the in-memory `weft` module keeps the v1 semantics).
//!
//! Observable behavior matched (weft.py):
//!   - the same table shape: `events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
//!     id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, author TEXT NOT NULL,
//!     sig TEXT NOT NULL)`;
//!   - the same stored `payload` TEXT bytes: `json.dumps(payload,
//!     sort_keys=True)` with the DEFAULT separators (", ", ": ") and
//!     ensure_ascii=True (see `hashing::python_dumps_sorted`) — NOT the
//!     compact canonical bytes the content id hashes;
//!   - append (linear path): parents=[head], lamport = 1 + max(parent
//!     lamports, 0), NFC on entry, eid = content_id(payload, "event"), the
//!     author signs the eid string's UTF-8 bytes;
//!   - warm start (`_load_head`): head = id of the highest-seq row, lamport =
//!     that row's payload["lamport"]; empty log → (None, 0);
//!   - `events()` reads in seq order VERIFYING each row: the content id is
//!     recomputed from the parsed payload (mismatch → `ContentTampered`), and
//!     the author's signature is verified against the keyring (failure →
//!     `BadSignature`) — fail closed on tamper, exactly like weft.py raising
//!     WeftError.
//!
//! NOT ported (none exercised by the extended vectors): key-rotation
//! succession chains (`_rot_*`), `ingest` (sync acceptance), merge forks
//! (explicit parent sets), and seq windows (`upto_seq` / `from_seq`).

use rusqlite::Connection;
use serde_json::{json, Value};

use crate::crypto::Keyring;
use crate::hashing::{content_id, nfc_deep, python_dumps_sorted};
use crate::weft::{Event, WeftError, VERBS};

pub struct WeftDb<'k> {
    pub keyring: &'k Keyring,
    conn: Connection,
    head: Option<String>,
    lamport: i64,
}

impl<'k> WeftDb<'k> {
    /// Open (or create) a Weft database, creating the events table if needed
    /// and recovering head/lamport from the existing log (warm start —
    /// weft.py `__init__` + `_load_head`).
    pub fn open(path: &std::path::Path, keyring: &'k Keyring) -> Result<Self, WeftError> {
        let conn = Connection::open(path).map_err(|e| WeftError::Storage(e.to_string()))?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS events (
                 seq INTEGER PRIMARY KEY AUTOINCREMENT,
                 id TEXT UNIQUE NOT NULL,
                 payload TEXT NOT NULL,
                 author TEXT NOT NULL,
                 sig TEXT NOT NULL
             )",
        )
        .map_err(|e| WeftError::Storage(e.to_string()))?;
        let (head, lamport) = load_head(&conn)?;
        Ok(WeftDb {
            keyring,
            conn,
            head,
            lamport,
        })
    }

    /// weft.py `_load_head`: the id + payload lamport of the highest-seq row,
    /// or (None, 0) on an empty log.
    pub fn head(&self) -> Option<&str> {
        self.head.as_deref()
    }

    pub fn lamport(&self) -> i64 {
        self.lamport
    }

    /// Linear append (weft.py `append` with parents=None). The INSERT stores
    /// the Python `json.dumps(payload, sort_keys=True)` bytes — default
    /// separators, ensure_ascii — byte-for-byte.
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
        let stored = python_dumps_sorted(&payload);
        self.conn
            .execute(
                "INSERT INTO events (id, payload, author, sig) VALUES (?1,?2,?3,?4)",
                rusqlite::params![eid, stored, author_pid, sig],
            )
            .map_err(|e| WeftError::Storage(e.to_string()))?;
        self.head = Some(eid.clone());
        self.lamport = lamport;
        let seq = self.seq_of(&eid)?;
        Ok(Event {
            seq,
            id: eid,
            parents,
            author: author_pid.to_string(),
            authorized: authorized.map(str::to_string),
            verb: verb.to_string(),
            body: payload["body"].clone(),
            lamport,
            sig,
        })
    }

    fn seq_of(&self, eid: &str) -> Result<i64, WeftError> {
        self.conn
            .query_row("SELECT seq FROM events WHERE id=?1", [eid], |r| r.get(0))
            .map_err(|e| WeftError::Storage(e.to_string()))
    }

    /// Read every event in causal (seq) order, VERIFYING each row on read
    /// (Laws 1 & 4 — weft.py `events`): recompute the content id from the
    /// stored payload (tamper → `ContentTampered`) and verify the author's
    /// signature (`BadSignature`). Fail closed: the FIRST bad row is an error,
    /// never silently skipped.
    pub fn events(&self) -> Result<Vec<Event>, WeftError> {
        let mut stmt = self
            .conn
            .prepare("SELECT seq, id, payload, author, sig FROM events ORDER BY seq ASC")
            .map_err(|e| WeftError::Storage(e.to_string()))?;
        let rows = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            })
            .map_err(|e| WeftError::Storage(e.to_string()))?;
        let mut out = Vec::new();
        for row in rows {
            let (seq, eid, payload_text, author, sig) =
                row.map_err(|e| WeftError::Storage(e.to_string()))?;
            let payload: Value = serde_json::from_str(&payload_text)
                .map_err(|_| WeftError::ContentTampered { seq })?;
            if content_id(&payload, "event") != eid {
                return Err(WeftError::ContentTampered { seq });
            }
            if !self.keyring.verify(&author, &eid, &sig) {
                return Err(WeftError::BadSignature { seq });
            }
            out.push(Event {
                seq,
                id: eid,
                parents: serde_json::from_value(payload["parents"].clone())
                    .map_err(|_| WeftError::ContentTampered { seq })?,
                author,
                authorized: payload["authorized"].as_str().map(str::to_string),
                verb: payload["verb"].as_str().unwrap_or("").to_string(),
                body: payload["body"].clone(),
                lamport: payload["lamport"].as_i64().unwrap_or(0),
                sig,
            });
        }
        Ok(out)
    }

    /// The exact stored `payload` TEXT bytes per row (seq order) — the
    /// persistence-format surface the extended vectors pin.
    pub fn stored_payloads(&self) -> Result<Vec<(i64, String)>, WeftError> {
        let mut stmt = self
            .conn
            .prepare("SELECT seq, payload FROM events ORDER BY seq ASC")
            .map_err(|e| WeftError::Storage(e.to_string()))?;
        let rows = stmt
            .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
            .map_err(|e| WeftError::Storage(e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| WeftError::Storage(e.to_string()))
    }

    pub fn count(&self) -> Result<usize, WeftError> {
        let n: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM events", [], |r| r.get(0))
            .map_err(|e| WeftError::Storage(e.to_string()))?;
        Ok(n as usize)
    }

    /// Direct row mutation — the TAMPER SEAM the conformance tests use to
    /// prove on-read verification fails closed (weft.py's table is likewise
    /// just a SQLite table anyone holding the file can edit).
    pub fn tamper_row(&self, sql: &str) -> Result<usize, WeftError> {
        self.conn
            .execute(sql, [])
            .map_err(|e| WeftError::Storage(e.to_string()))
    }
}

/// weft.py `_load_head` on a bare connection.
fn load_head(conn: &Connection) -> Result<(Option<String>, i64), WeftError> {
    let row: Option<(String, String)> = conn
        .query_row(
            "SELECT id, payload FROM events ORDER BY seq DESC LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .ok();
    match row {
        None => Ok((None, 0)),
        Some((id, payload_text)) => {
            let payload: Value = serde_json::from_str(&payload_text)
                .map_err(|e| WeftError::Storage(e.to_string()))?;
            Ok((Some(id), payload["lamport"].as_i64().unwrap_or(0)))
        }
    }
}
