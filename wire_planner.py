#!/usr/bin/env python3
"""
Red-Line-Routing
----------------
All-in-one electrical job planner: work orders, drawings, relay settings, CROWs,
tailboards, safety documents.  Save/load plans as project folders with .redline
JSON and organised subfolders.  Export Wizard builds print-ready PDF packages,
hyperlinked HTML, and zip packages for tablets.

Application entry point.  All implementation lives in the redline/ package:

  redline/models.py          data factories, JOB_TYPE_SHORT
  redline/formatting.py      format_job(), text helpers, HTML colour theme
  redline/utils.py           file/URL/GUI utilities, combobox helpers
  redline/db.py              _AppDB (~/.redlinerouting.db), _GlobalDrawingCache
  redline/net.py             HTTP/cookie helpers (Windows DPAPI, NTLM)
  redline/html_export.py     Export Wizard HTML generators, _build_tablet_zip
  redline/pdf_export.py      _SimplePDFBuilder, _PDFPage, _build_print_pdf
  redline/job_dialogs.py     DrawingAwareFrame, JobDialog, DrawingEditDialog, …
  redline/export_wizard.py   ExportWizard, CrowDialog
  redline/registry_dialogs.py RelaySettingDialog, StandardsLibraryDialog, …
  redline/pts_dialogs.py     _PTSImportDialog, _PTSCompletionDialog
  redline/search_dialogs.py  DrawingSearchDialog, _BrowserCookieDialog, …
  redline/startup_dialogs.py SoftwareSetupDialog, LandingDialog, ProjectWizard
  redline/app.py             RedLineApp (main tk.Tk window)

Companion packages (must stay next to this file):
  drawing_search/   corporate drawing search client (parser, cache, HTTP)
  pypdf/            vendored pypdf with local patches — see CLAUDE.md
"""

import os
import sys

# Ensure this directory is on sys.path so sibling packages
# (drawing_search/, pypdf/) are importable regardless of cwd.
_app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

from redline.app import RedLineApp  # noqa: E402

if __name__ == "__main__":
    app = RedLineApp()
    app.mainloop()
