# Remote Collaboration & Rebuild — Design

**Status:** Draft / RFC — for discussion, not yet approved
**Audience:** maintainers of Red-Line-Routing
**Author:** drafted from the "Future Plan's Ideas" brain-dump

This document turns the future-plans brain-dump into a concrete, phased
engineering plan. It is deliberately a *design*, not an implementation — the
goal is to agree the shape of the rebuild before any code moves. Open
decisions that need a human call are collected in [§10](#10-open-decisions).

---

## 1. The vision (restated)

Red-Line-Routing today is a single-user, offline desktop planner. The plan is
to grow it into a **multi-user, multi-device collaboration tool** built around
an **optional central server**:

- A team can work **one plan together** from several devices at once.
- A crew at a **low-bandwidth site** interacts with the plan and files over a
  thin connection, while a **high-bandwidth server** does the heavy lifting:
  pulling documents from corporate sources and pushing them to reporting sites.
- Users **log in with SSO** and **sign their changes** with that identity, so
  authorship of every change is attributable and non-repudiable.
- The local copy and the central copy stay in **bidirectional sync**.
- Small jobs must still work with **no server at all** — fully local.

### What must stay consistent across copies

| Thing | Sync behaviour the ideas call for |
|---|---|
| The `.redline` plan file | Shared; **Git-like** version control — see changes, **approve** changes |
| Important notes | Same Git-like, review-and-approve flow |
| Checking off a line item | **Immediate** — propagates the moment someone ticks it, no approval |
| File downloads (drawings, PDFs) | Stay **user's choice** — never force a low-bandwidth client to pull |
| File integrity | A **checksum** so a user can confirm their local copy matches central |

> The crucial insight: this is **not** one uniform sync policy. Substantive
> edits are **review-gated** (like a pull request); operational flags are
> **instant** (like a chat reaction). The architecture has to support both over
> the *same* document. Everything below is built around that split.

### Explicit scope direction from the brain-dump

- **Drop the "no pip packages" rule.** The server is a **container** and may use
  whatever dependencies it needs. (Client dependency policy is an open
  decision — see §10.)
- **Local-only mode is a first-class feature**, not a fallback: "this work does
  not need a central server and can be done local for small jobs."
- **Cut scope.** "Ditch some functions"; the setup **wizard comes later**.

---

## 2. Where we are today (and why it matters)

A rebuild has to start from an honest read of the current code. The relevant
constraints, with file references:

1. **Whole-file save/load.** `RedLineApp._write()` (`wire_planner.py:11969`)
   serialises the *entire* project to one JSON blob; `_open()`
   (`wire_planner.py:11896`) reads it back. There is no notion of a change, a
   version, or a diff — the unit of persistence is "the whole plan."
   *Implication:* field-level sync and Git-like history do not exist yet and
   can't be bolted on without a model layer.

2. **State is welded to the GUI.** Notes live in a `tk.Text` widget
   (`self.title_notes`) and are only pulled into the model at save time
   (`_write()` does `tp["notes"] = self.title_notes.get(...)`). Many other
   values live in Tk variables. *Implication:* there is no headless,
   serialisable "project model" a server or sync engine could operate on. This
   is the single biggest blocker and the first thing to fix.

3. **The instant-check-off primitive already exists.** Job completion is just
   `job["completed"]` (a bool on each job dict, see `empty_job()`
   `wire_planner.py:267`). This maps directly onto the "instant op" class — it's
   the easiest thing to make real-time.

4. **Global vs per-project split is already established.** Cross-project state
   (settings, standards library, drawing cache) is in SQLite via `_AppDB`
   (`wire_planner.py:5060`), opened per-call so it's thread-safe. The per-project
   `.redline` is separate. The rebuild keeps this split and adds a third tier:
   *shared project state on the server.*

5. **Downloads already abstract transport.** `_download_with_progress()`
   (`wire_planner.py:8095`) handles HTTP(S), cookie auth, and `file://`
   (incl. UNC) via `_file_url_to_path()` (`wire_planner.py:234`). A server-side
   relay slots in as a new transport behind the same call site.

6. **12k-line monolith, stdlib-only.** `wire_planner.py` is ~12,200 lines of
   mixed model + widgets + networking + PDF generation. The rebuild is the
   moment to split this into packages.

---

## 3. Design principles

1. **Local-first.** The app is fully usable with no network. The server is an
   accelerator and a meeting point, never a hard dependency. A crew with a dead
   link keeps working; sync catches up later.
2. **Sync changes, not files.** The plan is small and edited constantly; bulk
   documents are large and pulled rarely. Treat them with opposite strategies —
   stream tiny operations for the plan, lazy-pull big files on demand.
3. **Every change is attributable.** No anonymous mutation of shared state.
   Identity comes from SSO; integrity from a signature.
4. **Two change classes, one document.** `instant` (apply + broadcast now) and
   `review` (propose → approve → merge). The class is a property of the *field*,
   defined by policy, not chosen ad hoc by the user.
5. **Bandwidth is a feature.** Every interaction is designed around a thin pipe:
   deltas over snapshots, hashes over downloads, compression on, resumable
   transfers, pull-on-demand for documents.
6. **Verifiable copies.** Any client can cheaply prove its local plan equals
   central by comparing a content hash — without downloading the plan.

---

## 4. Target architecture

```
                 ┌─────────────────────────────────────────────┐
                 │  CENTRAL SERVER  (container, high bandwidth) │
                 │                                              │
   SSO / OIDC ◄──┤  • Auth & identity (SSO, issues tokens)      │
                 │  • Canonical plan state + operation log      │
                 │  • Approval queue (review-gated changes)     │
                 │  • Content-addressed file store + relay      ├──► Corporate
   Reporting ◄───┤  • Push channel (WebSocket / SSE)            │    drawing /
   sites         │                                              │    relay sources
                 └───────────────▲───────────────▲──────────────┘
                                 │ ops + hashes  │ lazy file pulls
                 thin pipe ──────┼───────────────┼──────── thin pipe
            ┌────────────────────┴───┐     ┌─────┴──────────────────┐
            │  CLIENT A (low bw site)│     │  CLIENT B (office)     │
            │  • local plan + op log │     │  • local plan + op log │
            │  • pending local ops   │     │  • approvals UI        │
            │  • Tk GUI (or future)  │     │                        │
            └────────────────────────┘     └────────────────────────┘

   No-server mode: a CLIENT runs entirely on its own local plan + op log.
```

### 4.1 Topology & deployment

- **Server** ships as a container image (Docker/OCI). One command to run; a
  compose file wires it to its database and (optional) object store. It lives at
  a high-bandwidth site.
- **Client** is the existing desktop app, refactored. It can run in three modes:
  - **Local-only** — no account, no server, today's behaviour. For small jobs.
  - **Connected** — signed in, syncing one or more shared plans.
  - **Offline-connected** — was connected, link dropped; queues ops locally and
    reconciles on reconnect.
- A plan is **promotable**: start local, later "publish to server" to collaborate.

### 4.2 Identity & signing (SSO + signed changes)

- **Authentication:** SSO via **OIDC** (recommended; SAML as an alternative if
  the org standard requires it). The client runs the auth-code flow in the
  browser, receives a short-lived access token + refresh token, and presents the
  token to the server.
- **Signing changes:** every operation carries `author` and a **signature** so
  authorship is non-repudiable and tamper-evident.
  - *Recommended:* each user holds a keypair; the public key is bound to their
    SSO identity at enrolment (ideally a cert from the org PKI — this is the
    "certificate" in the brain-dump). The client signs each op's canonical bytes;
    the server verifies against the enrolled key and records it in the log.
  - *Simpler fallback:* the server signs/stamps each accepted op on behalf of the
    authenticated session (attestation, not client-side non-repudiation). Less
    strong, much less moving plumbing. See §10.
- The op log is therefore an **auditable, signed history** — the substrate for
  the "Git-like" experience.

### 4.3 The sync model (the core)

Represent a plan as **canonical state + an append-only operation log**, instead
of an opaque JSON blob.

- **Operation (`op`):** the unit of change.

  ```jsonc
  {
    "id":        "uuid",
    "plan":      "plan-id",
    "base":      "content-hash the op was authored against",
    "author":    "sso-subject-id",
    "ts":        "2026-06-24T12:00:00Z",
    "class":     "instant" | "review",
    "path":      "jobs[3].completed",      // JSON-pointer-ish target
    "op":        "set" | "insert" | "delete" | "move",
    "value":     true,
    "sig":       "base64 signature over the canonical op bytes"
  }
  ```

- **Field policy decides the class.** A policy table maps document paths to
  `instant` or `review`:

  | Path / field | Class | Why |
  |---|---|---|
  | `jobs[*].completed` | instant | The check-off the ideas single out |
  | tailboard / safety sign-on toggles | instant | Operational state, time-critical |
  | `jobs[*]` structure, endpoints, wire | review | Substantive plan content |
  | `title_page.notes`, job notes | review | "Important notes" — explicitly review-gated |
  | registries (drawings/relays/standards) | review | Shared reference data |

- **Instant ops** apply locally at once, ship immediately, and the server
  broadcasts them to other clients on the push channel. Conflicts use a simple,
  defensible rule (last-writer-wins by `ts`; or per-user completion if we want to
  record *who* ticked what).

- **Review ops** don't touch canonical state on arrival. They collect into a
  **proposed changeset** (a "PR"): an approver sees a human-readable diff and
  **approves/merges** or rejects. On merge, the ops fold into canonical state and
  a new content hash is minted. This is the Git-like flow the ideas ask for —
  *see changes, approve changes* — applied to the plan and notes.

- **Content hashing / checksums.** Canonical state hashes to a SHA-256 over a
  **canonical JSON serialisation** (sorted keys, fixed separators, normalised
  numbers). Every committed version is identified by its hash (content-addressed,
  like a Git commit). A client confirms it's in sync by fetching *just the hash*
  (a few bytes) and comparing — no plan download. This is the "checksum so users
  can confirm their local copy is the same."

- **Why an op log instead of file-diffing?** Diffing whole JSON files is
  bandwidth-heavy and can't express field-level policy or per-field authorship.
  An op log is tiny on the wire, gives free history/blame/revert, makes
  instant-vs-review a per-op property, and degrades gracefully offline (queue
  ops, replay on reconnect).

### 4.4 Files & bandwidth

- **Documents stay pull-on-demand.** The plan syncs; the 40 MB of PDFs behind it
  do **not** stream to a low-bandwidth client unasked. The client sees that a
  document *exists* (name + hash + size in the plan metadata) and pulls it only
  when the user chooses — preserving today's "download is user choice" behaviour
  in `_download_with_progress()`.
- **Content-addressed file store.** Files are stored on the server keyed by their
  hash. Benefits: automatic dedupe (a drawing referenced by many plans is stored
  once), integrity verification on download, and **resumable / range** transfers
  for flaky links.
- **The server as a relay.** This realises "server at a high-bandwidth site
  uploads documents to reporting sites." A thin client asks the server to fetch a
  drawing from the corporate source and/or push a finished package to a reporting
  site; the bulk transfer happens server-side over the fat pipe, and the client
  only exchanges a request + a hash. The existing cookie-auth download machinery
  moves server-side.
- **On the wire:** gzip/deflate everything, send deltas not snapshots, and make
  every bulk transfer resumable.

### 4.5 Conflict resolution & history (Git-like)

- **History for free.** The op log *is* the history: who changed what, when, with
  a verifiable signature. "See changes" is a log view; "approve changes" is the
  changeset merge; revert is replaying the inverse op.
- **Conflicts:**
  - *Instant fields* — last-writer-wins (or union for set-like state). Cheap,
    predictable, good enough for check-offs.
  - *Review fields* — surfaced at merge time. If two changesets touch the same
    path off a shared base, the approver resolves it during review (exactly the
    PR-merge mental model).
- Because ops carry the `base` hash they were authored against, the server can
  detect when an op was written against stale state and route it through
  conflict handling instead of applying blindly.

### 4.6 Transport & protocol

- **Control plane:** HTTPS/JSON REST — auth, push ops, fetch hash, fetch
  snapshot, list/approve changesets, request a file relay.
- **Push plane:** a server→client channel so instant ops and "new changeset to
  review" notifications arrive live. **WebSocket** preferred; **SSE or long-poll**
  as the low-bandwidth/firewall-friendly fallback (both are easy on a thin pipe
  and through corporate proxies).
- **Offline:** all client writes go to a local op queue first, then flush on
  reconnect. The UI never blocks on the network.

### 4.7 Server packaging & dependency policy

- **Server is a container** with a real stack — framework + database + push. A
  reasonable, boring choice: **FastAPI (or Flask) + SQLite/Postgres + Redis** (or
  in-process pub/sub) for fan-out, behind the org's TLS. The no-pip rule **does
  not** apply to the server.
- **Client dependency policy is an open decision (§10).** Now that no-pip is
  being dropped we *can* give the client a few vetted deps (`requests`,
  `websocket-client`, `cryptography`). But local-only small-job users shouldn't
  be forced to install anything — so the recommendation is: **client uses deps
  only for connected features, with a stdlib path that keeps local-only mode
  install-free.**

---

## 5. Data-model changes

The rebuild's enabling refactor is **a headless project model**, decoupled from
Tk.

- Introduce a `core/` package: a plain-Python `Project` model (jobs, registries,
  title page, tailboard refs, history) that serialises to/from the existing
  `.redline` JSON — **byte-compatible** with today's files so nothing breaks.
- The GUI binds *to the model*, not the other way round. Notes, completion, etc.
  live in the model; widgets observe and edit it. (Today `notes` only exists in a
  Tk widget until save — that ends.)
- Add version metadata to `.redline` (additively, so old files still load):

  ```jsonc
  {
    "schema":    2,
    "plan_id":   "uuid",                 // stable identity across copies
    "version":   "content-hash",          // current canonical hash
    "server":    "https://… | null",      // null = local-only
    // … all existing keys unchanged …
  }
  ```

- The op-log and pending changesets live **beside** the `.redline` (e.g. a
  `.redline.log` or a small SQLite sidecar), not inside it — keeping the plan
  file clean and human-readable, as it is today.

---

## 6. Scope: what to cut or defer

Per "ditch some functions" and "wizard should come later":

- **Defer the 5-step Project Wizard** (`ProjectWizard`, the wizard branch of the
  startup flow). Replace with a minimal "new plan / open / connect to server"
  landing during the rebuild; reintroduce a wizard later if wanted.
- **Audit for removal during the model split** (candidates — confirm in §10):
  legacy JSON migration paths once the new schema lands; rarely used export
  permutations; any feature that doesn't survive the model/GUI decoupling cheaply.
- **One transport for files.** Collapse the bespoke per-tab download paths onto
  the single relay-aware downloader.

The aim is a smaller, modular core (`core/`, `sync/`, `server/`, `ui/`) rather
than growing the 12k-line monolith.

---

## 7. Phased roadmap

Each phase is independently shippable and de-risks the next.

| Phase | Deliverable | Server needed? |
|---|---|---|
| **0. Decouple model from GUI** | `core/` `Project` model; GUI binds to it; `.redline` round-trips byte-for-byte | No |
| **1. Versioning & checksums** | Canonical hashing; `schema`/`plan_id`/`version` fields; local op log; local history/undo view | No |
| **2. Optional server + SSO** | Container server; OIDC login; publish/clone a plan; push/pull **whole** plan with hash verification | Yes |
| **3. Field-level sync** | Op streaming; **instant** check-offs live; **review-gated** changesets for plan/notes; push channel | Yes |
| **4. File relay** | Content-addressed store; pull-on-demand; server fetches from corporate sources & pushes to reporting sites | Yes |
| **5. Signing & approvals UI** | Per-user signed ops; Git-like diff/approve/reject UX; audit/blame view | Yes |

Phases 0–1 deliver real value (history, integrity, undo) to **today's
single-user app with no server** — a safe, useful beachhead before any
distributed-systems work.

---

## 8. Security & trust notes

- All transport over TLS; tokens short-lived; refresh handled silently.
- Signature verification on the server is the trust boundary for "who changed
  this." Keep signing keys in the OS keystore where possible.
- The server becomes custody of corporate-auth cookies/headers for relaying —
  treat that store as sensitive (per-user, encrypted at rest, scoped).
- Treat any content fetched from corporate/reporting sites as untrusted input.

---

## 9. Risks

- **Refactor risk (Phase 0).** Decoupling a 12k-line GUI from its state is
  invasive. Mitigate with the byte-compatible `.redline` round-trip as a golden
  test before any sync work.
- **Distributed complexity.** Op logs, conflict handling, and approvals are real
  engineering. The phasing keeps each step small and the app usable throughout.
- **Enterprise integration unknowns.** SSO provider, PKI, proxy/firewall posture
  at low-bandwidth sites — discover these early (Phase 2 spike).
- **Two-class sync UX.** Users must understand why a check-off is instant but an
  edit needs approval. Lean on familiar metaphors (chat reaction vs pull request).

---

## 10. Open decisions

These genuinely need a human call. Recommendations are first.

1. **Client dependency policy.** *Recommend:* allow vetted deps for connected
   features, keep a stdlib path so local-only stays install-free. (Alt: keep the
   client pure-stdlib even when connected.)
2. **Signing strength.** *Recommend:* per-user keys bound to SSO/PKI (true
   non-repudiation). (Alt: server-side attestation only — far less plumbing.)
3. **SSO protocol.** *Recommend:* OIDC. (Alt: SAML if mandated by the org.)
4. **Server stack.** *Recommend:* FastAPI + Postgres + Redis in a container.
   (Alt: Flask + SQLite for a smaller footprint.)
5. **Push transport for thin pipes.** *Recommend:* WebSocket with SSE/long-poll
   fallback. (Alt: long-poll only — simplest through hostile proxies.)
6. **Exactly which functions to ditch now**, and whether the wizard is removed or
   just hidden during the rebuild.
7. **Conflict policy for instant fields** — last-writer-wins vs per-user
   completion records (the latter lets the plan show *who* completed each step).

---

## 11. Suggested first step

Land **Phase 0** behind no behaviour change: extract a `core/Project` model that
`_write()`/`_open()` delegate to, with a golden test asserting every existing
`.redline` round-trips unchanged. That single refactor unblocks versioning,
history, and every later sync phase — and ships value (a testable model) even if
the server is never built.
