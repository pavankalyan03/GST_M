"""
GST Invoice Automation — Web Server
=====================================

FastAPI server providing:
  - File upload endpoint with batch naming
  - Real-time progress via Server-Sent Events (SSE)
  - Pipeline state control (pause/resume/stop/cancel)
  - PDF config management
"""

import os
import sys
import subprocess
import webbrowser
from pathlib import Path
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import queue
import threading
import json
from ruamel.yaml import YAML
import shutil
import zipfile
import tempfile

from gst_downloader import config

app = FastAPI(title="GST Invoice Automation")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global State ─────────────────────────────────────────────
log_queue = queue.Queue()
current_job_filename = ""
current_batch_name = ""

# Ensure directories exist
Path(config.UPLOADS_DIR).mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


# ════════════════════════════════════════════════════════════════
#  PIPELINE EXECUTION
# ════════════════════════════════════════════════════════════════

def run_pipeline_thread(filepath: str, start_from: str = "1", batch_name: str = ""):
    """Run the main.py pipeline as a subprocess, streaming logs to the queue."""
    cmd = [
        sys.executable, "-u", "main.py",
        "--raw-excel", filepath,
        "--start-from", start_from,
    ]
    if batch_name:
        cmd.extend(["--batch-name", batch_name])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )

    for line in iter(process.stdout.readline, ''):
        if line:
            log_queue.put(line)

    process.stdout.close()
    return_code = process.wait()

    # Read state to check if cancelled or stopped
    try:
        with open("ipc_state.json", "r") as f:
            state = json.load(f).get("status", "")
    except Exception:
        state = ""

    if state == "CANCEL":
        log_queue.put("\n[SYSTEM] Automation cancelled by user.\n")
    elif state == "STOP":
        log_queue.put("\n[SYSTEM] Automation stopped by user.\n")
    elif return_code == 0:
        log_queue.put("\n[SYSTEM] Automation completed successfully!\n")
    else:
        log_queue.put(f"\n[SYSTEM] Automation finished with error code {return_code}\n")


# ════════════════════════════════════════════════════════════════
#  FILE UPLOAD & PROCESSING
# ════════════════════════════════════════════════════════════════

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), start_from: int = 1, batch_name: str = ""):
    """Upload an Excel or ZIP file and start the processing pipeline."""
    is_zip = file.filename.endswith('.zip')
    if not (file.filename.endswith('.xlsx') or is_zip):
        return {"status": "error", "message": "Only .xlsx and .zip files are supported."}

    temp_save_path = Path(config.UPLOADS_DIR) / file.filename
    with open(temp_save_path, "wb") as f:
        f.write(await file.read())
        
    excel_file_path = temp_save_path
    
    if is_zip:
        extract_dir = Path(config.UPLOADS_DIR) / f"extracted_{Path(file.filename).stem}"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(temp_save_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Find all .xlsx files (excluding hidden files/folders like __MACOSX)
            xlsx_files = [f for f in extract_dir.rglob("*.xlsx") if not f.name.startswith('.')]
            
            if len(xlsx_files) == 0:
                shutil.rmtree(extract_dir, ignore_errors=True)
                temp_save_path.unlink(missing_ok=True)
                return {"status": "error", "message": "No .xlsx file found in the ZIP archive."}
            elif len(xlsx_files) > 1:
                shutil.rmtree(extract_dir, ignore_errors=True)
                temp_save_path.unlink(missing_ok=True)
                return {"status": "error", "message": "Multiple .xlsx files found in the ZIP archive. Please include only one."}
                
            # Move the single excel file to UPLOADS_DIR and use it
            excel_file_path = Path(config.UPLOADS_DIR) / xlsx_files[0].name
            shutil.move(str(xlsx_files[0]), str(excel_file_path))
            
            # Clean up the zip and extraction dir
            shutil.rmtree(extract_dir, ignore_errors=True)
            temp_save_path.unlink(missing_ok=True)
            
        except zipfile.BadZipFile:
            temp_save_path.unlink(missing_ok=True)
            return {"status": "error", "message": "Invalid ZIP file."}
        except Exception as e:
            return {"status": "error", "message": f"Error processing ZIP: {str(e)}"}

    # Reset IPC state to RUN
    with open("ipc_state.json", "w") as f:
        json.dump({"status": "RUN"}, f)

    global current_job_filename, current_batch_name
    current_job_filename = excel_file_path.name
    current_batch_name = batch_name or excel_file_path.stem

    # Start process in background
    thread = threading.Thread(
        target=run_pipeline_thread,
        args=(str(excel_file_path), str(start_from), current_batch_name),
        daemon=True,
    )
    thread.start()

    return {
        "status": "success",
        "message": f"Started processing {excel_file_path.name}",
        "batch_name": current_batch_name,
    }


# ════════════════════════════════════════════════════════════════
#  PDF CONFIG MANAGEMENT
# ════════════════════════════════════════════════════════════════

@app.get("/config")
def get_config():
    """Read editable fields from pdf_config.yaml."""
    yaml = YAML()
    try:
        with open(config.PDF_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.load(f)
    except FileNotFoundError:
        return {"status": "error", "message": "Config file not found"}

    editable = {}
    for key, val in cfg.items():
        if isinstance(val, dict) and "new" in val:
            editable[key] = val["new"]
        elif isinstance(val, str):
            editable[key] = val
    return {"status": "success", "config": editable}


@app.post("/config")
async def update_config(new_config: dict):
    """Update editable fields in pdf_config.yaml."""
    yaml = YAML()
    with open(config.PDF_CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = yaml.load(f)

    for key, val in new_config.items():
        if val is None:
            val = ""
        if key in cfg:
            if isinstance(cfg[key], dict) and "new" in cfg[key]:
                cfg[key]["new"] = val
            elif isinstance(cfg[key], str):
                cfg[key] = val

    with open(config.PDF_CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)

    return {"status": "success"}


# ════════════════════════════════════════════════════════════════
#  PIPELINE STATE CONTROL
# ════════════════════════════════════════════════════════════════

@app.post("/state")
async def set_state(req: dict):
    """
    Control the pipeline state.
    Expects {"command": "PAUSE" | "RESUME" | "CANCEL" | "STOP" | "RUN" | "PROMPT_CONTINUE"}
    """
    cmd = req.get("command")
    if not cmd:
        return {"status": "error", "message": "Missing 'command' field"}

    with open("ipc_state.json", "w") as f:
        json.dump({"status": cmd}, f)

    if cmd == "CANCEL":
        _cleanup_batch_folders()

    return {"status": "success"}


def _cleanup_batch_folders():
    """Delete staging, originals, and processed folders for the current batch."""
    global current_batch_name
    if not current_batch_name:
        return

    folders_to_clean = [
        Path(config.STAGING_DIR) / current_batch_name,
        Path(config.ORIGINALS_DIR) / current_batch_name,
        Path(config.PROCESSED_DIR) / current_batch_name,
    ]

    for folder in folders_to_clean:
        if folder.exists():
            try:
                shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass


# ════════════════════════════════════════════════════════════════
#  SERVER-SENT EVENTS (SSE) LOG STREAM
# ════════════════════════════════════════════════════════════════

def event_generator():
    """Generator that yields SSE events from the log queue."""
    idle_count = 0
    max_idle = 300  # Stop after ~5 minutes of no activity

    while idle_count < max_idle:
        try:
            line = log_queue.get(timeout=1.0)
            idle_count = 0  # Reset on activity
            line = line.replace('\n', '')
            if line.strip():
                yield f"data: {line}\n\n"

                # Check for terminal events
                if any(keyword in line for keyword in [
                    "Automation completed successfully",
                    "Automation finished with error",
                    "Automation stopped by user",
                    "Automation cancelled by user",
                ]):
                    return
        except queue.Empty:
            idle_count += 1
            yield ": keep-alive\n\n"


@app.get("/progress")
async def get_progress():
    """SSE endpoint for real-time log streaming."""
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ════════════════════════════════════════════════════════════════
#  APP STARTUP
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = 8080

    def open_browser():
        webbrowser.open(f"http://localhost:{port}")

    threading.Timer(1.5, open_browser).start()
    uvicorn.run("app:app", host="0.0.0.0", port=port)
