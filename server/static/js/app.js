// Phase 1 demo page: proves the enrollment -> challenge/response login ->
// signed event -> real-time broadcast loop end-to-end in a browser. This
// is deliberately minimal (no framework, no build step) — Phase 2+ builds
// the real UI on top of the same auth/sync primitives established here.

const $ = (id) => document.getElementById(id);
const log = (msg) => {
  const el = $("log");
  el.textContent += msg + "\n";
  el.scrollTop = el.scrollHeight;
};

const STORAGE_KEY = "redline_identity_v1";

function loadIdentity() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  const obj = JSON.parse(raw);
  return {
    privateKey: ed25519.hexToBytes(obj.privateKeyHex),
    publicKey: ed25519.hexToBytes(obj.publicKeyHex),
  };
}

function saveIdentity(privateKey, publicKey) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    privateKeyHex: ed25519.bytesToHex(privateKey),
    publicKeyHex: ed25519.bytesToHex(publicKey),
  }));
}

let identity = loadIdentity();
let sessionToken = null;
let ws = null;

async function refreshIdentityUI() {
  if (!identity) {
    $("identity").textContent = "No local identity yet.";
    return;
  }
  const fp = await ed25519.fingerprint(identity.publicKey);
  $("identity").textContent =
    `public key: ${ed25519.bytesToHex(identity.publicKey)}\nfingerprint: ${fp}`;
}

$("gen-keypair").addEventListener("click", async () => {
  const kp = await ed25519.generateKeypair();
  identity = { privateKey: kp.privateKey, publicKey: kp.publicKey };
  saveIdentity(identity.privateKey, identity.publicKey);
  await refreshIdentityUI();
  log("Generated a new local Ed25519 keypair (private key never leaves this browser).");
});

$("enroll").addEventListener("click", async () => {
  if (!identity) { log("Generate a keypair first."); return; }
  const code = $("enroll-code").value.trim();
  if (!code) { log("Paste an enrollment code first."); return; }
  const resp = await fetch("/auth/enroll", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code,
      public_key_hex: ed25519.bytesToHex(identity.publicKey),
      label: navigator.userAgent.slice(0, 40),
    }),
  });
  const body = await resp.json();
  if (!resp.ok) { log("Enroll failed: " + body.detail); return; }
  log(`Enrolled. user_id=${body.user_id} fingerprint=${body.fingerprint}`);
});

$("login").addEventListener("click", async () => {
  if (!identity) { log("Generate a keypair first."); return; }
  const chal = await (await fetch("/auth/challenge")).json();
  const sig = await ed25519.sign(identity.privateKey, new TextEncoder().encode(chal.nonce));
  const fp = await ed25519.fingerprint(identity.publicKey);
  const resp = await fetch("/auth/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nonce: chal.nonce,
      fingerprint: fp,
      signature_hex: ed25519.bytesToHex(sig),
    }),
  });
  const body = await resp.json();
  if (!resp.ok) { log("Login failed: " + body.detail); return; }
  sessionToken = body.token;
  log(`Logged in. session token acquired, user_id=${body.user_id}`);
});

async function apiFetch(path, opts = {}) {
  opts.headers = Object.assign({}, opts.headers, {
    Authorization: sessionToken ? `Bearer ${sessionToken}` : "",
  });
  return fetch(path, opts);
}

$("create-project").addEventListener("click", async () => {
  const name = $("project-name").value.trim() || "Demo Project";
  const resp = await apiFetch("/admin/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const body = await resp.json();
  if (!resp.ok) { log("Create project failed: " + body.detail); return; }
  $("project-id").value = body.project_id;
  log("Created project " + body.project_id);
});

$("connect-ws").addEventListener("click", () => {
  const projectId = $("project-id").value.trim();
  if (!projectId || !sessionToken) { log("Need a project id and to be logged in first."); return; }
  if (ws) ws.close();
  ws = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events/${projectId}?token=${sessionToken}`
  );
  ws.onopen = () => log("WebSocket connected — this tab will receive live events for this project.");
  ws.onclose = () => log("WebSocket closed.");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    log("LIVE EVENT RECEIVED: " + JSON.stringify(msg.event.payload_json));
  };
});

$("send-event").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  const text = $("event-text").value.trim() || "demo step completed";
  if (!identity || !sessionToken || !projectId) {
    log("Need identity, login, and a project id first.");
    return;
  }
  const fp = await ed25519.fingerprint(identity.publicKey);
  const payload = { description: text, at: new Date().toISOString() };
  const event = {
    id: crypto.randomUUID(),
    project_id: projectId,
    device_fingerprint: fp,
    entity_type: "demo",
    entity_id: "demo-entity",
    op: "demo_event",
    payload_json: JSON.stringify(payload),
    client_time: new Date().toISOString(),
    signer_fingerprint: fp,
  };
  const sig = await ed25519.sign(identity.privateKey, canonicalEventBytes(event));
  event.signature = ed25519.bytesToHex(sig);
  // prev_hash: for this demo, ask the server for its current event list to
  // find the last hash for our device+project. Real client/sync.py keeps
  // this locally instead of asking each time.
  const existing = await (await apiFetch(`/projects/${projectId}/events`)).json();
  const mine = existing.filter((e) => e.device_fingerprint === fp);
  const prevHash = mine.length ? mine[mine.length - 1].hash : "";
  event.prev_hash = prevHash;
  event.hash = await eventHash(event, prevHash);

  const resp = await apiFetch(`/projects/${projectId}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  });
  const body = await resp.json();
  if (!resp.ok) { log("Send event failed: " + body.detail); return; }
  log("Event accepted by server: " + body.id);
});

// Mirrors shared/crypto.py canonical_event_bytes / event_hash exactly —
// same excluded-field list, same JSON separators, same key order (JSON
// stringify of a JS object with insertion order matching Python's sorted
// order requires us to sort keys manually here).
function canonicalEventBytes(event) {
  const excluded = new Set(["signature", "hash", "prev_hash", "server_time", "needs_review"]);
  const keys = Object.keys(event).filter((k) => !excluded.has(k)).sort();
  const obj = {};
  for (const k of keys) obj[k] = event[k];
  return new TextEncoder().encode(JSON.stringify(obj));
}

async function eventHash(event, prevHash) {
  const bytes = canonicalEventBytes(event);
  const prevBytes = new TextEncoder().encode(prevHash || "");
  const combined = new Uint8Array(prevBytes.length + bytes.length);
  combined.set(prevBytes, 0);
  combined.set(bytes, prevBytes.length);
  return ed25519.sha256Hex(combined);
}

refreshIdentityUI();
