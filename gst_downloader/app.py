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
from fastapi import FastAPI, UploadFile, File, Form
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
import psutil
import time
from fastapi.responses import FileResponse
from pydantic import BaseModel

from gst_downloader import config
from gst_downloader.processing.excel_preprocessor import preprocess_excel
from gst_downloader.processing.pdf_modifier import _load_config

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

active_subprocess = None

last_heartbeat = 0
heartbeat_active = False

global_pipeline_state = {
    "status": "IDLE",
    "metrics": {
        "total": 0,
        "processed": 0,
        "errors": 0
    },
    "workers": {
        "downloader": { "status": "idle", "current": None },
        "modifier": { "status": "idle", "current": None }
    },
    "queue": [],
    "errors": []
}

# Reset IPC state on server startup to avoid stale UI state
try:
    with open(config.DATA_DIR / "ipc_state.json", "w") as f:
        json.dump({"status": "IDLE"}, f)
except Exception:
    pass

def reset_global_state():
    global global_pipeline_state
    global_pipeline_state.update({
        "status": "IDLE",
        "metrics": {"total": 0, "processed": 0, "errors": 0},
        "workers": {
            "downloader": { "status": "idle", "current": None },
            "modifier": { "status": "idle", "current": None }
        },
        "queue": [],
        "errors": []
    })

# Ensure directories exist
Path(config.UPLOADS_DIR).mkdir(exist_ok=True, parents=True)
static_dir = Path(__file__).parent / "web" / "static"
static_dir.mkdir(exist_ok=True, parents=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open(static_dir / "index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/heartbeat")
async def heartbeat():
    """Receives ping from frontend to keep server alive."""
    global last_heartbeat, heartbeat_active
    last_heartbeat = time.time()
    heartbeat_active = True
    return {"status": "ok"}

def heartbeat_monitor():
    """Background thread that kills the server if the browser tab is closed."""
    global last_heartbeat, heartbeat_active, active_subprocess
    while True:
        time.sleep(1)
        if heartbeat_active and (time.time() - last_heartbeat > 5):
            print("\n[SYSTEM] Browser tab closed. Shutting down server...")
            if active_subprocess is not None:
                try:
                    parent = psutil.Process(active_subprocess.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                except Exception:
                    pass
            os._exit(0)


# ════════════════════════════════════════════════════════════════
#  PIPELINE EXECUTION
# ════════════════════════════════════════════════════════════════

def run_pipeline_thread(filepath: str, start_from: str = "1", batch_name: str = "", retry_only: bool = False):
    """Run the main.py pipeline as a subprocess, streaming logs to the queue."""
    global active_subprocess
    
    cmd = [
        sys.executable, "-u", str(Path(__file__).parent / "main.py"),
        "--cleaned-excel", filepath,
        "--start-from", start_from,
    ]
    if batch_name:
        cmd.extend(["--batch-name", batch_name])
    if retry_only:
        cmd.append("--retry-failed")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    active_subprocess = process

    for line in iter(process.stdout.readline, ''):
        if line:
            if line.startswith("__STATE_EVENT__:"):
                try:
                    payload = json.loads(line[len("__STATE_EVENT__:") :])
                    _handle_state_event(payload)
                except Exception as e:
                    print(f"Error parsing state event: {e}")
            else:
                log_queue.put(line)

    process.stdout.close()
    return_code = process.wait()
    active_subprocess = None

    # Read state to check if cancelled or stopped
    try:
        with open(config.DATA_DIR / "ipc_state.json", "r") as f:
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


def _handle_state_event(payload):
    global global_pipeline_state
    event_type = payload.get("type")
    data = payload.get("data", {})
    
    if event_type == "INIT_BATCH":
        global_pipeline_state["status"] = "INITIALIZING"
        global_pipeline_state["metrics"]["total"] = data.get("total_records", 0)
    elif event_type == "STATUS_UPDATE":
        global_pipeline_state["status"] = data.get("status", global_pipeline_state["status"])
    elif event_type == "QUEUE_UPDATE":
        global_pipeline_state["queue"] = data.get("queue", [])
    elif event_type == "DOWNLOADER_STATUS":
        global_pipeline_state["workers"]["downloader"]["status"] = data.get("status", "idle")
        if "current" in data:
            global_pipeline_state["workers"]["downloader"]["current"] = data.get("current")
    elif event_type == "DOWNLOADER_SUCCESS":
        global_pipeline_state["metrics"]["processed"] += 1
    elif event_type == "DOWNLOADER_FAIL":
        global_pipeline_state["metrics"]["errors"] += 1
        global_pipeline_state["errors"].append({
            "type": "DOWNLOAD",
            "irn": data.get("irn", ""),
            "message": data.get("error", "Failed")
        })
    elif event_type == "MODIFIER_STATUS":
        global_pipeline_state["workers"]["modifier"]["status"] = data.get("status", "idle")
        if "current" in data:
            global_pipeline_state["workers"]["modifier"]["current"] = data.get("current")
    elif event_type == "MODIFIER_FAIL":
        global_pipeline_state["errors"].append({
            "type": "MODIFIER",
            "file": data.get("filename", ""),
            "message": data.get("error", "Failed")
        })
    elif event_type == "PIPELINE_COMPLETE":
        global_pipeline_state["status"] = "COMPLETED"
    elif event_type == "PIPELINE_CANCELLED":
        global_pipeline_state["status"] = "CANCELLED"


# ════════════════════════════════════════════════════════════════
#  FILE UPLOAD & PROCESSING
# ════════════════════════════════════════════════════════════════

@app.post("/upload_and_preprocess")
async def upload_and_preprocess(file: UploadFile = File(...), batch_name: str = Form("")):
    """Upload an Excel or ZIP file, preprocess it, and pause at the checkpoint."""
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

    global current_job_filename, current_batch_name
    current_batch_name = batch_name or excel_file_path.stem
    current_job_filename = excel_file_path.name

    # Load config to get the processed_excel_folder
    config_path = config.PDF_CONFIG_FILE
    try:
        modifier_config = _load_config(config_path)
    except Exception as e:
        return {"status": "error", "message": f"Failed to load config: {e}"}
        
    processed_excel_folder = Path(modifier_config["processed_excel_folder"])
    processed_excel_folder.mkdir(parents=True, exist_ok=True)
    cleaned_excel_path = processed_excel_folder / f"{current_batch_name}.xlsx"

    # Run preprocessing
    try:
        valid_count = preprocess_excel(str(excel_file_path), str(cleaned_excel_path))
    except Exception as e:
        return {"status": "error", "message": f"Preprocessing failed: {e}"}

    # Set IPC state to PENDING_CONFIRMATION
    with open(config.DATA_DIR / "ipc_state.json", "w") as f:
        json.dump({"status": "PENDING_CONFIRMATION", "batch_name": current_batch_name, "cleaned_excel": str(cleaned_excel_path)}, f)

    # Check for existing failed IRNs
    failed_log_path = Path(modifier_config.get("processed_folder", "processed")) / current_batch_name / "failed_irns.txt"
    has_failed_irns = False
    if failed_log_path.exists():
        try:
            with open(failed_log_path, "r", encoding="utf-8") as f:
                if any(line.strip() for line in f):
                    has_failed_irns = True
        except Exception:
            pass

    return {
        "status": "success",
        "message": f"Preprocessed {excel_file_path.name}",
        "batch_name": current_batch_name,
        "valid_count": valid_count,
        "cleaned_excel": str(cleaned_excel_path),
        "has_failed_irns": has_failed_irns
    }

class StartAutomationRequest(BaseModel):
    cleaned_excel_path: str
    batch_name: str
    start_from: int = 1
    retry_only: bool = False

@app.post("/start_automation")
async def start_automation(req: StartAutomationRequest):
    """Start the actual pipeline with the preprocessed (and potentially user-edited) Excel file."""
    # Reset IPC state to RUN
    with open(config.DATA_DIR / "ipc_state.json", "w") as f:
        json.dump({"status": "RUN"}, f)
        
    reset_global_state()
    global_pipeline_state["status"] = "RUNNING"

    # Start process in background
    thread = threading.Thread(
        target=run_pipeline_thread,
        args=(req.cleaned_excel_path, str(req.start_from), req.batch_name, req.retry_only),
        daemon=True,
    )
    thread.start()

    return {
        "status": "success",
        "message": f"Automation started for {req.batch_name}"
    }

@app.post("/reset_job")
async def reset_job():
    """Reset the application state so a new file can be processed."""
    reset_global_state()
    try:
        with open(config.DATA_DIR / "ipc_state.json", "w") as f:
            json.dump({"status": "IDLE"}, f)
    except Exception:
        pass
    return {"status": "success"}

@app.get("/download_excel")
async def download_excel(filepath: str):
    """Serve the preprocessed Excel file for verification or editing."""
    path = Path(filepath)
    if not path.exists():
        return {"status": "error", "message": "File not found"}
    return FileResponse(path, filename=path.name)


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

@app.get("/api/history")
async def get_history():
    """Returns the job history from history.json."""
    try:
        with open(config.DATA_DIR / "history.json", "r") as f:
            return json.load(f)
    except Exception:
        return []

@app.get("/api/errors")
async def get_errors():
    """Returns the parsed errors from the global pipeline state."""
    global global_pipeline_state
    # Formatting to match the old expected format if needed by older clients
    formatted = []
    for err in global_pipeline_state.get("errors", []):
        formatted.append({
            "row": err.get("row", "-"),
            "invoice": err.get("file", err.get("irn", "-")),
            "irn": err.get("irn", "-"),
            "error": err.get("message", "Error")
        })
    return formatted


@app.post("/state")
async def set_state(req: dict):
    """
    Control the pipeline state.
    Expects {"command": "PAUSE" | "RESUME" | "CANCEL" | "STOP" | "RUN" | "PROMPT_CONTINUE"}
    """
    cmd = req.get("command")
    if not cmd:
        return {"status": "error", "message": "Missing 'command' field"}

    with open(config.DATA_DIR / "ipc_state.json", "w") as f:
        json.dump({"status": cmd}, f)

    if cmd in ["CANCEL", "STOP"]:
        global active_subprocess
        if active_subprocess is not None:
            def force_kill():
                time.sleep(3)
                if active_subprocess is not None and active_subprocess.poll() is None:
                    print(f"[SYSTEM] Forcing kill of stuck automation process...")
                    try:
                        parent = psutil.Process(active_subprocess.pid)
                        for child in parent.children(recursive=True):
                            child.kill()
                        parent.kill()
                    except Exception:
                        pass
            threading.Thread(target=force_kill, daemon=True).start()

    if cmd == "CANCEL":
        _cleanup_batch_folders()

    return {"status": "success"}


def _cleanup_batch_folders():
    """Delete staging, originals, and processed folders for the current batch."""
    global current_batch_name
    if not current_batch_name:
        return

    modifier_config = {}
    try:
        modifier_config = _load_config(config.PDF_CONFIG_FILE)
    except Exception:
        pass

    folders_to_clean = [
        Path(config.STAGING_DIR) / current_batch_name,
    ]
    
    if "original_folder" in modifier_config:
        folders_to_clean.append(Path(modifier_config["original_folder"]) / current_batch_name)
    if "processed_folder" in modifier_config:
        folders_to_clean.append(Path(modifier_config["processed_folder"]) / current_batch_name)

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


def dashboard_stream_generator():
    """Generator for unified dashboard SSE (logs, health, state)."""
    while True:
        # Collect any new logs
        new_logs = []
        try:
            while not log_queue.empty():
                line = log_queue.get_nowait().replace('\n', '')
                if line.strip():
                    new_logs.append(line)
        except queue.Empty:
            pass

        # Read pipeline state
        pipeline_state = global_pipeline_state
            
        # Read IPC state
        ipc_state = {"status": "IDLE"}
        try:
            if (config.DATA_DIR / "ipc_state.json").exists():
                with open(config.DATA_DIR / "ipc_state.json", "r") as f:
                    ipc_state = json.load(f)
        except Exception:
            pass

        # System health
        health = {
            "cpu_percent": psutil.cpu_percent(),
            "mem_percent": psutil.virtual_memory().percent
        }

        payload = {
            "logs": new_logs,
            "pipeline": pipeline_state,
            "ipc_status": ipc_state.get("status", "IDLE"),
            "health": health
        }

        yield f"data: {json.dumps(payload)}\n\n"
        
        # Sleep ~1 sec before next tick
        threading.Event().wait(1.0)


@app.get("/api/dashboard_stream")
async def get_dashboard_stream():
    """Unified SSE endpoint for dashboard updates."""
    return StreamingResponse(dashboard_stream_generator(), media_type="text/event-stream")


# ════════════════════════════════════════════════════════════════
#  APP STARTUP
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = 8080

    def open_browser():
        webbrowser.open(f"http://localhost:{port}")

    threading.Timer(1.5, open_browser).start()
    threading.Thread(target=heartbeat_monitor, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=port)
