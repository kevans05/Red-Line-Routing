# Remote Collaboration & Rebuild — Design

**Status:** Draft / RFC — v3 (all-Microsoft identity; local activity log as the near-term feature)
**Audience:** maintainers of Red-Line-Routing
**Author:** drafted from the "Future Plan's Ideas" brain-dump

This document turns the future-plans brain-dump into a concrete, phased
engineering plan. It is deliberately a *design*, not an implementation — the
goal is to agree the shape of the rebuild before any code moves.

**v2 changes:** client dependency policy resolved with a worked evaluation
([§4.7](#47-client-dependency-policy-resolved)); the setup wizard is **removed
now, remade later** ([§6](#6-scope-what-to-cut-or-defer)); the concurrency model
is corrected (stable IDs + server sequence, not array-index + wall-clock); and a
new [§9 Open problems](#9-open-problems--gaps) collects the gaps the brain-dump
did not address. Remaining human calls are in [§10](#10-open-decisions).

**v3 changes:** the org is **exclusively Microsoft**, so identity is settled — the
Windows/Entra account underpins **local attribution now** and Entra SSO + Kerberos
relay + ADCS signing later ([§4.2](#42-identity-authn--authz), [§4.4](#44-files--bandwidth)).
A new [§5.1](#5-data-model-changes) makes a **local, unsigned activity log** (full
scope) the first user-visible feature — accountability now, op-log seed for later.

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

### Explicit scope direction (resolved)

- **Drop the "no pip packages" rule** for the **server** (it's a container, free
  to use any deps). The **client** stays **minimal pip**: local-only mode is
  **zero-pip**, connected mode takes a small, each-justified, *optional* set with
  a stdlib fallback — see [§4.7](#47-client-dependency-policy-resolved).
- **Local-only mode is a first-class feature**, not a fallback: "this work does
  not need a central server and can be done local for small jobs."
- **Cut scope.** The 5-step setup **wizard is removed now and remade later**,
  once the collaborative workflow is settled — see [§6](#6-scope-what-to-cut-or-defer).

---

## 2. Where we are today (and why it matters)

A rebuild has to start from an honest read of the current code. The relevant
constraints, with file references:

1. **Whole-file save/load.** `RedLineApp._write()` (`wire_planner.py:11969`)
   serialises the *entire* project to one JSON blob; `_open()`
   (`wire_planner.py:11896`) reads it back. There is no notion of a change, a
   version, or a diff — the unit of persistence is "the whole plan."

2. **State is welded to the GUI.** Notes live in a `tk.Text` widget
   (`self.title_notes`) and are only pulled into the model at save time
   (`_write()` does `tp["notes"] = self.title_notes.get(...)`). There is no
   headless, serialisable "project model" a server or sync engine could operate
   on. This is the single biggest blocker and the first thing to fix.

3. **The instant-check-off primitive already exists.** Job completion is just
   `job["completed"]` (a bool on each job dict, see `empty_job()`
   `wire_planner.py:267`). This maps directly onto the "instant op" class.

4. **No stable identity for anything.** `empty_job()` (`:267`) returns a
   positional dict — a job is identified only by its **index** in `self.jobs`.
   This breaks index-based sync the instant two people reorder/insert/delete
   concurrently. See [§9.1](#9-open-problems--gaps).

5. **Global vs per-project split is already established.** Cross-project state
   (settings, standards library, drawing cache) is in SQLite via `_AppDB`
   (`wire_planner.py:5060`). The rebuild keeps this split and adds a third tier:
   *shared project state on the server.*

6. **Downloads already abstract transport.** `_download_with_progress()`
   (`wire_planner.py:8095`) handles HTTP(S), **the user's browser cookies** as
   auth (`request_headers`, the Edge/Chrome cookie-grab), and `file://`/UNC via
   `_file_url_to_path()` (`:234`). That the auth is the *user's own session* is
   central to the relay problem in [§9.4](#9-open-problems--gaps).

7. **12k-line monolith, stdlib-only, zero tests.** The rebuild is the moment to
   split this into packages and — critically — introduce a test harness.

---

## 3. Design principles

1. **Local-first.** Fully usable with no network. The server is an accelerator
   and a meeting point, never a hard dependency.
2. **Sync changes, not files.** Stream tiny operations for the plan; lazy-pull
   big documents on demand.
3. **Every change is attributable.** Identity from SSO; integrity from a signature.
4. **Two change classes, one document.** `instant` (apply + broadcast now) and
   `review` (propose → approve → merge), set by **per-field policy**.
5. **Bandwidth is a feature.** Deltas over snapshots, hashes over downloads,
   compression on, resumable transfers, pull-on-demand documents.
6. **Verifiable copies.** Any client can cheaply prove its local plan equals
   central by comparing a content hash — without downloading the plan.
7. **Stable identity underpins all of it.** Every syncable entity carries a UUID;
   ordering is explicit data, never array position.

---

## 4. Target architecture

```
                 ┌─────────────────────────────────────────────┐
                 │  CENTRAL SERVER  (container, high bandwidth) │
                 │                                              │
   SSO / OIDC ◄──┤  • Auth (identity) + authz (per-plan roles)  │
                 │  • Canonical plan state + operation log      │
                 │  • Approval queue (review-gated changes)     │
                 │  • Content-addressed file store + relay      ├──► Corporate
   Reporting ◄───┤  • Push channel (SSE / WebSocket)            │    drawing /
   sites         │                                              │    relay sources
                 └───────────────▲───────────────▲──────────────┘
                                 │ ops + hashes  │ lazy file pulls
                 thin pipe ──────┼───────────────┼──────── thin pipe
            ┌────────────────────┴───┐     ┌─────┴──────────────────┐
            │  CLIENT A (low bw site)│     │  CLIENT B (office)     │
            │  local plan + op log   │     │  local plan + op log   │
            │  pending local ops     │     │  approvals inbox       │
            └────────────────────────┘     └────────────────────────┘

   No-server mode: a CLIENT runs entirely on its own local plan + op log.
```

### 4.1 Topology & deployment
- **Server**: a container image; one command to run, a compose file wires it to
  its database and file store. Lives at a high-bandwidth site (likely on-prem —
  see [§9.12](#9-open-problems--gaps)).
- **Client** runs in three modes: **local-only** (no account/server — today's
  behaviour, zero-pip), **connected** (signed in, syncing), **offline-connected**
  (was connected, link dropped; queues ops and reconciles on reconnect).
- A plan is **promotable**: start local, later "publish to server" to collaborate.

### 4.2 Identity, authn & authz
- **Authentication:** SSO via **OIDC** (the Edge/DPAPI cookie code implies
  Microsoft **Entra** — confirm, [§10](#10-open-decisions)). Browser auth-code
  flow → short-lived token + refresh.
- **Authorization is separate and must be defined** (the brain-dump only says
  "approve changes", which is authz, not authn). Minimal **per-plan roles**:
  owner / approver / contributor / viewer. Self-approval policy is configurable
  (see [§9.2](#9-open-problems--gaps)).
- **Offline auth:** cache a refreshable token with an **offline grace window** so
  a dead link mid-shift doesn't stop work ([§9.3](#9-open-problems--gaps)).
- **All-Microsoft environment (confirmed).** The org is exclusively Microsoft, so
  one identity — the **Windows/Entra account** — underpins everything. It powers
  **local attribution today** with *zero* infrastructure ([§5.1](#5-data-model-changes)),
  and the *same* account later powers Entra **SSO**, **Kerberos delegation** for
  the relay ([§4.4](#44-files--bandwidth)), and **certificate signing** via AD
  Certificate Services ([§10](#10-open-decisions)). We **federate to Windows/Entra,
  we don't build auth.** Three acts stay distinct: *authenticate* (needs the IdP,
  periodic), *sign* (local key, offline-capable), *approve* (server-side, at merge).

### 4.3 The sync model (the core)

Represent a plan as **canonical state + an append-only operation log**.

- **Operation (`op`):** the unit of change. Targets a **stable entity id**, never
  an array index.

  ```jsonc
  {
    "id":     "uuid",
    "plan":   "plan-id",
    "base":   "content-hash the op was authored against",
    "seq":    "server-assigned monotonic sequence (set on accept)",
    "lclock": "author logical clock (ordering; not wall-clock)",
    "author": "sso-subject-id",
    "ts":     "wall-clock — for DISPLAY only",
    "class":  "instant" | "review",
    "target": "job:UUID.completed",   // entity id + field, not jobs[3]
    "op":     "set" | "insert" | "delete" | "reorder",
    "value":  true,
    "sig":    "signature over the canonical op bytes"
  }
  ```

- **Field policy decides the class:**

  | Target | Class | Why |
  |---|---|---|
  | `job:*.completed`, sign-on toggles | instant | Operational, time-critical |
  | job structure / endpoints / wire | review | Substantive plan content |
  | `title_page.notes`, job notes | review | "Important notes" — review-gated |
  | registries (drawings/relays/standards) | review | Shared reference data |
  | job **ordering** | review | Order is shared data ([§9.5](#9-open-problems--gaps)) |

- **Instant ops** apply locally at once and broadcast — but **only against
  canonical items** (you can't tick a line that exists solely in someone's
  unapproved changeset).
- **Review ops** collect into a **proposed changeset** ("PR"); an authorised
  approver sees a human-readable diff and **approves/merges** or rejects. On
  merge, ops fold into canonical state and a new content hash is minted.
- **Ordering is by server-assigned `seq` + logical clocks, not wall-clock `ts`**
  — field/offline devices have clock skew (correction vs v1).
- **Content hashing / checksums.** Canonical state hashes (SHA-256 over canonical
  JSON: sorted keys, fixed separators). Every committed version = its hash
  (content-addressed, like a Git commit). A client confirms it's in sync by
  fetching **just the hash** — no plan download.
- **Why an op log, not file-diffing?** Tiny on the wire, free history/blame/revert,
  per-field instant-vs-review policy, graceful offline (queue + replay).

### 4.4 Files & bandwidth
- **Documents stay pull-on-demand.** The plan syncs; the PDFs behind it do not
  stream to a thin client unasked — the client sees a document *exists* (name +
  hash + size) and pulls only on user action, preserving today's behaviour in
  `_download_with_progress()`.
- **Content-addressed file store** keyed by hash → dedupe, integrity check on
  download, resumable/range transfers for flaky links.
- **The server as a relay**: a thin client asks the server to fetch from a
  corporate source and/or push a package to a reporting site; the bulk transfer
  happens server-side over the fat pipe. **Leading answer in an all-Microsoft
  shop:** Kerberos Constrained Delegation (KCD/RBCD) with a **gMSA**, so the server
  fetches *as the user* with **no stored credentials** — falling back to the gMSA
  acting as itself ("on behalf of Jane") for store-and-forward after the user
  disconnects; Entra-cloud connectors use OBO/Graph instead. Still open per
  connector and **deferred until server buy-in** — see [§9.4](#9-open-problems--gaps),
  [§10](#10-open-decisions).
- **On the wire:** gzip everything; send deltas; make bulk transfers resumable.

### 4.5 Conflict resolution & history (Git-like)
- **History for free.** The op log *is* the history: who/what/when, signed.
- **Instant fields:** converge by `seq` order; set-like state (sign-ons) unions.
  For check-offs, prefer **per-user completion records** so the plan can show
  *who* completed each step (decision in [§10](#10-open-decisions)).
- **Review fields:** conflicts surface at merge time and the approver resolves
  them (PR-merge model). The op's `base` hash lets the server detect stale
  authorship and route to conflict handling instead of applying blindly.
- **Deletes use tombstones** so delete-vs-edit / delete-vs-checkoff has an
  explicit winner, never silent loss.

### 4.6 Transport & protocol
- **Control plane:** HTTPS/JSON REST — auth, push ops, fetch hash, fetch snapshot,
  list/approve changesets, request a file relay.
- **Push plane:** server→client live channel for instant ops and "changeset to
  review" notices. **SSE or long-poll first** (stdlib-friendly, proxy-friendly on
  thin pipes); WebSocket as an opt-in upgrade.
- **Offline:** all client writes hit a local op queue first, flush on reconnect;
  the UI never blocks on the network.

### 4.7 Client dependency policy (resolved)

Local-only mode can stay **100% stdlib**; the connected client can be
**near-stdlib**. pip deps are **optional enhancements**, each buying one thing.

| Capability | Pure-stdlib path | Optional pip option | What the dep buys |
|---|---|---|---|
| HTTP to server | `urllib` (already used, `:8095`) | `requests`/`httpx` | ergonomics, pooling |
| Hash / checksum | `hashlib` | — | — |
| Sign (symmetric) | `hmac`+`hashlib` | — | HMAC attestation |
| Sign (asymmetric / PKI) | none | **`cryptography`** | true non-repudiation |
| Push channel | SSE/long-poll over `urllib` | `websocket-client` | lower-latency duplex |
| SSO / OIDC | `urllib` + tiny local `http.server` callback | `msal` (Entra) / `authlib` | token refresh/cache |
| Canonical JSON, gzip | `json`, `gzip`/`zlib` | — | — |

**Adopted policy:** local-only = **zero pip** (hard rule). Connected = a small,
*optional*, each-justified set; degrade to stdlib transport (SSE/long-poll) and
HMAC when a dep is absent. The one dep most worth taking is **`cryptography`**
for genuine per-user signatures. **Server** has no such constraint.

---

## 5. Data-model changes

The enabling refactor is **a headless project model, decoupled from Tk.**

- A `core/` package: a plain-Python `Project` (jobs, registries, title page,
  tailboard refs, history) serialising to/from `.redline` JSON **byte-compatibly**.
- The GUI binds *to the model*; notes/completion live in the model, not widgets.
- **Stable IDs** on every syncable entity (jobs and keyed registry/CROW entries),
  assigned on creation and via a **deterministic, idempotent migration** for
  existing files.
- Additive version metadata so old files still load:

  ```jsonc
  { "schema": 2, "plan_id": "uuid", "version": "content-hash",
    "server": "https://… | null"  /* null = local-only */ }
  ```

- The op log + pending changesets live **beside** the `.redline` (a small SQLite
  sidecar), keeping the plan file clean and human-readable.

### 5.1 Local attribution & the activity log (near-term, no server)

**Decided as the first user-visible feature.** Use the Windows identity to record
*who did what, when* — "Kyle completed this step", "Kyle attached this document" —
with **zero infrastructure**. It delivers accountability now and **seeds the
op-log** the sync system needs later: when buy-in arrives, the same records simply
start getting signed.

- **Identity (local, zero new deps):** `getpass.getuser()` (stdlib) + `USERDOMAIN`
  give `DOMAIN\login`; a one-time, user-confirmable **display name** ("Kyle Evans")
  is remembered in the global DB (`_AppDB`) so the UI shows a name, not a login.
- **Scope — full activity log** (user's choice): stamp completions, document
  attach/upload, job add/edit/delete, and registry/CROW changes; **fold the
  existing `tailboard_signons`/`safety_signons` name lists into the same actor
  model** so "who" is captured one way everywhere.
- **Record shape (upgrade-compatible):**

  ```jsonc
  { "ts": "2026-06-25T14:30:00", "actor": "DOMAIN\\kevans05",
    "actor_name": "Kyle Evans", "action": "complete",
    "target": "job:<uuid>", "sig": null }   // sig null now → ADCS cert later
  ```

  Plus inline convenience fields where shown (`completed_by`/`completed_at` on a
  job, `added_by` on a document) so the Implementation list can render
  "✓ Kyle Evans · Jun 25 14:30" with no lookups.
- **Storage:** the append-only `activity` list travels **inside the `.redline`** (so
  it reaches anyone who opens the plan) and is the same data the Phase-1 op-log
  sidecar adopts. Targets reference **entity UUIDs** — which is why this rides on
  the Phase-0 stable-ID work.
- **⚠️ Honest caveat:** this is **good-faith, local, *unsigned*** attribution. It
  trusts the Windows login and the `.redline` is editable JSON — it's
  *accountability and history*, **not** tamper-proof non-repudiation. Real proof
  arrives later with the ADCS signature + server verification (the `sig` field).
  Don't lean on it for legal/safety *proof* until then.

---

## 6. Scope: what to cut or defer

Per "remove the wizard and remake it later":

- **Remove the 5-step wizard now** (~700 lines): `ProjectWizard`
  (`wire_planner.py:6724`), the `new_wizard` branch of `_startup_flow` (`:7403`),
  `_apply_wizard_result` (`:7409`), the wizard `RelayDialog` (`:6667`), and the
  wizard option in `LandingDialog` (`:6622`, `:6660`).
- **Safe because** `_apply_wizard_result` only duplicates folder-creation + save
  logic `_save_as` (`:11947`) already performs, and the "Quick Start" path
  already gives a no-wizard entry. After removal `LandingDialog` collapses to
  **Open / Quick Start**.
- **Remake the wizard later**, redesigned around the collaborative workflow.
- **One transport for files.** Collapse the per-tab download paths onto the single
  relay-aware downloader.

Aim: a smaller, modular core (`core/`, `sync/`, `server/`, `ui/`) instead of
growing the 12k-line monolith.

---

## 7. Phased roadmap

| Phase | Deliverable | Server? |
|---|---|---|
| **0. Decouple + cut** | `core/` model; **stable IDs** + migration; **wizard removal**; **golden round-trip test**; **local activity log** (Windows-identity attribution, unsigned — seeds the op-log, [§5.1](#5-data-model-changes)) | No |
| **1. Versioning** | Canonical hashing/checksums; `schema`/`plan_id`/`version`; local op log (**adopts the §5.1 activity list**); history/undo | No |
| **2. Server + identity** | Container server; OIDC login + **per-plan authz roles**; **offline-auth grace**; publish/clone; whole-plan push/pull with hash verify | Yes |
| **3. Field-level sync** | Op streaming; **instant** check-offs (canonical-only); **review-gated** changesets for plan/notes; SSE/WS push; tombstones | Yes |
| **4. File relay** | Content-addressed store; pull-on-demand; **relay credential model**; reporting-site upload; retention/compaction | Yes |
| **5. Signing & approvals** | Per-user signed ops (`cryptography`); Git-like diff/approve/reject UX; presence + approval inbox + audit export | Yes |

Phases 0–1 deliver real value (history, integrity, undo, stable IDs, less code)
to **today's single-user app with no server** — a safe beachhead.

---

## 8. Security & trust notes
- TLS throughout; short-lived tokens; silent refresh; offline grace window.
- Signature verification on the server is the trust boundary for authorship.
  Keep signing keys in the OS keystore.
- The relay's custody of corporate credentials is sensitive — see [§9.4](#9-open-problems--gaps).
- Treat all content fetched from corporate/reporting sites as untrusted input.

---

## 9. Open problems & gaps

Things the brain-dump did not address. ⚠️ marks the heavyweight ones.

1. **⚠️ Stable identity (confirmed gap).** Jobs are positional (`empty_job()`
   `:267`). Ops keyed on index corrupt under concurrent reorder/insert/delete.
   Fix: UUID per syncable entity + idempotent migration. Prerequisite for sync;
   lands in Phase 0.
2. **⚠️ Authorization ≠ authentication.** "Approve changes" needs a *who-may-approve*
   model. Define per-plan roles; decide self-approval (likely allowed for solo
   field crews, deniable for safety-critical fields).
3. **⚠️ Offline auth & signing.** SSO needs the IdP, but the point is thin/dead-link
   sites. Cache a refreshable token + offline grace; sign locally while offline;
   re-verify on reconnect. Define the grace period and past-grace behaviour.
4. **⚠️ Relay credential problem.** Today auth is the *user's browser cookies*
   (`_build_tailboard_headers` `:11371`); a server can't reuse a browser session.
   **All-Microsoft leading answer:** Kerberos delegation (KCD/RBCD) + a **gMSA**
   → fetch *as the user*, no stored creds (Entra-cloud connectors use OBO/Graph);
   signing via **ADCS** certs. Open per connector: on-prem-Kerberos vs cloud-Graph,
   and sync-vs-store-and-forward. **Deferred until server buy-in.** Shapes the
   whole server.
5. **Concurrency correctness (corrects v1).** Order by server `seq` + logical
   clock, not wall-clock. Instant ops apply to canonical items only. Tombstones
   for deletes. Job ordering is review-class shared data (explicit order field /
   fractional index), not array position.
6. **Binary file conflicts & retention.** Two crews upload a new revision of the
   same doc — binaries don't merge. Central winner + attribution (latest-wins +
   archived history, surfaced not auto-clobbered). Define op-log compaction and
   file-store retention — both grow forever otherwise.
7. **Offline divergence UX.** A full-shift offline crew (or two at once) produces
   large reconciliations and real review conflicts. Provide a "your changes vs
   central" review at reconnect; never auto-clobber.
8. **Shared scope beyond `.redline`.** The global DB holds the standards library,
   settings, drawing cache. Decide which become server-shared (standards library
   is a natural candidate) vs stay local — else teams silently diverge.
9. **Cross-version client/server negotiation.** Mixed-version clients on one
   server make migration distributed. Version the wire protocol + schema;
   negotiate on connect.
10. **Existing Tablet/iOS export vs sync.** `_build_tablet_zip` already emits
    `project.redline` + `manifest.json` for a future iOS reader. Decide: read-only
    consumer vs first-class sync client — it changes the protocol surface.
11. **Presence, notifications, audit.** Soft-presence/locking to reduce collisions;
    an approval inbox so approvers learn of pending changesets; confirm any
    **regulatory retention/audit** obligation for safety work — it constrains the
    log design.
12. **Operations.** Hosting (on-prem likely, given corporate-source egress + auth)
    vs cloud; backup/DR for the now-authoritative server; a plan registry +
    per-plan access (today 1 `.redline` = 1 folder; the server holds many);
    **and a test harness — there are zero tests today.**

---

## 10. Open decisions

Recommendations first; these need a human call.

1. **IdP — RESOLVED: Microsoft Entra / AD** (the org is exclusively Microsoft).
   Federate to Windows/Entra; don't build our own auth.
2. **Relay credentials** — all-MS leaning: **KCD/RBCD + gMSA** (fetch as the user,
   no stored creds), with **OBO/Graph** for any Entra-cloud connectors. Open per
   connector: on-prem-vs-cloud, and sync-vs-store-and-forward. **Deferred until
   server buy-in.**
3. **Signing strength** — all-MS path is **ADCS** per-user certs (native PKI),
   leaning per-user signatures over HMAC. Deferred; today's attribution is
   **unsigned** ([§5.1](#5-data-model-changes)).
4. **Authz & self-approval** — role model + whether authors can approve their own.
5. **Shared scope** — which global-DB data (standards library, drawing cache)
   becomes server-shared vs stays local.
6. **iOS app** — read-only consumer vs full sync client.
7. **Hosting** — on-prem vs cloud; plus any audit/retention obligation.
8. **Check-off semantics** — single shared bool vs per-user completion records
   (the latter shows *who* completed each step).
9. **Attribution scope — RESOLVED: full activity log** (completions, attachments,
   job + registry/CROW edits), captured locally from the Windows identity,
   **unsigned for now** ([§5.1](#5-data-model-changes)).

---

## 11. Suggested first step

Land **Phase 0** behind no behaviour change: extract a `core/Project` model that
`_write()`/`_open()` delegate to, **add stable entity IDs with an idempotent
migration**, **remove the wizard**, and add a **golden test** asserting every
existing `.redline` round-trips unchanged (modulo the additive `id`/`schema`
fields). That single phase unblocks versioning, history, and every later sync
phase — and ships value (a testable model, stable IDs, less code) even if the
server is never built. Stamp the **local activity log** ([§5.1](#5-data-model-changes))
in the same pass: the Windows identity gives "who did what, when" with no
infrastructure, and those records are exactly what the op-log later signs.
