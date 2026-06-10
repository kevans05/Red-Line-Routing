# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
python3 wire_planner.py
```

No build step, no package install — the application uses only the Python standard library (`tkinter`, `json`, `os`, `threading`, `urllib`, `webbrowser`, `subprocess`) plus a **vendored copy of pypdf** in `pypdf/` (no pip install needed).

Syntax-check without launching the GUI:

```bash
python3 -c "import ast; ast.parse(open('wire_planner.py').read()); print('OK')"
```

## Repository layout

```
wire_planner.py   # main application (~8 300 lines)
drawing_search/   # drawing search client package (parser, cache, HTTP client)
pypdf/            # vendored pypdf 6.13.1 (patched — see below)
.gitignore        # ignores __pycache__ and *.pyc
```

No tests, no CI config, no requirements file.

### Vendored pypdf patches

The `pypdf/` folder is pypdf 6.13.1 with two local patches that must be preserved if the vendored copy is ever upgraded:

1. **`pypdf/_crypt_providers/__init__.py`** — `except ImportError` widened to `except BaseException` (a broken Rust `cryptography` extension raises `pyo3_runtime.PanicException`, which is a `BaseException`), plus stderr suppression around the import.
2. **`typing_extensions` fallbacks** — eight files (`_utils.py`, `types.py`, `_writer.py`, `_reader.py`, `generic/_base.py`, `generic/_data_structures.py`, `generic/_image_xobject.py`, `annotations/_markup_annotations.py`) wrap `from typing_extensions import …` in `try/except ImportError` falling back to `typing.Any`, so pypdf works without the `typing_extensions` package on Python < 3.11.

The same defensive pattern exists at the import site in `wire_planner.py`: the pypdf import is wrapped in `except BaseException`, stores the failure message in `_PYPDF_ERROR`, and sets `_PYPDF_AVAILABLE = False`. Export falls back to print-ready HTML with a warning dialog when pypdf can't load.

## Architecture — wire_planner.py layers

Reading top-to-bottom follows the dependency order:

| Lines (approx) | Layer |
|---|---|
| 1 – 215 | Module-level data factories and UI helpers |
| 219 – 1250 | Reusable widget classes and job/protection dialogs (`DrawingAwareFrame`, `EndpointFrame`, `JobDialog`, …) |
| 1250 – 1390 | Job-preview text formatting (`format_job`) and shared colour theme (`_ROW_STYLE`, `_ROW_BORDER`, `_esc`) |
| 1395 – 1895 | Export Wizard HTML generators (`_ew_*` functions, no GUI) |
| 1897 – 2740 | PDF package generation (`_SimplePDFBuilder`, `_PDFPage`, `_pdf_*` section builders, `_build_print_pdf`) |
| 2741 – 3150 | `ExportWizard` dialog |
| 3152 – 4310 | Registry dialogs, drawing search, download helpers |
| 4313 – 5250 | Startup flow dialogs and the project wizard |
| 5251 – end | `RedLineApp` — the main `tk.Tk` window |

## Key data model

`RedLineApp` owns these instance attributes; they are serialised together into the `.redline` JSON:

```python
self.jobs                           # list of job dicts (see empty_job())
self.drawing_registry               # {name: {title, rev, url, notes}}
self.relay_registry                 # {device_id: {title, revision, engineer, contact, url, …}}
self.maintenance_standards_registry # {standard_id: {title, revision, url_telecom, url_transmission, notes}}
self.engineering_standards_registry # {standard_id: {title, revision, standard_type, url, notes}}
self.title_page                     # {notes: str, crows: [{outage_number, url, files: [str]}]}
self.history                        # {device/location/pin/panel/wire: [str, …]}  – autocomplete pool
```

### Global database (`~/.redlinerouting.db`)

Everything global (not per-project) lives in a SQLite database wrapped by `_AppDB`. Connections are opened per call, so the one instance (`self._app_db`) is safe from background threads. Three tables:

| Table | Contents |
|---|---|
| `config` | App settings as JSON-encoded key/value rows — loaded into the in-memory dict `self.app_config`; `_save_app_config()` writes the whole dict back |
| `standards_library` | Cross-project standards library, keyed `(kind, standard_id)` where kind is `maintenance` or `engineering` |
| `drawing_cache` | Drawing search results shared by all projects, keyed `facility\|type\|subject\|state` |

Migration is automatic and one-way: on first run `_AppDB._migrate_legacy_json()` imports `~/.redlinerouting.json` (settings + standards library) if the config table is empty; opening an old `.redline` that contains a `drawing_search_cache` key folds it into the DB via `cache_merge_legacy()` (newer timestamps win). The cache is no longer written into `.redline` files.

Standards library flow: `_remember_standard()` captures entries as they're added/edited, `_remember_all_standards()` sweeps both registries on project open/save, and `StandardsLibraryDialog` ("📚 From Library" buttons) adds remembered standards to the current project. `_GlobalDrawingCache` keeps the old `_ProjectDrawingCache` interface (`get`/`put`/`is_cacheable`/`iter_keys`) but reads/writes the DB.

Drawing cache freshness: `_schedule_drawing_cache_refresh()` starts a timer in `__init__` that checks every 15 minutes (`_CACHE_CHECK_INTERVAL_MS`) and re-fetches entries older than 4 hours (`drawing_cache_refresh_hours` app setting overrides the default). Project open triggers the same stale-only refresh; the search dialog additionally revalidates in the background on every cache hit. A `_cache_refresh_running` flag prevents overlapping refresh runs.

Job types: `REMOVE`, `ADD`, `MOVE`, `BLOCK`, `UNBLOCK`, `TESTING`.  
`BLOCK`/`UNBLOCK` carry a `protection` sub-dict; `MOVE` carries both `start/end` and `add_start/add_end` endpoint pairs.

### JSON key names vs Python attribute names

One intentional mismatch exists for backwards compatibility with files saved by older versions:

| Python attribute | JSON key in `.redline` file |
|---|---|
| `self.relay_registry` | `"relay_settings"` |

All other registries use the same name in both Python and JSON.

## Drawing name convention

`XXXX-YZZ-IIII-N`  (Site — Type — Subject — Serial — Sheet)

`Y == 'H'` signals a horizontal drawing: the **Drawing Cell** field is enabled only for H-type drawings (`is_h_type_drawing()`). All other types disable that field automatically.

## Export — the Export Wizard

All exporting goes through `ExportWizard` (File → Export Wizard…, Ctrl+E, or the header-bar button). The old standalone report/table/CSV/HTML exports have been removed.

Three output formats, selectable in step 1:

| Format | Output |
|---|---|
| **PDF Package** | Single merged PDF (`Package_<date>.pdf`): cover page + TOC, colour-coded work-orders table, registry tables, then every downloaded PDF from the project subfolders appended via pypdf. Falls back to print-ready HTML when pypdf is unavailable. |
| **HTML / PDF (digital)** | Screen-optimised HTML with live hyperlinks. |
| **Tablet** | Zip package (`Tablet_<date>.zip`, built by `_build_tablet_zip`): `manifest.json` (format `redline-tablet-package` v1, section modes, file inventory) + `index.html` (large-text HTML with relative links) + `project.redline` (same JSON as a saved project) + the documents of every section set to "print" plus CROW attachments. Designed to be consumed by a future iOS reader app. |

Step 2 picks sections; each optional section (Drawings, Relay Settings, Maintenance Standards, Engineering Standards) has a three-state mode:

- **Skip** — not included
- **Print** — section table + downloaded documents included
- **TOC only** — listed on the cover page TOC as "printed separately" but no pages generated (for documents the user already has printed)

Step 3 picks a paper size per section (Letter Portrait/Landscape, 11×17 Portrait/Landscape). The size names use the Unicode `×` in UI strings; `_SimplePDFBuilder.SIZES` carries both `x` and `×` key variants — keep both when editing.

### PDF generation internals

`_SimplePDFBuilder` writes complete PDF 1.4 bytes from scratch (standard Type1 fonts, no dependencies): used for the cover, work-orders, and registry-table pages. `_PDFPage` uses top-left coordinates (converted internally to PDF's bottom-up system). Text fitting uses the Helvetica AFM width table `_HELV_W` (`_ptw`/`_ptrunc`).

`_build_print_pdf()` orchestrates: generated pages first, then `_collect_pdfs()` gathers documents from each project subfolder. `.txt` files are converted natively (`_txt_to_pdf_bytes`), `.docx`/`.doc` via LibreOffice headless or PowerShell + Word COM (`_docx_to_pdf_bytes`); files that can't be converted are reported in an info dialog after export. Everything is merged with `_merge_pdfs_bytes()` (pypdf).

## UI patterns to keep consistent

### `tk.Frame` vs `ttk.Frame`

Use `tk.Frame(bg=…)` whenever the frame needs a specific background colour (dark header, light content area). `ttk.Frame` ignores `bg` on most platforms.

### `side="bottom"` packing rule

In any `tk.Frame` / `ttk.Frame` that mixes `expand=True` content with footer widgets, **pack the footer first** (with `side="bottom"`) before packing the expanding centre. Violating this order makes the footer invisible.

### Startup dialog sizing

Call `_center_window(win)` (no w/h) to auto-size a dialog to its content. Only pass explicit dimensions for two-pane layouts like `ProjectWizard`.

### `_hover_btn(frame, normal_bg, hover_bg)`

Walks the full widget tree under `frame` and binds `<Enter>`/`<Leave>` to swap background colour. Use this on any `tk.Frame` acting as a clickable card. Remember to bind `<Button-1>` on **all** children including the accent strip, not just the content frame.

### Generic print helpers

`_print_selected(tree, subfolder, label)` and `_print_all(subfolder)` are the single implementation for all eight "Print Selected / Print All" toolbar buttons across the four registry tabs (Drawings, Relay Settings, Maintenance Standards, Engineering Standards). Each tab's named method is a one-line delegate.

### Opening generated files

`_open_file(path)` opens with the OS default handler; `_reveal_file(path)` opens the containing folder with the file selected (used for generated PDFs so they don't auto-open in a browser).

## Startup flow

```
RedLineApp.__init__
  └── after_idle(_startup_flow)
        ├── SoftwareSetupDialog  (first run only — settings stored in ~/.redlinerouting.db)
        ├── LandingDialog        (open existing / new quick / wizard)
        └── ProjectWizard        (optional 5-step wizard → _apply_wizard_result)
```

## Downloads

`_download_with_progress` handles both HTTP(S) and `file://` URLs. `_file_url_to_path()` converts `file://` URLs — including UNC network shares (`file://server/share/path` → `\\server\share\path` on Windows) — to local paths before any HTTP machinery runs.

## Project folder structure (on save)

```
<ProjectName>/
  <ProjectName>.redline        ← JSON (the source of truth)
  Drawings/                    ← downloaded drawing files
  Relay Settings/              ← downloaded relay setting files
  Maintenance Standards/       ← downloaded maintenance standard files
  Engineering Standards/       ← downloaded engineering standard files
  CROW Outage/                 ← CROW-related files + attached documents
  Tailboards/
    Completed/
  Other/
```
