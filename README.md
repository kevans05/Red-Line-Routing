# Red-Line-Routing

All-in-one electrical job planner for terminal wiring work. Plan, track, and document Remove / Add / Move / Block / Unblock / Testing jobs, manage drawings and relay settings, and export to HTML, PDF, or CSV.

## Requirements

- Python 3.8 or later
- `tkinter` (included in most Python distributions; on Linux install `python3-tk`)

No third-party packages required.

## Running

```bash
python3 wire_planner.py
```

On first launch you will be prompted to configure your organisation's base URLs (drawing server, CROW system, Aspen, relay settings). These settings are global and stored in `~/.redlinerouting.json`. You can skip and update them later via **File → Software Settings**.

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
  <ProjectName>.redline    ← JSON file (open this to reload a project)
  Drawings/                 ← auto-downloaded drawing files
  Relay Settings/           ← auto-downloaded relay setting files
  CROW Outage/
  Other/
```

Open an existing project with **File → Open** and browse to the `.redline` file.

## Tabs

| Tab | Purpose |
|---|---|
| **Work Order** | Add, edit, reorder, and complete jobs. Left pane = job list; right pane = detail preview. |
| **Project Drawings** | Drawing registry (name, title, revision, URL). Ctrl-click a row to open the URL. **⬇ Download All** fetches files into `Drawings/`. |
| **Relay Settings** | Per-device relay records (Device ID, revision, engineer, contact, URL, Aspen model). Ctrl-click to open URL. |
| **CROW** | Outage records (number + URL) and project-level notes. |

## Job types

| Type | Description |
|---|---|
| **REMOVE** | Remove a wire from start → end endpoint |
| **ADD** | Add a wire from start → end endpoint |
| **MOVE** | Remove from one location, add at another (two endpoint pairs) |
| **BLOCK** | Apply protection block — carries protection details and drawings |
| **UNBLOCK** | Remove protection block |
| **TESTING** | Free-form testing step with notes |

Each job shows coloured in the Work Order tree and the exported HTML.

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

## Exports

All exports are accessible from the header bar or **File** menu.

| Export | Format |
|---|---|
| HTML / PDF | Self-contained HTML page — open in a browser and use Print → Save as PDF. Includes checkboxes; ticking a row draws a strikethrough (print-safe). |
| Report | Plain-text detailed report with full endpoint blocks |
| Table | Fixed-width ASCII table |
| CSV | Flat CSV (one row per job) |

## Keyboard shortcuts

| Action | Shortcut |
|---|---|
| Open URL in browser | Ctrl-click any URL entry field |
| Open drawing/relay URL from a list row | Ctrl-click the row |

## Autocomplete and context-aware suggestions

When editing a job, the Device / Location / Pin / Panel / Drawing comboboxes are pre-populated from:

1. **Context scoring** — values that previously appeared alongside whatever is already filled in the other fields rank first.
2. **Global history** — all values ever entered in that field.
3. **Drawing registry** — registered drawing names, for the Drawing field.

A green border on a combobox indicates context data is available and the suggestions are ranked.

## Software settings (global)

Stored in `~/.redlinerouting.json`. Accessible via **File → Software Settings**.

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
    "crows": [{"outage_number": "CR-001", "url": "https://..."}]
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
