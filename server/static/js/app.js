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
  const result = await signAndPostEvent(projectId, "demo", "demo-entity", "demo_event",
    { description: text, at: new Date().toISOString() });
  if (!result.ok) { log("Send event failed: " + result.body.detail); return; }
  log("Event accepted by server: " + result.body.id);
});

// Builds, signs, hashes, and posts one event — the same sequence any
// registry/job mutation goes through. Real client/sync.py will keep
// prev_hash locally instead of asking the server each time; this demo
// asks each time to stay simple and stateless across page reloads.
async function signAndPostEvent(projectId, entityType, entityId, op, payloadObj) {
  const fp = await ed25519.fingerprint(identity.publicKey);
  const event = {
    id: crypto.randomUUID(),
    project_id: projectId,
    device_fingerprint: fp,
    entity_type: entityType,
    entity_id: entityId,
    op,
    payload_json: JSON.stringify(payloadObj),
    client_time: new Date().toISOString(),
    signer_fingerprint: fp,
  };
  const sig = await ed25519.sign(identity.privateKey, canonicalEventBytes(event));
  event.signature = ed25519.bytesToHex(sig);

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
  return { ok: resp.ok, body };
}

$("add-drawing").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  const name = $("drawing-name").value.trim();
  if (!identity || !sessionToken || !projectId || !name) {
    log("Need identity, login, a project id, and a drawing name first.");
    return;
  }
  const payload = {
    name,
    title: $("drawing-title").value.trim(),
    rev: $("drawing-rev").value.trim(),
    url: $("drawing-url").value.trim(),
    notes: "",
  };
  const result = await signAndPostEvent(projectId, "drawing", name, "registry_add", payload);
  if (!result.ok) { log("Add drawing failed: " + JSON.stringify(result.body.detail)); return; }
  log("Drawing registry event accepted: " + name);
});

$("list-drawings").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  if (!sessionToken || !projectId) { log("Need to be logged in with a project id first."); return; }
  const resp = await apiFetch(`/projects/${projectId}/drawings`);
  const rows = await resp.json();
  if (!resp.ok) { log("List drawings failed: " + JSON.stringify(rows.detail)); return; }
  const el = $("drawings-list");
  el.innerHTML = "";
  for (const row of rows) {
    const li = document.createElement("li");
    li.textContent = `${row.name} — ${row.title} (rev ${row.rev}) ${row.url}`;
    el.appendChild(li);
  }
  log(`Loaded ${rows.length} drawing(s).`);
});

$("create-job").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  if (!identity || !sessionToken || !projectId) {
    log("Need identity, login, and a project id first.");
    return;
  }
  const jobId = crypto.randomUUID();
  const payload = {
    type: $("job-type").value.trim() || "REMOVE",
    description: $("job-description").value.trim(),
    wire: "", start: {}, end: {}, notes: "",
  };
  const result = await signAndPostEvent(projectId, "job", jobId, "job_created", payload);
  if (!result.ok) { log("Create job failed: " + JSON.stringify(result.body.detail)); return; }
  $("job-id").value = jobId;
  log("Job created: " + jobId);
});

$("add-step").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  const jobId = $("job-id").value.trim();
  const description = $("step-description").value.trim();
  if (!identity || !sessionToken || !projectId || !jobId || !description) {
    log("Need identity, login, a project id, a job id, and a step description.");
    return;
  }
  const stepId = crypto.randomUUID();
  const result = await signAndPostEvent(projectId, "job_step", stepId, "step_added",
    { job_id: jobId, description });
  if (!result.ok) { log("Add step failed: " + JSON.stringify(result.body.detail)); return; }
  $("step-id").value = stepId;
  log("Step added: " + stepId);
});

$("assign-step").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  const stepId = $("step-id").value.trim();
  const assignTo = $("assign-user-id").value.trim();
  if (!identity || !sessionToken || !projectId || !stepId || !assignTo) {
    log("Need identity, login, a project id, a step id, and a user id to assign to.");
    return;
  }
  const result = await signAndPostEvent(projectId, "job_step", stepId, "step_assigned",
    { assigned_to: assignTo });
  if (!result.ok) { log("Assign step failed: " + JSON.stringify(result.body.detail)); return; }
  log("Step assigned: " + stepId + " -> " + assignTo);
});

$("complete-step").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  const stepId = $("step-id").value.trim();
  if (!identity || !sessionToken || !projectId || !stepId) {
    log("Need identity, login, a project id, and a step id.");
    return;
  }
  const result = await signAndPostEvent(projectId, "job_step", stepId, "step_completed", {});
  if (!result.ok) { log("Complete step failed: " + JSON.stringify(result.body.detail)); return; }
  log("Step completed: " + stepId);
});

$("list-steps").addEventListener("click", async () => {
  const projectId = $("project-id").value.trim();
  const jobId = $("job-id").value.trim();
  if (!sessionToken || !projectId || !jobId) { log("Need login, a project id, and a job id."); return; }
  const resp = await apiFetch(`/projects/${projectId}/jobs/${jobId}/steps`);
  const rows = await resp.json();
  if (!resp.ok) { log("List steps failed: " + JSON.stringify(rows.detail)); return; }
  const el = $("steps-list");
  el.innerHTML = "";
  for (const row of rows) {
    const li = document.createElement("li");
    li.textContent = `[${row.status}] ${row.description} — added by ${row.added_by}` +
      (row.assigned_to ? `, assigned to ${row.assigned_to}` : "") +
      (row.completed_by ? `, completed by ${row.completed_by} at ${row.completed_at}` : "");
    el.appendChild(li);
  }
  log(`Loaded ${rows.length} step(s) for job ${jobId}.`);
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
