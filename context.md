# Development Context & History

This document chronicles the major bug fixes, architectural upgrades, edge cases discovered, and logic improvements implemented during the development of GST_M.

## 1. Dynamic YAML Configuration System
- **The Initial Problem**: Hardcoded replacement values were brittle.
- **The Fix**: Introduced `pdf_config.yaml` to securely read original and target replacement values.
- **Gotchas**: When the user configures replacement strings in the UI, we must preserve exact indentation and line breaks, especially for multiline addresses. The python `yaml` dump process uses block scalars (`|`) for multiline strings.

## 2. Robust IPC (Inter-Process Communication) and Frontend Controls
- **The Initial Problem**: Using `CANCEL` or `KeyboardInterrupt` indiscriminately wiped out directories, or left the browser hanging.
- **The Fix**:
  - Created `ipc_state.json` written by the `/state` FastAPI endpoint.
  - Added a **Pause / Resume** feature which loops Playwright passively without crashing.
  - Separated termination into two distinct buttons: **Stop** (graceful stop, retains files) and **Start Over** (wipes folders for a clean restart).
- **Edge Cases**: Closing the browser instance from an interrupt required special care; added a `dl.browser.close()` fallback to the `finally` block so the Chrome instance isn't permanently orphaned.

## 3. PDF Modifier — Per-Line Redaction (v3 Overhaul)
- **The Initial Problem (V1)**: Word-by-word modification failed because the GST portal generates fragmented bounding boxes.
- **The V2 Approach**: Block-level redaction dropped massive white rectangles over entire sections. This caused 5x file bloat (62KB → 305KB), visible white patches on zoom/print, and fragile absolute positioning.
- **The V3 Fix (Current)**:
  - Merged V2's robust GSTIN anchor detection (grouping fragmented text hits) with V1's per-line redaction approach.
  - Each text line is redacted individually with tight-fit rectangles (minimal padding), preserving table borders and cell backgrounds.
  - Replacement text is inserted at the exact original line coordinates.
  - File sizes are now ~136% of original (vs ~488% with V2).
  - Protected PDFs (encrypted, empty, corrupt) are gracefully skipped with clear error messages.
  - Uses `garbage=4, deflate=True` on save for optimal file size.

## 4. Producer/Consumer Pipeline Architecture (v2 Overhaul)
- **The Initial Problem**: A single `ThreadPoolExecutor` with a download callback was a bottleneck — no state tracking, no deduplication, no recovery.
- **The Fix**:
  - Built `pipeline.py` with 5 coordinated workers:
    - **Downloader** → saves to `staging/`
    - **Backup Worker** → copies to `downloads/` (permanent originals)
    - **2 Modifier Workers** → process PDFs in parallel, save to `processed/`
    - **State Manager** → tracks per-file state via atomic transitions
  - Per-file state machine: `STAGED → BACKING_UP → BACKED_UP → MODIFYING → COMPLETED/FAILED`
  - `threading.Lock` guards all state transitions — no race conditions or duplicate processing
  - Retry logic with configurable max attempts; permanently failed files are logged
  - Orphan recovery: on startup, scans staging for files from crashed runs
  - Periodic state persistence to `pipeline_state.json` for crash recovery

## 5. Folder Naming Fix
- **The Initial Problem**: `downloads/` contained originals and `backups/` contained modified PDFs — the exact opposite of what the names implied.
- **The Fix**:
  - Introduced clear, purpose-driven folder names:
    - `staging/` — temporary download landing zone (cleaned after processing)
    - `downloads/` — permanent backup of unmodified originals
    - `processed/` — final modified output
    - `uploads/` — uploaded Excel files (renamed from `input/`)
  - Updated all references across `config.py`, `main.py`, `app.py`, `pdf_config.yaml`, and frontend

## 6. Client UX — Batch Naming
- **The Initial Problem**: Output folders were named after the Excel file stem, which was non-descriptive.
- **The Fix**:
  - Added a batch name prompt modal in the UI
  - When the user uploads an Excel file and clicks "Start Processing", they're asked to name the batch (e.g., "June_2026_Invoices")
  - This name becomes the subfolder under `downloads/` and `processed/`
  - Default suggestion is `{Month}_{Year}` based on current date

## 7. UI Progress & Log Parsing
- **The Initial Problem**: Progress bar didn't increment for skipped rows, and log messages from the new pipeline workers weren't recognized.
- **The Fix**: Updated `script.js` to parse `[ModifierWorker-N] Completed:`, `[BackupWorker]`, and `[Pipeline]` log messages. Added batch name display in the dashboard header.

## 8. Bug Fixes
- Fixed font file reference (`Helvetica-Medium.ttf` → `helvmn.ttf` — the actual file in the project)
- Fixed blocking `input()` call in `main.py` that hung when run as a subprocess from `app.py`
- Initialized `current_job_filename` and `current_batch_name` as module-level globals
- Added timeout-based exit to SSE `event_generator` to prevent infinite loops
- Fixed broken markdown in `CLIENT_INSTALL_GUIDE.md`
