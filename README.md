# Red-Line-Routing

All-in-one electrical job planner for terminal wiring work. Plan, track, and document wiring jobs (Remove / Add / Move / Block / Restore / Testing / Isolation / Device install & removal), manage drawings, relay settings and standards, run tailboards and safety sign-ons, and export complete print-ready PDF work packages with the Export Wizard.

## Requirements

- Python 3.8 or later
- `tkinter` (included in most Python distributions; on Linux install `python3-tk`)

No pip packages required — a patched copy of [pypdf](https://github.com/py-pdf/pypdf) is vendored in the `pypdf/` folder for PDF merging, and the drawing-search client lives in `drawing_search/`. **Keep both folders next to `wire_planner.py` and always update them together** — running a new `wire_planner.py` against an old `drawing_search/` disables features.

## Running

```bash
python3 wire_planner.py
```

On first launch you will be prompted to configure your organisation's base URLs (drawing server, drawing search, CROW system, Aspen, relay settings). These settings are global and stored in `~/.redlinerouting.db` (SQLite; settings from an older `~/.redlinerouting.json` are migrated automatically). You can skip and update them later via **File → Software Settings**.

## First-time setup flow

```
Launch → Software Settings (global URLs, once only)
       → Landing screen (open existing or create new)
             ├── Quick Start  — blank planner, start adding jobs immediately
             └── Wizard       — 5-step guided setup
                   1. Project Info  (name, site, save location, notes)
                   2. Drawings      (register drawings upfront, or search the drawing server)
                   3. Relays        (relay devices + Aspen settings)
                   4. CROWs         (outage records)
                   5. Summary       (review + create project folder)
```

## Project structure on disk

When you save or complete the wizard, a folder is created:

```
<ProjectName>/
  <ProjectName>.redline      ← JSON file (open this to reload a project)
  Drawings/                  ← auto-downloaded drawing files
  Relay Settings/            ← auto-downloaded relay setting files
  Maintenance Standards/     ← auto-downloaded maintenance standard files
  Engineering Standards/     ← auto-downloaded engineering standard files
  CROW Outage/               ← CROW files and attached documents
  Tailboards/
    Completed/               ← timestamped tailboard records
  Safety Documents/
    Completed/               ← timestamped safety records and uploads
  Other Documents/           ← free-form uploads
  Other/
```

Open an existing project with **File → Open** and browse to the `.redline` file.

## Tabs

| Tab | Purpose |
|---|---|
| **Work Order** | Add, edit, reorder, and complete jobs. Left pane = job list; right pane = detail preview. |
| **Project Drawings** | Drawing registry (name, title, revision, URL). Ctrl-click a row to open the URL. **🔍 Search…** queries the corporate drawing server. **⬇ Download All** fetches files into `Drawings/`. |
| **Relay Settings** | Per-device relay records (Device ID, revision, engineer, phone/contact, URL, Aspen model). Ctrl-click to open URL. |
| **Maintenance Standards** | Maintenance standard registry with telecom/transmission URLs and downloads. |
| **Engineering Standards** | Engineering standard registry with downloads. |
| **CROW** | Outage records (number + URL + attached documents) and project-level notes. |
| **Safety Documents** | Upload and manage safety documents; works with the safety panel in Implementation mode (template + sign-ons + timestamped saves). |
| **Other Documents** | Free-form document uploads — anything that doesn't fit the other categories. |

### Standards library

Every maintenance or engineering standard you add to any project is remembered globally (in `~/.redlinerouting.db`). In a new project, click **📚 From Library** on either standards tab to multi-select remembered standards and add them in one click — no re-typing. Standards from older projects are picked up automatically when you open them.

## Job types

| Type | Description |
|---|---|
| **REMOVE** | Remove a wire from start → end endpoint |
| **ADD** | Add a wire from start → end endpoint |
| **MOVE** | Remove from one location, add at another (two endpoint pairs) |
| **BLOCK** | Apply protection block — carries protection details, drawings, and master-blocking flags |
| **RESTORE** | Remove a protection block (can copy details from an existing BLOCK step) |
| **TESTING** | Free-form testing step with notes |
| **ISOLATION** | Isolation step with drawing references |
| **CR PROTECTION** | Control-room protection step with desk and CROW references |
| **INSTALL DEVICE** / **REMOVE DEVICE** | Add or remove a physical device at one endpoint |

Each job shows coloured in the Work Order tree and in every Export Wizard output.

> Compatibility note: RESTORE is stored as `"UNBLOCK"` inside `.redline` files so older files keep loading; only the display name changed.

## Drawing naming convention

```
XXXX - Y ZZ - IIII - N
Site   Type    Subject  Serial  Sheet
```

`Y = H` means a horizontal/schematic drawing — the **Drawing Cell** field is enabled only for H-type drawings. The app enforces this automatically.

## Drawing search

Configure **Drawing Search URL** in Software Settings, then use the **🔍 Search…** button on the Project Drawings tab (also available inside the project wizard). Search by drawing number, title, facility, type, subject, serial range, and state, then multi-select results to import them into the registry.

- **Authentication** is cookie-based: paste your browser's request headers into Software Settings, or click **Grab from Browser** to extract cookies for the drawing server directly from Edge/Chrome (Windows only; reads the browser cookie database and decrypts values via DPAPI). Rotating session cookies are absorbed automatically from server responses and persisted.
- **Caching**: categorical searches (facility/type/subject/state, no free-text) are cached in `~/.redlinerouting.db` and shared by all projects. Stale entries refresh automatically every 15 minutes (entries older than 4 hours; override with the `drawing_cache_refresh_hours` setting). Cache hits show instantly and revalidate in the background. Empty results are never cached, so an expired login can't poison the cache.
- **0 results — possible auth issue**: click **Show Response** in the search dialog to inspect the raw server reply; refresh your cookies if it is a login page.

## Modes

Switch between **Planner** and **Implementation** mode with the buttons in the header bar.

- **Planner** — full edit access to all tabs.
- **Implementation** — read-only step list on the left, step details on the right, and a file viewer showing downloaded drawings and relay settings from the project folder. Check off steps as you complete them. Special rows above the job steps:
  - **PREP** — project briefing (notes, CROWs, registries with engineer phone numbers).
  - **TAILBOARD** — run the tailboard: template, sign-on list, timestamped saves into `Tailboards/Completed/`.
  - **SAFETY DOCS** — same controls for safety documents: pick a template (remembered globally), collect sign-ons, save timestamped records into `Safety Documents/Completed/`.

## Export Wizard

All exporting goes through the **Export Wizard** — header-bar button, **File → Export Wizard…**, or **Ctrl+E**.

**Step 1 — formats** (pick any combination):

| Format | Output |
|---|---|
| **PDF Package** | One merged, print-ready PDF (`Package_<date>.pdf`): cover page with outage numbers and table of contents, colour-coded work order table, registry tables, and every downloaded PDF from the project folders appended. `.txt` and `.docx` attachments are converted to PDF automatically (Word docs need LibreOffice or Microsoft Word installed). |
| **HTML / PDF** | Screen-optimised HTML page (`Digital_<date>.html`) with live hyperlinks — open in a browser, Ctrl+P → Save as PDF. |
| **Tablet (iPad)** | Zip package (`Tablet_<date>.zip`) for transfer to a tablet: `manifest.json` (package metadata + file inventory), `index.html` (large-text HTML, relative links work once extracted), `project.redline` (full project JSON), and all documents of the selected sections. Designed to be read by a companion tablet app. |

**Step 2 — sections.** Each optional section (Drawings, Relay Settings, Maintenance Standards, Engineering Standards, Tailboards, Safety Documents, Other Documents) can be set to:

- **Skip** — leave it out entirely
- **Print** — include the section table and its downloaded documents
- **TOC only** — list it on the cover page as *printed separately* without adding pages (useful when you already have that document printed)

**Step 3 — paper sizes.** Choose a page size per generated section: Letter Portrait/Landscape or 11×17 Portrait/Landscape. Work orders default to 11×17 Landscape. (Document-only sections like Tailboards keep their original page sizes.)

PDF and zip outputs are revealed in Explorer/Finder; HTML opens in the browser.

## Keyboard shortcuts

| Action | Shortcut |
|---|---|
| Export Wizard | Ctrl+E |
| Open URL in browser | Ctrl-click any URL entry field |
| Open drawing/relay URL from a list row | Ctrl-click the row |

## Autocomplete and context-aware suggestions

When editing a job, the Device / Location / Pin / Panel / Drawing comboboxes are pre-populated from:

1. **Context scoring** — values that previously appeared alongside whatever is already filled in the other fields rank first.
2. **Global history** — all values ever entered in that field.
3. **Drawing registry** — registered drawing names, for the Drawing field.

A green border on a combobox indicates context data is available and the suggestions are ranked.

## Software settings (global)

Stored in `~/.redlinerouting.db` alongside the standards library and the shared drawing-search cache. Accessible via **File → Software Settings**.

| Key | Used for |
|---|---|
| `base_drawing_url` | Pre-fills the URL field when adding a new drawing |
| `drawing_search_url` | Corporate drawing search endpoint (enables 🔍 Search) |
| `drawing_search_path` | Optional override of the search form path |
| `request_headers` | Raw request headers (incl. Cookie) used to authenticate drawing search |
| `aspen_url` | Pre-fills Aspen links on relay settings |
| `base_crow_url` | Pre-fills the URL field when adding a CROW |
| `base_relay_url` | Pre-fills the URL field when adding a relay setting |
| `safety_template_path` | Safety document template used by the SAFETY DOCS panel |
| `drawing_cache_refresh_hours` | Cache staleness threshold (default 4 hours) |

## .redline file format

The `.redline` file is plain JSON and can be inspected or edited in any text editor.

```json
{
  "project": "Site Name Work Order 123",
  "title_page": {
    "notes": "...",
    "crows": [{"outage_number": "CR-001", "url": "https://...", "files": ["outage.pdf"]}],
    "tailboard_signons": [], "tailboard_done": false,
    "safety_signons": [],   "safety_done": false
  },
  "drawing_registry": {
    "XXXX-HZZ-0001-1": {"title": "...", "rev": "C", "url": "https://...", "notes": ""}
  },
  "relay_settings": {
    "DEVICE_ID": {
      "title": "", "revision": "", "engineer": "", "contact": "",
      "url": "", "wo_device": "", "aspen_model": "", "aspen_url": "", "aspen_notes": ""
    }
  },
  "maintenance_standards": {},
  "engineering_standards": {},
  "history": {"device": [], "location": [], "pin": [], "panel": [], "wire": []},
  "jobs": [
    {
      "type": "REMOVE",
      "description": "...",
      "wire": "RD",
      "start": {"device": "...", "location": "...", "pin": "...", "panel": "...",
                "drawing": "...", "drawing_rev": "", "drawing_url": "", "drawing_cell": ""},
      "end":   { "…same fields…" },
      "notes": "",
      "completed": false
    }
  ]
}
```

(`relay_settings` is the historical JSON key for the relay registry — kept for compatibility with files saved by older versions.)

## Troubleshooting

| Symptom | Fix |
|---|---|
| `TypeError: … unexpected keyword argument` after updating | Your `drawing_search/` folder is older than `wire_planner.py`. Update the whole folder (`git pull`), not just the main file. |
| Drawing search returns 0 results | Session cookies expired — use **Grab from Browser** or paste fresh headers, then search again. Click **Show Response** to confirm (a login page means expired auth). |
| Cookie grab fails with a lock error | Close every Edge/Chrome window including background apps in the system tray, then click Grab again. |
| Export says pypdf unavailable | Keep the `pypdf/` folder next to `wire_planner.py`; export falls back to print-ready HTML until it's restored. |

## Development

```bash
# Syntax-check without launching the GUI
python3 -c "import ast; ast.parse(open('wire_planner.py').read()); print('OK')"
```

`CLAUDE.md` documents the internal architecture (file layers, data model, UI conventions, vendored pypdf patches) for anyone working on the code.
