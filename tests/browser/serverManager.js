// serverManager.js — spawn/stop/restart the REAL Decima Shell for a spec.
//
// Each instance owns a persistent temp Weft db and a fixed keyring seed, so a restart
// re-derives the SAME pairing secret and reopens the SAME durable store. Starting the
// backend from scratch also rebuilds every disposable projection from the Weft, so a
// restart doubles as the "projection rebuild" durability check.

const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
// Path used only for PYTHONPATH so the launcher can import the `decima` package.
// Defaults to the repo checkout (matches tests/browser/run.sh: `${TESTENV:=$REPO_ROOT}`);
// set TESTENV if decima's deps live in a separate site-packages env.
const TESTENV = process.env.TESTENV || REPO_ROOT;

// A fixed 32-byte seed (hex) → deterministic identity across restarts.
const DEFAULT_SEED = "00".repeat(32);

class DecimaServer {
  constructor(opts = {}) {
    this.seed = opts.seed || DEFAULT_SEED;
    // When true, the launcher creates ONE bounded agent via the canonical kernel path so
    // the browser can drive the gated terminate/revoke -> approval flow (the Shell itself
    // never spawns agents; the runtime does — the harness stands in for it).
    this.seedAgent = !!opts.seedAgent;
    this.dbDir = fs.mkdtempSync(path.join(os.tmpdir(), "decima-qual-"));
    this.dbPath = path.join(this.dbDir, "weft.db");
    this.proc = null;
    this.baseURL = null;
    this.pairing = null;
    this.seedAgentId = null;
  }

  _spawn() {
    return new Promise((resolve, reject) => {
      const env = Object.assign({}, process.env, {
        PYTHONPATH: `${TESTENV}:${REPO_ROOT}`,
      });
      const argv = [
        "-m",
        "tests.browser.serve_fixture",
        "--db",
        this.dbPath,
        "--port",
        "0",
        "--seed",
        this.seed,
      ];
      if (this.seedAgent) argv.push("--seed-agent");
      const proc = spawn("python3", argv, {
        cwd: REPO_ROOT,
        env,
        stdio: ["ignore", "pipe", "pipe"],
      });
      this.proc = proc;

      let buf = "";
      let settled = false;
      const onData = (chunk) => {
        buf += chunk.toString();
        const pm = buf.match(/DECIMA_SHELL_PAIRING=(\S+)/);
        const rm = buf.match(/DECIMA_SHELL_READY=(\S+)/);
        const am = buf.match(/DECIMA_SEED_AGENT=(\S+)/);
        if (pm) this.pairing = pm[1];
        if (am) this.seedAgentId = am[1];
        if (rm && !settled) {
          settled = true;
          // Normalize: strip trailing slash so callers build `${baseURL}/api/...`.
          this.baseURL = rm[1].replace(/\/$/, "");
          resolve();
        }
      };
      proc.stdout.on("data", onData);
      proc.stderr.on("data", (c) => {
        buf += c.toString();
      });
      proc.on("exit", (code) => {
        if (!settled) {
          settled = true;
          reject(new Error(`serve_fixture exited early (code ${code}):\n${buf}`));
        }
      });
      setTimeout(() => {
        if (!settled) {
          settled = true;
          reject(new Error(`serve_fixture did not become ready in 20s:\n${buf}`));
        }
      }, 20_000);
    });
  }

  async start() {
    await this._spawn();
    return this;
  }

  async stop() {
    if (!this.proc) return;
    const proc = this.proc;
    this.proc = null;
    await new Promise((resolve) => {
      proc.on("exit", () => resolve());
      proc.kill("SIGTERM");
      setTimeout(() => {
        try {
          proc.kill("SIGKILL");
        } catch (_) {}
        resolve();
      }, 5_000);
    });
  }

  // Restart over the SAME db + seed: same identity, reopened Weft, rebuilt projections.
  async restart() {
    await this.stop();
    await this._spawn();
    return this;
  }

  cleanup() {
    try {
      fs.rmSync(this.dbDir, { recursive: true, force: true });
    } catch (_) {}
  }
}

module.exports = { DecimaServer, REPO_ROOT, TESTENV };
