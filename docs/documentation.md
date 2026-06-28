# GST Invoice Automation & Modifier (GST_M)

## Overview
GST_M is an automated bulk processing system built for chartered accountants, businesses, and bookkeeping professionals who need to systematically download and modify large volumes of e-Invoices from the GST portal.

It solves two key problems:
1. **Bulk Downloading**: Instead of manually searching and downloading hundreds of IRNs, the Playwright-powered bot sequentially downloads them in the background.
2. **Automated Modification**: Many suppliers make typographical errors or formatting mistakes in the "Party Details" section of their GST invoices. This tool uses a precise PDF redaction engine to cleanly replace these sections with correctly formatted name and address data.

## Architecture

The project consists of four main components:

### 1. The Frontend UI (`app.py`, `static/`)
A lightweight FastAPI server serves a dynamic HTML/JS dashboard. It provides:
- A drag-and-drop interface for uploading the input Excel file.
- A **batch naming prompt** that asks the client to name the processing run (e.g., "June_2026_Invoices") — this name is used for all output folders.
- Real-time progress via Server-Sent Events (SSE) from the processing pipeline.
- Process controls: **Pause**, **Resume**, **Stop** (graceful exit preserving progress), and **Start Over** (wiping directories to start fresh).
- A Settings tab that reads/writes directly to `pdf_config.yaml` to allow non-technical users to define replacement text dynamically.

### 2. The Playwright Downloader (`gst_downloader/core.py`)
This engine automates the Chrome browser to navigate the GST e-Invoice portal.
- **Manual Login Hand-off**: Pauses the script, prompts the user via a UI modal, and allows them to solve the captcha and log in manually.
- **Intelligent Iteration**: Reads IRNs, Invoice Numbers, and Dates from the Excel sheet, searches the portal, and downloads each PDF.
- **Graceful Resumption**: If the script is stopped, you can resume from a specific row the next day.

### 3. The PDF Modifier (`gst_downloader/pdf_modifier.py`)
Cleanly modifies GST Invoice PDFs without white overlays or visual artifacts.
- **Per-Line Redaction**: Redacts individual text lines (name, address) with tight-fit rectangles rather than block-level white-outs. This preserves table borders, grid lines, and keeps file sizes within ~136% of the original.
- **GSTIN Anchor Detection**: Locates the three GSTIN occurrences (header, recipient, ship-to) and uses them as positional anchors for modification.
- **4-Phase Processing**: (1) Extract text + rects, (2) Add per-line redaction annotations, (3) Apply all redactions, (4) Insert replacement text.

### 4. The Processing Pipeline (`gst_downloader/pipeline.py`)
A producer/consumer architecture with 5 coordinated workers:
- **Worker 1 (Downloader)**: Downloads PDFs to `staging/`
- **Worker 2 (Backup)**: Copies staging files to `downloads/` (permanent originals backup)
- **Workers 3-4 (Modifiers)**: Two parallel workers that modify PDFs and save to `processed/`
- **Worker 5 (State Manager)**: Tracks per-file processing state, handles retries, detects failures, cleans staging

All synchronization uses `threading.Lock` with atomic state transitions to prevent race conditions and duplicate processing.

## Folder Structure

```
GST_M/
├── uploads/          ← Uploaded Excel files
├── staging/          ← Temporary download landing zone (auto-cleaned)
│   └── {batch}/
├── downloads/        ← Permanent backup of original, unmodified PDFs
│   └── {batch}/
├── processed/        ← Final output: modified PDFs
│   └── {batch}/
├── logs/             ← Timestamped log files
├── gst_downloader/   ← Core Python modules
├── static/           ← Frontend HTML/CSS/JS
└── pdf_config.yaml   ← PDF modification settings
```

## Usage
1. Run `python app.py`.
2. Open `http://localhost:8080`.
3. Configure replacement text in the **Settings** tab (if needed).
4. Upload your Excel file.
5. Give the batch a meaningful name (e.g., "June_2026_Invoices").
6. Click **Proceed** and log in to the GST portal when prompted.
7. Find processed invoices in `processed/{batch_name}/`.
