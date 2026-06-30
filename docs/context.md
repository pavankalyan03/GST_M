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

## 9. Throughput Optimizations (Turbo Mode)
- **The Initial Problem**: The automation was designed to simulate a slow human (taking ~4 seconds per invoice), resulting in massively slow processing times for large batches.
- **The Fix**: 
  - Eliminated artificial Playwright delays (`slow_mo=80`) and hardcoded human sleeps (`time.sleep()`).
  - Replaced manual text entry simulation with Playwright's instantaneous `.fill()` command.
  - Reduced pacing delays to near-zero and scaled PDF modifier threads dynamically based on the system's available CPU cores (`(os.cpu_count() or 4) // 2`).

## 10. Fast Resume & Processing Control
- **The Initial Problem**: Resuming a partially completed batch required the browser to painfully re-verify every single file by navigating the portal, taking hours for large datasets.
- **The Fix**: Implemented pre-scan logic in the downloader to read the output directory at startup, building a set of already-processed files for instant skipping (milliseconds per file). 
- Added a UI toggle allowing users to entirely skip the PDF modification step if they only want original downloads.

## 11. UI DOM Crash & Heartbeat Fixes
- **The Initial Problem**: The new "Turbo Mode" emitted dozens of logs per second to the UI. The UI terminal had no cap, leading to an Out-of-Memory DOM crash after ~10,000 logs. This crash stopped the Javascript heartbeat, causing the Python backend to forcefully kill the Playwright process. Additionally, minimizing the browser caused Chrome/Edge to throttle the Javascript heartbeat timer, triggering false-positive automation shutdowns.
- **The Fix**: 
  - Capped the UI terminal logs to a rolling window of 1,000 lines, completely fixing the DOM memory leak.
  - Replaced the Javascript timer-based heartbeat with **Server-Sent Events (SSE) socket connection tracking**. The backend natively monitors the active TCP socket used for the dashboard stream. When the socket drops (i.e., the user manually closes the tab), the backend waits for a 5-second grace period (to allow for refreshes) before gracefully shutting down the server and automation. This is 100% reliable and entirely bypasses browser background throttling.
