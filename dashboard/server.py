#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Local SN25 miner / project dashboard.

  ./run.sh dashboard
  ./run.sh dashboard --port 8787

Serves a live overview of how the miner and this repo fit together.
Never exposes seeds, JWTs, or private key material.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
DIAGRAMS = ROOT / "diagrams" / "readme"
STATE_DIR = Path(os.environ.get("URNETWORK_STATE_DIR") or (Path.home() / ".urnetwork"))
PM2_NAME = os.environ.get("PM2_NAME", "sn25-miner")
PM2_NAME_ALL = os.environ.get("PM2_NAME_ALL", "sn25-miner-all")
PROVIDER = os.environ.get("PROVIDER", "")


def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "TERM": "dumb"},
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def find_provider() -> str | None:
    if PROVIDER and os.access(PROVIDER, os.X_OK):
        return PROVIDER
    candidates = [
        Path.home() / ".local/share/urnetwork-provider/bin/urnetwork",
        Path.home() / ".local/bin/provider",
        Path.home() / ".local/bin/urnetwork",
        ROOT / "miner" / "provider",
        ROOT / "provider",
        ROOT / "cli" / "miner" / "miner",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    for name in ("provider", "urnetwork"):
        code, out, _ = _run(["bash", "-lc", f"command -v {name}"], timeout=2)
        if code == 0 and out.strip():
            return out.strip()
    return None


def read_json_file(path: Path):
    try:
        with path.open() as f:
            return json.load(f)
    except Exception:
        return None


def provider_status_json(bin_path: str | None):
    if not bin_path:
        return None, "provider binary not found"
    code, out, err = _run([bin_path, "status", "--json"], timeout=10)
    if not out.strip():
        return None, err.strip() or f"status exit {code}"
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        return None, "status --json was not valid JSON"


def provider_doctor_text(bin_path: str | None) -> str:
    if not bin_path:
        return "provider binary not found"
    _, out, err = _run([bin_path, "doctor"], timeout=12)
    text = (out or err or "").strip()
    return text or "(no doctor output)"


def pm2_apps():
    code, out, _ = _run(["pm2", "jlist"], timeout=6)
    if code != 0 or not out.strip():
        return []
    try:
        apps = json.loads(out)
    except json.JSONDecodeError:
        return []
    wanted = {PM2_NAME, PM2_NAME_ALL}
    rows = []
    for a in apps:
        name = a.get("name") or ""
        if name not in wanted:
            continue
        env = a.get("pm2_env") or {}
        started = env.get("pm_uptime")
        uptime_s = None
        if isinstance(started, (int, float)) and started > 0:
            uptime_s = max(0, int(time.time() - started / 1000))
        rows.append(
            {
                "name": name,
                "id": a.get("pm_id"),
                "pid": a.get("pid"),
                "status": env.get("status"),
                "restarts": env.get("restart_time"),
                "uptime_s": uptime_s,
                "script": env.get("pm_exec_path"),
                "args": env.get("args") or [],
                "cwd": env.get("pm_cwd"),
                "memory": (a.get("monit") or {}).get("memory"),
                "cpu": (a.get("monit") or {}).get("cpu"),
            }
        )
    return rows


def operators_summary():
    path = STATE_DIR / "operators.yml"
    listed = []
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("- domain:"):
                listed.append(line.split(":", 1)[1].strip())
    auth_dirs = []
    op_root = STATE_DIR / "operators"
    if op_root.is_dir():
        for child in sorted(op_root.iterdir()):
            if child.is_dir():
                auth_dirs.append(
                    {
                        "domain": child.name,
                        "has_jwt": (child / "jwt").is_file()
                        or (child / ".provider.jwt").is_file(),
                    }
                )
    return {"list_file": str(path) if path.is_file() else None, "domains": listed, "local": auth_dirs}


def project_map():
    entries = [
        ("miner/", "Provider / miner binary — carry traffic for network operators"),
        ("validator/", "Validator: /verify trails, scoring, commit-reveal weights"),
        ("cli/", "Operator and chain CLI tooling"),
        ("ledger/", "Settlement v2 Merkle sum-tree verify / challenge / demand"),
        ("mainnet/", "EVM contract suite + deployment / bootstrap tests"),
        ("deploy/", "Signed policy and deploy artifacts"),
        ("diagrams/", "Mechanism diagrams used in README"),
        ("dashboard/", "This local miner / project dashboard"),
        ("run.sh", "PM2 launcher for the miner (+ this dashboard)"),
        ("WHITEPAPER.md", "Full protocol specification"),
        ("VALIDATOR.md", "Routing-verification protocol"),
        ("CAPTURETHEFLAG.md", "CTF coverage auction (epoch 8+)"),
    ]
    resolved = []
    for name, desc in entries:
        path = ROOT / name.rstrip("/")
        resolved.append(
            {
                "path": name,
                "desc": desc,
                "exists": path.exists() or (ROOT / name).exists(),
            }
        )
    return resolved


def how_it_works():
    return {
        "title": "How SN25 mining works",
        "steps": [
            {
                "who": "You (provider)",
                "what": "Run the miner under PM2. It signs in to a network operator, registers a client_id, and carries egress traffic.",
            },
            {
                "who": "Network operator",
                "what": "Sells privacy traffic, assigns routes, publishes usage, and commits a payout root each epoch.",
            },
            {
                "who": "Validators",
                "what": "Walk /verify trails through providers, measure liveness and latency, then set weights every tempo.",
            },
            {
                "who": "Yuma Consensus",
                "what": "Turns validator weights into miner emission (α / $UR). Pool providers claim from the NO vault; head fleets are paid on their own UID.",
            },
            {
                "who": "Settlement challenge",
                "what": "After a v2 payout root commit, a 14,400-block (~48h) window lets anyone challenge bad accounts or demand missing data before finalize.",
            },
        ],
        "tiers": [
            {"name": "Pool (tail)", "detail": "Join an operator pool — no own UID needed; claim α from the NO each epoch."},
            {"name": "Head (fleet)", "detail": "Large fleets can register their own miner UID and get paid directly by Yuma."},
        ],
    }


def _b58encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(data, "big")
    chars: list[str] = []
    while number:
        number, remainder = divmod(number, 58)
        chars.append(alphabet[remainder])
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return ("1" * pad) + ("".join(reversed(chars)) or "1")


def _ss58(pubkey: bytes, prefix: int = 42) -> str:
    import hashlib

    payload = bytes([prefix]) + pubkey
    checksum = hashlib.blake2b(b"SS58PRE" + payload, digest_size=64).digest()[:2]
    return _b58encode(payload + checksum)


def _statement_from_message(text: str) -> dict | None:
    if "\n" not in text:
        return None
    try:
        value = json.loads(text.split("\n", 1)[1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _as_bytes(value) -> bytes | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(n, int) and 0 <= n <= 255 for n in value):
        return None
    return bytes(value)


def _hex_short(raw: bytes | None) -> str | None:
    if not raw:
        return None
    encoded = raw.hex()
    if len(encoded) <= 16:
        return encoded
    return f"{encoded[:8]}…{encoded[-8:]}"


def _uuid(raw: bytes | None) -> str | None:
    if raw is None or len(raw) != 16:
        return None
    encoded = raw.hex()
    return f"{encoded[0:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:32]}"


def _statement_detail(statement: dict, now: int) -> dict:
    """Public fields of a wallet statement. No nonce, signature, or seed."""
    domain = statement.get("domain") if isinstance(statement.get("domain"), dict) else {}
    prospective = statement.get("prospective") if isinstance(statement.get("prospective"), dict) else {}
    edge = prospective.get("boundary") if isinstance(prospective.get("boundary"), dict) else {}
    coldkey = _as_bytes(statement.get("coldkey"))
    expires = int(statement.get("expires_at") or 0)
    return {
        "schema": statement.get("schema"),
        "chain_id": domain.get("chain_id"),
        "netuid": domain.get("netuid"),
        "coordinator": domain.get("coordinator"),
        "settlement_vault": domain.get("settlement_vault"),
        "client_id": _uuid(_as_bytes(statement.get("client_id"))),
        "network_id": _uuid(_as_bytes(statement.get("network_id"))),
        "user_id": _uuid(_as_bytes(statement.get("user_id"))),
        "coldkey_ss58": _ss58(coldkey) if coldkey and len(coldkey) == 32 else None,
        "generation": statement.get("generation"),
        "from_epoch": statement.get("from_epoch"),
        "through_epoch": statement.get("through_epoch"),
        "issued_at": statement.get("issued_at"),
        "expires_at": expires or None,
        "seconds_left": max(0, expires - now) if expires else None,
        "expired": bool(expires and expires <= now),
        "boundary_epoch": edge.get("epoch"),
        "boundary_block": edge.get("block"),
        "boundary_hash": _hex_short(_as_bytes(edge.get("hash"))),
        "signer": prospective.get("signer"),
    }


def _coldkey_from_env() -> str:
    env = ROOT / ".env"
    if not env.is_file():
        return ""
    for line in env.read_text().splitlines():
        if line.startswith("COLDKEY_SS58="):
            return line.split("=", 1)[1].strip()
    return ""


def wallet_challenge_view() -> dict:
    """Public payout-wallet challenge and the accepted consent. No seeds."""
    now = int(time.time())
    coldkey = _coldkey_from_env()
    challenge: dict = {"present": False}
    challenge_path = ROOT / ".secrets" / "wallet-challenge.txt"
    if challenge_path.is_file():
        text = challenge_path.read_text()
        statement = _statement_from_message(text) or {}
        detail = _statement_detail(statement, now)
        challenge = {
            "present": True,
            "expired": detail["expired"],
            "from_epoch": detail["from_epoch"],
            "through_epoch": detail["through_epoch"],
            "generation": detail["generation"],
            "issued_at": detail["issued_at"],
            "expires_at": detail["expires_at"],
            "seconds_left": detail["seconds_left"],
            "boundary_epoch": detail["boundary_epoch"],
            "message": text.strip("\n"),
            "detail": detail,
        }
    accepted = None
    history = STATE_DIR / ".provider.jwt.wallet-consent" / "history.json"
    data = read_json_file(history) if history.is_file() else None
    records = (data or {}).get("records") or []
    if records:
        record = records[-1]
        message = ((record.get("original") or {}).get("message")) or ""
        statement = _statement_from_message(message) or {}
        raw_key = statement.get("coldkey")
        ss58 = ""
        if isinstance(raw_key, list) and len(raw_key) == 32 and all(isinstance(n, int) for n in raw_key):
            ss58 = _ss58(bytes(raw_key))
        detail = _statement_detail(statement, now)
        accepted = {
            "acknowledged": bool(record.get("acknowledged")),
            "generation": detail["generation"],
            "from_epoch": detail["from_epoch"],
            "through_epoch": detail["through_epoch"],
            "coldkey_ss58": ss58 or detail["coldkey_ss58"] or "",
            "matches_env": bool((ss58 or detail["coldkey_ss58"]) and coldkey and (ss58 or detail["coldkey_ss58"]) == coldkey),
            "detail": detail,
        }
    return {"coldkey_ss58": coldkey, "accepted": accepted, "challenge": challenge, "history": _record_wallet_status(coldkey, challenge, accepted)}


def _status_db():
    path = ROOT / ".secrets" / "wallet-status.sqlite"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_challenge_status (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            coldkey_ss58 TEXT,
            generation INTEGER,
            from_epoch INTEGER,
            through_epoch INTEGER,
            issued_at INTEGER,
            expires_at INTEGER,
            boundary_epoch INTEGER,
            client_id TEXT,
            netuid INTEGER,
            matches_env INTEGER,
            acknowledged INTEGER,
            updated_at TEXT NOT NULL,
            UNIQUE (kind, issued_at, generation, from_epoch, through_epoch)
        )
        """
    )
    return conn


def _save_status(conn, row: dict) -> None:
    if row.get("issued_at") is None:
        return
    conn.execute(
        """
        INSERT INTO wallet_challenge_status (
            kind, status, coldkey_ss58, generation, from_epoch, through_epoch,
            issued_at, expires_at, boundary_epoch, client_id, netuid,
            matches_env, acknowledged, updated_at
        ) VALUES (
            :kind, :status, :coldkey_ss58, :generation, :from_epoch, :through_epoch,
            :issued_at, :expires_at, :boundary_epoch, :client_id, :netuid,
            :matches_env, :acknowledged, :updated_at
        )
        ON CONFLICT(kind, issued_at, generation, from_epoch, through_epoch) DO UPDATE SET
            status = excluded.status,
            expires_at = excluded.expires_at,
            matches_env = excluded.matches_env,
            acknowledged = excluded.acknowledged,
            coldkey_ss58 = excluded.coldkey_ss58,
            updated_at = excluded.updated_at
        """,
        row,
    )


def _record_wallet_status(coldkey: str, challenge: dict, accepted: dict | None) -> list[dict]:
    """Persist public challenge and consent status. No statement, seed, or signature."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = _status_db()
    except sqlite3.Error:
        return []
    try:
        detail = (challenge or {}).get("detail") or {}
        if challenge.get("present"):
            same = bool(detail.get("coldkey_ss58") and coldkey and detail.get("coldkey_ss58") == coldkey)
            accepted_detail = (accepted or {}).get("detail") or {}
            signed = bool(
                accepted
                and accepted.get("acknowledged")
                and detail.get("issued_at") == accepted_detail.get("issued_at")
                and detail.get("generation") == accepted.get("generation")
                and detail.get("from_epoch") == accepted.get("from_epoch")
                and detail.get("through_epoch") == accepted.get("through_epoch")
            )
            if signed:
                challenge_status = "signed"
            elif challenge.get("expired"):
                challenge_status = "expired"
            else:
                challenge_status = "open"
            _save_status(
                conn,
                {
                    "kind": "challenge",
                    "status": challenge_status,
                    "coldkey_ss58": detail.get("coldkey_ss58") or coldkey or None,
                    "generation": detail.get("generation"),
                    "from_epoch": detail.get("from_epoch"),
                    "through_epoch": detail.get("through_epoch"),
                    "issued_at": detail.get("issued_at"),
                    "expires_at": detail.get("expires_at"),
                    "boundary_epoch": detail.get("boundary_epoch"),
                    "client_id": detail.get("client_id"),
                    "netuid": detail.get("netuid"),
                    "matches_env": 1 if same else 0,
                    "acknowledged": 1 if signed else 0,
                    "updated_at": now,
                },
            )
        if accepted:
            detail = accepted.get("detail") or {}
            _save_status(
                conn,
                {
                    "kind": "consent",
                    "status": "accepted" if accepted.get("acknowledged") else "pending",
                    "coldkey_ss58": accepted.get("coldkey_ss58") or detail.get("coldkey_ss58") or None,
                    "generation": accepted.get("generation"),
                    "from_epoch": accepted.get("from_epoch"),
                    "through_epoch": accepted.get("through_epoch"),
                    "issued_at": detail.get("issued_at"),
                    "expires_at": detail.get("expires_at"),
                    "boundary_epoch": detail.get("boundary_epoch"),
                    "client_id": detail.get("client_id"),
                    "netuid": detail.get("netuid"),
                    "matches_env": 1 if accepted.get("matches_env") else 0,
                    "acknowledged": 1 if accepted.get("acknowledged") else 0,
                    "updated_at": now,
                },
            )
        conn.commit()
        rows = conn.execute(
            """
            SELECT kind, status, coldkey_ss58, generation, from_epoch, through_epoch,
                   issued_at, expires_at, boundary_epoch, client_id, netuid,
                   matches_env, acknowledged, updated_at
            FROM wallet_challenge_status
            ORDER BY issued_at DESC, id DESC
            LIMIT 20
            """
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def secrets_presence():
    secrets = ROOT / ".secrets"
    return {
        "dir": str(secrets),
        "hotkey_seed": (secrets / "hotkey.seed").is_file(),
        "coldkey_seed": (secrets / "coldkey.seed").is_file(),
        "wallet_challenge": (secrets / "wallet-challenge.txt").is_file(),
        "wallet_signature": (secrets / "wallet-signature.hex").is_file(),
        # never return contents
    }


def overview_payload():
    bin_path = find_provider()
    status, status_err = provider_status_json(bin_path)
    local = read_json_file(STATE_DIR / "miner-status.json")
    # strip nothing sensitive from local status — it has no secrets by design
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(ROOT),
        "state_dir": str(STATE_DIR),
        "provider_bin": bin_path,
        "status": status,
        "status_error": status_err,
        "local_status": local,
        "pm2": pm2_apps(),
        "operators": operators_summary(),
        "secrets": secrets_presence(),
        "wallet": wallet_challenge_view(),
        "how": how_it_works(),
        "project": project_map(),
        "commands": [
            {"cmd": "./run.sh start", "why": "PM2 start single-operator provide"},
            {"cmd": "./run.sh all", "why": "PM2 start --all-operators --auto-register"},
            {"cmd": "./run.sh status", "why": "Is the miner working?"},
            {"cmd": "./run.sh doctor", "why": "Deeper health checks"},
            {"cmd": "./run.sh logs", "why": "Follow PM2 miner logs"},
            {"cmd": "./run.sh restart", "why": "Restart the PM2 miner app"},
            {"cmd": "./run.sh stop", "why": "Stop / delete PM2 miner apps"},
            {"cmd": "./run.sh dashboard", "why": "Open this dashboard"},
        ],
        "diagrams": sorted(p.name for p in DIAGRAMS.glob("*.svg")) if DIAGRAMS.is_dir() else [],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if path == "/api/overview":
            return self._json(overview_payload())
        if path == "/api/doctor":
            return self._json({"text": provider_doctor_text(find_provider())})
        if path in ("/app.js", "/styles.css"):
            target = STATIC / path.lstrip("/")
            kind = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            return self._file(target, kind)
        if path.startswith("/diagrams/"):
            name = path[len("/diagrams/") :]
            if "/" in name or name.startswith(".") or not name.endswith(".svg"):
                self.send_error(404)
                return
            target = DIAGRAMS / name
            if not target.is_file():
                self.send_error(404)
                return
            return self._file(target, "image/svg+xml")
        return super().do_GET()

    def _json(self, obj):
        body = json.dumps(obj, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str):
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SN25 miner / project dashboard")
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "8787")))
    args = parser.parse_args(argv)

    if not STATIC.is_dir():
        print(f"dashboard static dir missing: {STATIC}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"SN25 dashboard → {url}", flush=True)
    print("API: /api/overview  /api/doctor  /diagrams/<svg>", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
