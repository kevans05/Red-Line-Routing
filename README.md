# Red-Line-Routing

All-in-one electrical job planner for terminal wiring work. Plan, track, and document Remove / Add / Move / Block / Unblock / Testing jobs, manage drawings and relay settings, and export complete print-ready PDF work packages with the Export Wizard.

## Requirements

- Python 3.8 or later
- `tkinter` (included in most Python distributions; on Linux install `python3-tk`)

No pip packages required — a patched copy of [pypdf](https://github.com/py-pdf/pypdf) is vendored in the `pypdf/` folder for PDF merging. Keep that folder next to `wire_planner.py`.

## Running

```bash
python3 wire_planner.py
```

On first launch you will be prompted to configure your organisation's base URLs (drawing server, CROW system, Aspen, relay settings). These settings are global and stored in `~/.redlinerouting.db` (SQLite; settings from an older `~/.redlinerouting.json` are migrated automatically). You can skip and update them later via **File → Software Settings**.

## First-time setup flow

```
Launch → Software Settings (global URLs, once only)
       → Landing screen (open existing or create new)
             ├── Quick Start  — blank planner, start adding jobs immediately
             └── Wizard       — 5-step guided setup
                   1. Project Info  (name, site, save location, notes)
                   2. Drawings      (register drawings upfront)
                   3. Relays        (relay devices + Aspen settings)
                   4. CROWs         (outage records)
                   5. Summary       (review + create project folder)
```

## Project structure on disk

When you save or complete the wizard, a folder is created:

```
<ProjectName>/
  <ProjectName>.redline      ← JSON file (open this to reload a project)
  Drawings/                   ← auto-downloaded drawing files
  Relay Settings/             ← auto-downloaded relay setting files
  Maintenance Standards/      ← auto-downloaded maintenance standard files
  Engineering Standards/      ← auto-downloaded engineering standard files
  CROW Outage/                ← CROW files and attached documents
  Tailboards/
    Completed/
  Other/
```

Open an existing project with **File → Open** and browse to the `.redline` file.

## Tabs

| Tab | Purpose |
|---|---|
| **Work Order** | Add, edit, reorder, and complete jobs. Left pane = job list; right pane = detail preview. |
| **Project Drawings** | Drawing registry (name, title, revision, URL). Ctrl-click a row to open the URL. **⬇ Download All** fetches files into `Drawings/`. |
| **Relay Settings** | Per-device relay records (Device ID, revision, engineer, contact, URL, Aspen model). Ctrl-click to open URL. |
| **Maintenance Standards** | Maintenance standard registry with telecom/transmission URLs and downloads. |
| **Engineering Standards** | Engineering standard registry with downloads. |
| **CROW** | Outage records (number + URL + attached documents) and project-level notes. |

### Standards library

Every maintenance or engineering standard you add to any project is remembered globally (in `~/.redlinerouting.db`). In a new project, click **📚 From Library** on either standards tab to multi-select remembered standards and add them in one click — no re-typing. Standards from older projects are picked up automatically when you open them.

## Job types

| Type | Description |
|---|---|
| **REMOVE** | Remove a wire from start → end endpoint |
| **ADD** | Add a wire from start → end endpoint |
| **MOVE** | Remove from one location, add at another (two endpoint pairs) |
| **BLOCK** | Apply protection block — carries protection details and drawings |
| **UNBLOCK** | Remove protection block |
| **TESTING** | Free-form testing step with notes |

Each job shows coloured in the Work Order tree and in every Export Wizard output.

## Drawing naming convention

```
XXXX - Y ZZ - IIII - N
Site   Type    Subject  Serial  Sheet
```

`Y = H` means a horizontal/schematic drawing — the **Drawing Cell** field is enabled only for H-type drawings. The app enforces this automatically.

## Modes

Switch between **Planner** and **Implementation** mode with the buttons in the header bar.

- **Planner** — full edit access to all tabs.
- **Implementation** — read-only step list on the left, step details on the right, and a file viewer showing downloaded drawings and relay settings from the project folder. Check off steps as you complete them.

## Export Wizard

All exporting goes through the **Export Wizard** — header-bar button, **File → Export Wizard…**, or **Ctrl+E**.

**Step 1 — formats** (pick any combination):

| Format | Output |
|---|---|
| **PDF Package** | One merged, print-ready PDF: cover page with outage numbers and table of contents, colour-coded work order table, registry tables, and every downloaded PDF from the project folders appended. `.txt` and `.docx` attachments are converted to PDF automatically (Word docs need LibreOffice or Microsoft Word installed). |
| **HTML / PDF** | Screen-optimised HTML page with live hyperlinks — open in a browser, Ctrl+P → Save as PDF. |
| **Tablet (iPad)** | Large-text HTML for Safari with relative links to the downloaded files in the project folder. |

**Step 2 — sections.** Each optional section (Drawings, Relay Settings, Maintenance Standards, Engineering Standards) can be set to:

- **Skip** — leave it out entirely
- **Print** — include the section table and its downloaded documents
- **TOC only** — list it on the cover page as *printed separately* without adding pages (useful when you already have that document printed)

**Step 3 — paper sizes.** Choose a page size per section: Letter Portrait/Landscape or 11×17 Portrait/Landscape. Work orders default to 11×17 Landscape.

The finished PDF is saved into the project folder as `Package_<date>.pdf` and revealed in Explorer/Finder.

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
| `drawing_search_url` | Future: drawing search integration |
| `aspen_url` | Future: Aspen integration |
| `base_crow_url` | Pre-fills the URL field when adding a CROW |
| `base_relay_url` | Pre-fills the URL field when adding a relay setting |

## .redline file format

The `.redline` file is plain JSON and can be inspected or edited in any text editor.

```json
{
  "project": "Site Name Work Order 123",
  "title_page": {
    "notes": "...",
    "crows": [{"outage_number": "CR-001", "url": "https://...", "files": ["outage.pdf"]}]
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
  "history": {"device": [], "location": [], "pin": [], "panel": [], "wire": []},
  "jobs": [
    {
      "type": "REMOVE",
      "description": "...",
      "wire": "RD",
      "start": {"device": "...", "location": "...", "pin": "...", "panel": "...",
                "drawing": "...", "drawing_rev": "", "drawing_url": "", "drawing_cell": ""},
      "end":   { "…same fields…" },
      "completed": false
    }
  ]
}
```
