const DIAGRAM_TITLES = {
  "01-roles.svg": "Who does what: customers, operators, providers, validators, Yuma",
  "02-channels.svg": "Three money channels: deposits, emission, settlement",
  "03-deposits.svg": "Deposits → reserve sink; conviction sets rate tier",
  "04-emission.svg": "Emission split and θ between head fleets and pool UIDs",
  "05-scoring.svg": "How validators score providers",
  "06-settlement.svg": "Epoch close → commit → challenge window → claims",
  "07-tiers.svg": "Pool tail vs head fleet tiers",
  "08-custody.svg": "Custody and claim flow",
  "09-participate.svg": "Three ways to take part",
  "10-one-mechanism.svg": "One incentive mechanism (mechanism_count = 1)",
};

function fmtDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "—";
  let s = Math.max(0, Math.floor(Number(seconds)));
  const d = Math.floor(s / 86400); s %= 86400;
  const h = Math.floor(s / 3600); s %= 3600;
  const m = Math.floor(s / 60); s %= 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function shortId(id) {
  if (!id) return "—";
  if (id.length <= 12) return id;
  return `${id.slice(0, 4)}…${id.slice(-4)}`;
}

function badge(text, kind) {
  return `<span class="badge ${kind || "neutral"}">${escapeHtml(text)}</span>`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function row(k, v) {
  return `<div class="row"><span class="k">${escapeHtml(k)}</span><span>${v}</span></div>`;
}

function stateKind(state) {
  if (state === "running") return "ok";
  if (state === "connecting" || state === "needs_setup") return "warn";
  if (state === "stopped") return "bad";
  return "unknown";
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

function renderHealth(data) {
  const st = data.status || {};
  const state = st.state || (data.status_error ? "error" : "unknown");
  const kind = stateKind(state);
  document.getElementById("pulse").className = `pulse ${kind}`;
  document.getElementById("state-label").textContent = state.replaceAll("_", " ");
  document.getElementById("state-sub").textContent = data.status_error
    ? data.status_error
    : st.network
      ? `network “${st.network}” · ${st.version || "unknown version"}`
      : st.version || "waiting for status";

  const stats = [
    ["Connected", st.connected ? "yes" : st.running ? "no" : "—"],
    ["Uptime", fmtDuration(st.uptime_s)],
    ["PID", st.pid ?? "—"],
    ["Client", shortId(st.client_id)],
    ["Members", st.members != null ? `${st.members_connected ?? 0}/${st.members}` : "—"],
    ["Signed in", st.signed_in ? "yes" : "no"],
  ];
  document.getElementById("stat-grid").innerHTML = stats
    .map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`)
    .join("");
}

function renderFlow(how) {
  document.getElementById("flow").innerHTML = (how.steps || [])
    .map((s) => `<li><div><strong>${escapeHtml(s.who)}</strong><p>${escapeHtml(s.what)}</p></div></li>`)
    .join("");
  document.getElementById("tiers").innerHTML = (how.tiers || [])
    .map((t) => `<div class="tier"><span>${escapeHtml(t.name)}</span><div>${escapeHtml(t.detail)}</div></div>`)
    .join("");
}

function renderPm2(apps) {
  const el = document.getElementById("pm2");
  if (!apps || !apps.length) {
    el.innerHTML = row("PM2", badge("no sn25 miner app", "warn"));
    return;
  }
  el.innerHTML = apps.map((a) => {
    const st = a.status === "online" ? badge(a.status, "ok") : badge(String(a.status || "?"), "bad");
    return [
      row("Name", escapeHtml(a.name)),
      row("Status", st),
      row("PID", escapeHtml(a.pid ?? "—")),
      row("Uptime", escapeHtml(fmtDuration(a.uptime_s))),
      row("Restarts", escapeHtml(a.restarts ?? 0)),
      row("Args", `<code>${escapeHtml((a.args || []).join(" ") || "(none)")}</code>`),
    ].join("");
  }).join('<div style="height:0.5rem"></div>');
}

function renderOperators(ops) {
  const el = document.getElementById("operators");
  const domains = (ops && ops.domains) || [];
  const local = (ops && ops.local) || [];
  const parts = [];
  parts.push(row("Published list", domains.length ? escapeHtml(domains.join(", ")) : "—"));
  if (local.length) {
    for (const o of local) {
      parts.push(row(o.domain, o.has_jwt ? badge("authenticated", "ok") : badge("needs auth", "warn")));
    }
  } else {
    parts.push(row("Local dirs", "none under operators/"));
  }
  el.innerHTML = parts.join("");
}

function fmtUnix(seconds) {
  if (!seconds) return "—";
  const date = new Date(Number(seconds) * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

let countdownTimer = null;

function dash(value) {
  if (value == null || value === "") return "—";
  return escapeHtml(value);
}

function detailCard(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`;
}

function epochSpan(detail) {
  if (!detail) return "—";
  return `${detail.from_epoch ?? "—"} → ${detail.through_epoch ?? "—"}`;
}

function statementFacts(detail) {
  const d = detail || {};
  return [
    detailCard("Coldkey", `<code>${dash(d.coldkey_ss58)}</code>`),
    detailCard("Generation", dash(d.generation)),
    detailCard("Earning epochs", dash(epochSpan(d))),
    detailCard("Issued", dash(fmtUnix(d.issued_at))),
    detailCard("Expires", dash(fmtUnix(d.expires_at))),
    detailCard("Netuid", dash(d.netuid)),
    detailCard("Chain id", dash(d.chain_id)),
    detailCard("Boundary epoch", dash(d.boundary_epoch)),
    detailCard("Boundary block", dash(d.boundary_block)),
    detailCard("Boundary hash", `<code>${dash(d.boundary_hash)}</code>`),
    detailCard("Client", `<code>${dash(d.client_id)}</code>`),
    detailCard("Network", `<code>${dash(d.network_id)}</code>`),
    detailCard("User", `<code>${dash(d.user_id)}</code>`),
    detailCard("Coordinator", `<code>${dash(d.coordinator)}</code>`),
    detailCard("Settlement vault", `<code>${dash(d.settlement_vault)}</code>`),
    detailCard("Boundary signer", `<code>${dash(d.signer)}</code>`),
  ].join("");
}

function startCountdown(expiresAt, expired) {
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
  const el = document.getElementById("wallet-countdown");
  if (!el) return;
  const paint = (left) => {
    if (left <= 0) {
      el.textContent = "Expired";
      el.className = "countdown expired";
      return;
    }
    const m = Math.floor(left / 60);
    const s = left % 60;
    el.textContent = `${m}m ${String(s).padStart(2, "0")}s`;
    el.className = "countdown open";
  };
  if (!expiresAt || expired) {
    paint(0);
    return;
  }
  const tick = () => paint(Math.floor(Number(expiresAt) - Date.now() / 1000));
  tick();
  countdownTimer = setInterval(tick, 1000);
}

function renderWallet(wallet) {
  const el = document.getElementById("wallet");
  const note = document.getElementById("wallet-note");
  const accepted = (wallet && wallet.accepted) || null;
  const challenge = (wallet && wallet.challenge) || {};
  const detail = challenge.detail || {};
  const coldkey = (wallet && wallet.coldkey_ss58) || detail.coldkey_ss58 || "";
  const open = Boolean(challenge.present && !challenge.expired);
  const bannerClass = open ? "open" : challenge.present ? "expired" : "missing";
  let title = "No challenge on disk";
  let summary = "Issue a statement, sign it with the payout coldkey, and submit it before it expires.";
  if (open) {
    title = "Open challenge";
    summary = "Sign this exact statement with the coldkey and submit it before the timer ends.";
  } else if (challenge.present && accepted && accepted.acknowledged) {
    title = "Saved challenge expired";
    summary = "The payout wallet is already accepted. This statement can no longer be submitted.";
  } else if (challenge.present) {
    title = "Saved challenge expired";
    summary = "Issue a new statement. This one can no longer be submitted.";
  } else if (accepted && accepted.acknowledged) {
    title = "Payout wallet accepted";
    summary = "No open challenge. A new one is only needed to change the payout coldkey or the epoch window.";
  }

  const steps = open
    ? [
        "Copy the statement below. Keep every character, including the line break.",
        "Sign it with the payout coldkey in the substrate signing context.",
        "Submit the signature before the timer hits zero.",
      ]
    : [
        `Issue a new statement: provider wallet challenge ${coldkey || "<coldkey_ss58>"}`,
        "Sign that new statement with the payout coldkey. Do not reuse an expired one.",
        "Submit with provider wallet set within five minutes of issue.",
      ];

  const consent = accepted
    ? `${accepted.acknowledged ? "Accepted" : "Pending"} · epochs ${epochSpan(accepted.detail || accepted)} · generation ${accepted.generation ?? "—"}`
    : "No consent on file";
  const match = accepted
    ? accepted.matches_env
      ? badge("matches .env", "ok")
      : badge("differs from .env", "warn")
    : badge("unset", "neutral");

  el.innerHTML = `
    <div class="wallet-banner ${bannerClass}">
      <div>
        <p class="eyebrow">${open ? "Sign now" : "Payout wallet"}</p>
        <h2>${escapeHtml(title)}</h2>
        <p class="muted">${escapeHtml(summary)}</p>
      </div>
      <div class="countdown-wrap">
        <p class="eyebrow">Time left</p>
        <p id="wallet-countdown" class="countdown">${open ? "—" : "Expired"}</p>
      </div>
    </div>
    <div class="wallet-columns">
      <div>
        <h3 class="later first">Statement</h3>
        <dl class="detail-grid">${challenge.present ? statementFacts(detail) : statementFacts(accepted && accepted.detail)}</dl>
      </div>
      <div>
        <h3 class="later first">How to use it</h3>
        <ol class="sign-steps">${steps.map((step) => `<li><span>${escapeHtml(step)}</span></li>`).join("")}</ol>
        <div class="stack consent-stack">
          ${row("Consent", accepted && accepted.acknowledged ? badge("accepted", "ok") : accepted ? badge("pending", "warn") : badge("none", "neutral"))}
          ${row("On file", escapeHtml(consent))}
          ${row("Coldkey check", match)}
          ${row(".env coldkey", `<code>${dash(coldkey)}</code>`)}
        </div>
      </div>
    </div>
    <div class="message-block">
      <div class="message-bar">
        <span class="k">Exact statement</span>
        <button type="button" class="btn" data-copy ${challenge.present ? "" : "disabled"}>Copy statement</button>
      </div>
      <pre id="wallet-message" class="doctor wallet-message">${challenge.present ? escapeHtml(challenge.message || "") : "No statement is saved. Issue a new wallet challenge to print one."}</pre>
    </div>`;

  if (open) note.textContent = "Sign this exact message with the coldkey and submit it within five minutes of issue.";
  else if (accepted && accepted.acknowledged && challenge.expired) note.textContent = "This coldkey is already confirmed. The saved challenge has expired, so it cannot be submitted again.";
  else if (!challenge.present) note.textContent = "No wallet challenge file is on disk. Run provider wallet challenge to print a new one.";
  else note.textContent = "The payout-wallet statement to sign, and the consent already on file.";

  startCountdown(challenge.expires_at, !open);
}

document.getElementById("wallet").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button || button.disabled) return;
  const text = document.getElementById("wallet-message")?.textContent || "";
  const original = button.textContent;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.left = "-9999px";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      if (!ok) throw new Error("copy failed");
    }
    button.textContent = "Copied";
  } catch {
    button.textContent = "Select the statement";
  }
  setTimeout(() => {
    button.textContent = original;
  }, 1600);
});

function renderSecrets(sec) {
  const el = document.getElementById("secrets");
  const bit = (ok) => (ok ? badge("present", "ok") : badge("missing", "neutral"));
  el.innerHTML = [
    row("Hotkey seed", bit(sec.hotkey_seed)),
    row("Coldkey seed", bit(sec.coldkey_seed)),
    row("Wallet challenge", bit(sec.wallet_challenge)),
    row("Wallet signature", bit(sec.wallet_signature)),
  ].join("");
}

function renderRepo(project) {
  document.getElementById("repo-map").innerHTML = (project || [])
    .map((p) => `<li><code>${escapeHtml(p.path)}</code><span>${escapeHtml(p.desc)}</span></li>`)
    .join("");
}

function renderCmds(cmds) {
  document.getElementById("cmds").innerHTML = (cmds || [])
    .map((c) => `<li><code>${escapeHtml(c.cmd)}</code><span class="why">${escapeHtml(c.why)}</span></li>`)
    .join("");
}

function renderDiagrams(names) {
  const tabs = document.getElementById("diagram-tabs");
  if (!names || !names.length) {
    tabs.innerHTML = "";
    document.getElementById("diagram-img").removeAttribute("src");
    document.getElementById("diagram-cap").textContent = "No diagrams found";
    return;
  }
  tabs.innerHTML = names
    .map((n, i) => `<button type="button" data-name="${escapeHtml(n)}" class="${i === 0 ? "active" : ""}">${escapeHtml(n.replace(".svg", ""))}</button>`)
    .join("");
  const show = (name) => {
    document.getElementById("diagram-img").src = `/diagrams/${encodeURIComponent(name)}`;
    document.getElementById("diagram-cap").textContent = DIAGRAM_TITLES[name] || name;
    for (const b of tabs.querySelectorAll("button")) {
      b.classList.toggle("active", b.dataset.name === name);
    }
  };
  tabs.onclick = (e) => {
    const btn = e.target.closest("button[data-name]");
    if (btn) show(btn.dataset.name);
  };
  show(names[0]);
}

async function loadDoctor() {
  try {
    const data = await fetchJson("/api/doctor");
    document.getElementById("doctor").textContent = data.text || "(empty)";
  } catch (err) {
    document.getElementById("doctor").textContent = String(err);
  }
}

async function refresh() {
  const clock = document.getElementById("clock");
  try {
    const data = await fetchJson("/api/overview");
    clock.textContent = new Date(data.generated_at).toLocaleString();
    renderHealth(data);
    renderFlow(data.how || {});
    renderPm2(data.pm2 || []);
    renderOperators(data.operators || {});
    renderSecrets(data.secrets || {});
    renderWallet(data.wallet || {});
    renderRepo(data.project || []);
    renderCmds(data.commands || []);
    renderDiagrams(data.diagrams || []);
  } catch (err) {
    clock.textContent = "error";
    document.getElementById("state-label").textContent = "dashboard error";
    document.getElementById("state-sub").textContent = String(err);
    document.getElementById("pulse").className = "pulse bad";
  }
}

document.getElementById("refresh").addEventListener("click", () => {
  refresh();
  loadDoctor();
});

refresh();
loadDoctor();
setInterval(refresh, 15000);
