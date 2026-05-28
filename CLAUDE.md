# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
python3 wire_planner.py
```

No build step, no package install — the entire application is a single Python 3 file using only the standard library (`tkinter`, `json`, `os`, `threading`, `urllib`, `webbrowser`, `subprocess`).

Syntax-check without launching the GUI:

```bash
python3 -c "import ast; ast.parse(open('wire_planner.py').read()); print('OK')"
```

## Repository layout

```
wire_planner.py   # entire application (~3 500 lines)
.gitignore        # ignores __pycache__ and *.pyc
```

No tests, no CI config, no requirements file.

## Architecture — one file, six layers

All code lives in `wire_planner.py`. Reading top-to-bottom follows the dependency order:

| Lines (approx) | Layer |
|---|---|
| 1 – 110 | Module-level data factories and UI helpers |
| 117 – 560 | Reusable widget classes (`DrawingAwareFrame`, `EndpointFrame`, composite frames) |
| 640 – 1015 | Job and protection dialogs (`JobDialog`, `DrawingEditDialog`, …) |
| 1016 – 1497 | Plain-text / HTML / CSV export generators (no GUI) |
| 1498 – 2320 | Startup flow dialogs and the project wizard |
| 2324 – 3507 | `WirePlannerApp` — the main `tk.Tk` window |

## Key data model

`WirePlannerApp` owns these instance attributes; they are serialised together into the `.wirePlan` JSON:

```python
self.jobs             # list of job dicts (see empty_job())
self.drawing_registry # {name: {title, rev, url, notes}}
self.relay_registry   # {device_id: {title, revision, engineer, contact, url, …}}
self.title_page       # {notes: str, crows: [{outage_number, url}]}
self.history          # {device/location/pin/panel/wire: [str, …]}  – autocomplete pool
```

Global (not per-project) settings live in `~/.redlinerouting.json` and are loaded into `self.app_config`.

Job types: `REMOVE`, `ADD`, `MOVE`, `BLOCK`, `UNBLOCK`, `TESTING`.  
`BLOCK`/`UNBLOCK` carry a `protection` sub-dict; `MOVE` carries both `start/end` and `add_start/add_end` endpoint pairs.

## Drawing name convention

`XXXX-YZZ-IIII-N`  (Site — Type — Subject — Serial — Sheet)

`Y == 'H'` signals a horizontal drawing: the **Drawing Cell** field is enabled only for H-type drawings (`is_h_type_drawing()`). All other types disable that field automatically.

## UI patterns to keep consistent

### `tk.Frame` vs `ttk.Frame`

Use `tk.Frame(bg=…)` whenever the frame needs a specific background colour (dark header, light content area). `ttk.Frame` ignores `bg` on most platforms.

### `side="bottom"` packing rule

In any `tk.Frame` / `ttk.Frame` that mixes `expand=True` content with footer widgets, **pack the footer first** (with `side="bottom"`) before packing the expanding centre. Violating this order makes the footer invisible.

### Startup dialog sizing

Call `_center_window(win)` (no w/h) to auto-size a dialog to its content. Only pass explicit dimensions for two-pane layouts like `ProjectWizard`.

### `_hover_btn(frame, normal_bg, hover_bg)`

Walks the full widget tree under `frame` and binds `<Enter>`/`<Leave>` to swap background colour. Use this on any `tk.Frame` acting as a clickable card. Remember to bind `<Button-1>` on **all** children including the accent strip, not just the content frame.

## Startup flow

```
WirePlannerApp.__init__
  └── after_idle(_startup_flow)
        ├── SoftwareSetupDialog  (first run only — writes ~/.redlinerouting.json)
        ├── LandingDialog        (open existing / new quick / wizard)
        └── ProjectWizard        (optional 5-step wizard → _apply_wizard_result)
```

## Export functions (no GUI dependency)

These four functions are pure and can be called or tested independently:

| Function | Output |
|---|---|
| `generate_report(jobs, …)` | Plain-text detailed report |
| `generate_table(jobs, …)` | Fixed-width ASCII table |
| `generate_csv(jobs, …)` | RFC 4180 CSV |
| `generate_html_table(jobs, …)` | Self-contained HTML with print/checkbox JS |

## Project folder structure (on save)

```
<ProjectName>/
  <ProjectName>.wirePlan   ← JSON (the source of truth)
  Drawings/                ← downloaded drawing files
  Relay Settings/          ← downloaded relay setting files
  CROW Outage/             ← CROW-related files
  Other/
```
