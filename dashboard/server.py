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
        if name not in wanted and "sn25" not in name:
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
