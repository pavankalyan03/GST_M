"""
Processing Pipeline — Producer/Consumer Architecture
======================================================

Implements a 5-worker distributed processing pipeline:

  Worker 1 (Downloader)  → Saves PDFs to staging/
  Worker 2 (Backup)      → Copies staging file to downloads/ (originals backup)
  Workers 3-4 (Modifiers)→ Modify PDFs and save to processed/
  Worker 5 (State Mgr)   → Tracks state, detects failures, handles retries

All coordination is done via thread-safe queues and a shared state dict
protected by a threading.Lock.
"""

import json
import shutil
import time
import threading
import logging
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable

from gst_downloader import config
from gst_downloader.processing.pdf_modifier import modify_single_pdf, _load_config
from gst_downloader.core.state import StateEmitter
from gst_downloader.utils.helpers import update_failed_irn_log


# ════════════════════════════════════════════════════════════════
#  STATE DEFINITIONS
# ════════════════════════════════════════════════════════════════

class FileState(str, Enum):
    """Processing state for each PDF file."""
    STAGED = "STAGED"                       # Downloaded to staging/
    BACKING_UP = "BACKING_UP"               # Being copied to downloads/
    BACKED_UP = "BACKED_UP"                 # Backup complete, ready for modification
    MODIFYING = "MODIFYING"                 # Being modified by a worker
    COMPLETED = "COMPLETED"                 # Successfully modified and saved
    FAILED = "FAILED"                       # Modification failed
    RETRY = "RETRY"                         # Queued for retry
    PERMANENTLY_FAILED = "PERMANENTLY_FAILED"  # Exhausted all retry attempts


@dataclass
class FileRecord:
    """Tracks the processing state of a single PDF."""
    filename: str
    staging_path: str
    backup_path: str = ""
    output_path: str = ""
    state: str = FileState.STAGED
    irn: str = ""
    attempts: int = 0
    last_error: str = ""
    created_at: str = ""
    completed_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


# ════════════════════════════════════════════════════════════════
#  PIPELINE STATE MANAGER (Worker 5)
# ════════════════════════════════════════════════════════════════

class PipelineStateManager:
    """
    Thread-safe state manager that tracks every PDF through the pipeline.
    Coordinates work dispatch and handles retries.
    """

    def __init__(self, staging_dir: Path, originals_dir: Path, processed_dir: Path,
                 logger: logging.Logger, max_retries: int = 3, failed_log_path: str = None):
        self._lock = threading.Lock()
        self._records: dict[str, FileRecord] = {}  # filename -> FileRecord
        self._logger = logger

        self.staging_dir = staging_dir
        self.originals_dir = originals_dir
        self.processed_dir = processed_dir
        self.max_retries = max_retries
        self.failed_log_path = failed_log_path

        # Ensure directories exist
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def register_file(self, filename: str, staging_path: str, irn: str = "") -> bool:
        """Register a newly downloaded file. Returns False if already registered."""
        with self._lock:
            if filename in self._records:
                existing = self._records[filename]
                if existing.state in (FileState.COMPLETED, FileState.MODIFYING, FileState.BACKING_UP):
                    self._logger.info(f"[StateManager] Skipping duplicate: {filename} (state={existing.state})")
                    return False
            self._records[filename] = FileRecord(
                filename=filename,
                staging_path=staging_path,
                backup_path=str(self.originals_dir / filename),
                output_path=str(self.processed_dir / filename),
                irn=irn
            )
            self._logger.info(f"[StateManager] Registered: {filename}")
            return True

    def transition(self, filename: str, new_state: FileState, error: str = "") -> bool:
        """Atomically transition a file to a new state."""
        with self._lock:
            if filename not in self._records:
                return False
            rec = self._records[filename]
            old_state = rec.state
            rec.state = new_state
            if error:
                rec.last_error = error
            if new_state == FileState.COMPLETED:
                rec.completed_at = datetime.now().isoformat()
            if new_state in (FileState.MODIFYING, FileState.RETRY):
                rec.attempts += 1
            self._logger.debug(f"[StateManager] {filename}: {old_state} -> {new_state}")
            return True

    def get_files_in_state(self, state: FileState) -> list[FileRecord]:
        """Get all files currently in the given state."""
        with self._lock:
            return [rec for rec in self._records.values() if rec.state == state]

    def claim_for_backup(self) -> FileRecord | None:
        """Atomically claim a STAGED file for backup."""
        with self._lock:
            for rec in self._records.values():
                if rec.state == FileState.STAGED:
                    rec.state = FileState.BACKING_UP
                    return rec
            return None

    def claim_for_modification(self) -> FileRecord | None:
        """Atomically claim a BACKED_UP or RETRY file for modification."""
        with self._lock:
            for rec in self._records.values():
                if rec.state in (FileState.BACKED_UP, FileState.RETRY):
                    if rec.attempts >= self.max_retries:
                        rec.state = FileState.PERMANENTLY_FAILED
                        self._logger.warning(
                            f"[StateManager] {rec.filename} permanently failed after "
                            f"{rec.attempts} attempts: {rec.last_error}"
                        )
                        if self.failed_log_path and rec.irn:
                            update_failed_irn_log(self.failed_log_path, 'add', rec.irn)
                        continue
                    rec.state = FileState.MODIFYING
                    rec.attempts += 1
                    return rec
            return None

    def get_summary(self) -> dict:
        """Return a summary of all file states."""
        with self._lock:
            summary = {}
            for rec in self._records.values():
                state = rec.state
                summary[state] = summary.get(state, 0) + 1
            summary["total"] = len(self._records)
            return summary

    def has_pending_work(self) -> bool:
        """Check if there are any files still being processed or waiting."""
        with self._lock:
            for rec in self._records.values():
                if rec.state in (FileState.STAGED, FileState.BACKING_UP,
                                 FileState.BACKED_UP, FileState.MODIFYING,
                                 FileState.RETRY):
                    return True
            return False

    def cleanup_staging(self, filename: str):
        """Remove a file from staging after successful backup + modification."""
        with self._lock:
            rec = self._records.get(filename)
            if rec and rec.state == FileState.COMPLETED:
                if self.failed_log_path and rec.irn:
                    update_failed_irn_log(self.failed_log_path, 'remove', rec.irn)
                    
                staging = Path(rec.staging_path)
                if staging.exists():
                    try:
                        staging.unlink()
                        self._logger.debug(f"[StateManager] Cleaned staging: {filename}")
                    except OSError as e:
                        self._logger.warning(f"[StateManager] Could not clean staging {filename}: {e}")

    def recover_orphans(self):
        """
        Scan staging directory for files not in the state tracker.
        Re-register them so they get processed.
        """
        if not self.staging_dir.exists():
            return
        for pdf_file in self.staging_dir.glob("*.pdf"):
            if pdf_file.name not in self._records:
                self._logger.info(f"[StateManager] Recovering orphan: {pdf_file.name}")
                self.register_file(pdf_file.name, str(pdf_file))

    def save_state(self, path: Path | str | None = None):
        """Persist state to disk for crash recovery."""
        if path is None:
            path = config.DATA_DIR / "pipeline_state.json"
        
        with self._lock:
            data = {
                filename: asdict(rec)
                for filename, rec in self._records.items()
            }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self._logger.warning(f"[StateManager] Failed to save state: {e}")

    def load_state(self, path: Path | str | None = None):
        """Load persisted state from disk."""
        if path is None:
            path = config.DATA_DIR / "pipeline_state.json"
            
        try:
            if not Path(path).exists():
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                for filename, rec_data in data.items():
                    self._records[filename] = FileRecord(**rec_data)
                self._logger.info(f"[StateManager] Loaded {len(self._records)} records from {path}")
        except Exception as e:
            self._logger.warning(f"[StateManager] Failed to load state: {e}")


# ════════════════════════════════════════════════════════════════
#  BACKUP WORKER (Worker 2)
# ════════════════════════════════════════════════════════════════

class BackupWorker:
    """
    Dedicated thread that copies staged files to the originals (downloads) folder.
    Runs continuously, checking for new work every 0.5 seconds.
    """

    def __init__(self, state_manager: PipelineStateManager, logger: logging.Logger, skip_modification: bool = False):
        self.state = state_manager
        self.log = logger
        self.skip_modification = skip_modification
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="BackupWorker", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def join(self, timeout=None):
        self._thread.join(timeout=timeout)

    def _run(self):
        self.log.info("[BackupWorker] Started")
        while not self._stop_event.is_set():
            rec = self.state.claim_for_backup()
            if rec is None:
                # No work available, wait briefly
                self._stop_event.wait(0.5)
                continue

            self._backup_file(rec)

        # Drain remaining work before exiting
        while True:
            rec = self.state.claim_for_backup()
            if rec is None:
                break
            self._backup_file(rec)

        self.log.info("[BackupWorker] Stopped")

    def _backup_file(self, rec: FileRecord):
        """Copy a staged file to the originals directory."""
        src = Path(rec.staging_path)
        dst = Path(rec.backup_path)

        for attempt in range(3):
            try:
                if not src.exists():
                    self.log.warning(f"[BackupWorker] Source missing: {src.name}")
                    self.state.transition(rec.filename, FileState.FAILED,
                                          f"Staging file missing: {src}")
                    return

                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                if self.skip_modification:
                    self.state.transition(rec.filename, FileState.COMPLETED)
                    self.state.cleanup_staging(rec.filename)
                    self.log.info(f"[BackupWorker] Finished (Mod skipped): {rec.filename}")
                    StateEmitter.emit("MODIFIER_SUCCESS", {"filename": rec.filename})
                else:
                    self.state.transition(rec.filename, FileState.BACKED_UP)
                    self.log.info(f"[BackupWorker] Backed up: {rec.filename}")
                return

            except (OSError, IOError) as e:
                self.log.warning(f"[BackupWorker] Copy failed (attempt {attempt+1}/3): {e}")
                time.sleep(0.5)

        # All retries exhausted
        self.state.transition(rec.filename, FileState.FAILED, f"Backup failed after 3 attempts")
        self.log.error(f"[BackupWorker] Failed to backup: {rec.filename}")


# ════════════════════════════════════════════════════════════════
#  MODIFIER WORKER (Workers 3 & 4)
# ════════════════════════════════════════════════════════════════

class ModifierWorker:
    """
    Worker thread that picks up backed-up PDFs and modifies them.
    Multiple instances can run in parallel for throughput.
    """

    def __init__(self, worker_id: int, state_manager: PipelineStateManager,
                 modifier_config: dict, logger: logging.Logger):
        self.worker_id = worker_id
        self.state = state_manager
        self.modifier_config = modifier_config
        self.log = logger
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"ModifierWorker-{worker_id}", daemon=True
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def join(self, timeout=None):
        self._thread.join(timeout=timeout)

    def _run(self):
        self.log.info(f"[ModifierWorker-{self.worker_id}] Started")
        while not self._stop_event.is_set():
            rec = self.state.claim_for_modification()
            if rec is None:
                self._stop_event.wait(0.5)
                continue

            self._modify_file(rec)

        # Drain remaining work before exiting
        while True:
            rec = self.state.claim_for_modification()
            if rec is None:
                break
            self._modify_file(rec)

        self.log.info(f"[ModifierWorker-{self.worker_id}] Stopped")

    def _modify_file(self, rec: FileRecord):
        """Modify a single PDF file."""
        try:
            in_path = rec.backup_path  # Read from originals (backup copy)
            out_path = rec.output_path
            
            StateEmitter.emit("MODIFIER_STATUS", {"status": "processing", "current": rec.filename})

            if not Path(in_path).exists():
                # Fall back to staging path if backup hasn't arrived yet
                in_path = rec.staging_path
                if not Path(in_path).exists():
                    self.state.transition(rec.filename, FileState.FAILED,
                                          "Source file not found for modification")
                    self.log.error(f"[ModifierWorker-{self.worker_id}] Source not found: {rec.filename}")
                    StateEmitter.emit("MODIFIER_FAIL", {"filename": rec.filename, "error": "Source not found"})
                    return

            self.log.info(f"[ModifierWorker-{self.worker_id}] Modifying: {rec.filename}")
            result = modify_single_pdf(in_path, out_path, self.modifier_config)

            if result["status"] == "ok":
                self.state.transition(rec.filename, FileState.COMPLETED)
                self.state.cleanup_staging(rec.filename)
                self.log.info(f"[ModifierWorker-{self.worker_id}] Completed: {rec.filename}")
                StateEmitter.emit("MODIFIER_SUCCESS", {"filename": rec.filename})
            elif result["status"] == "skipped":
                # No changes needed — still mark as completed
                self.state.transition(rec.filename, FileState.COMPLETED)
                self.state.cleanup_staging(rec.filename)
                self.log.info(f"[ModifierWorker-{self.worker_id}] Skipped (no changes): {rec.filename}")
                StateEmitter.emit("MODIFIER_SUCCESS", {"filename": rec.filename})
            else:
                error_msg = "; ".join(result["errors"][:2])  # Keep error concise
                self.state.transition(rec.filename, FileState.RETRY, error_msg)
                self.log.warning(
                    f"[ModifierWorker-{self.worker_id}] Failed: {rec.filename} — {error_msg[:100]}"
                )
                StateEmitter.emit("MODIFIER_FAIL", {"filename": rec.filename, "error": error_msg})

        except Exception as e:
            self.state.transition(rec.filename, FileState.RETRY, str(e))
            self.log.error(f"[ModifierWorker-{self.worker_id}] Exception: {rec.filename} — {e}")
            StateEmitter.emit("MODIFIER_FAIL", {"filename": rec.filename, "error": str(e)})
        finally:
            StateEmitter.emit("MODIFIER_STATUS", {"status": "idle"})


# ════════════════════════════════════════════════════════════════
#  PROCESSING PIPELINE (Orchestrator)
# ════════════════════════════════════════════════════════════════

class ProcessingPipeline:
    """
    Orchestrates the full processing pipeline:
      - State Manager tracks all files
      - Backup Worker copies originals
      - Modifier Workers process PDFs in parallel
    """

    def __init__(self, batch_name: str, config_path: str, logger: logging.Logger,
                 num_modifier_workers: int = None, failed_log_path: str = None,
                 skip_modification: bool = False):
        self.batch_name = batch_name
        self.config_path = config_path
        self.log = logger
        self.skip_modification = skip_modification

        if num_modifier_workers is None:
            import os
            # Set to half of the CPU cores (max 10, min 2) to maximize throughput without hogging system
            num_modifier_workers = min(10, max(2, (os.cpu_count() or 4) // 2))

        # Load modifier config
        self.modifier_config = _load_config(config_path)

        # Compute batch-specific directories
        staging_dir = Path(config.STAGING_DIR) / batch_name
        originals_dir = Path(self.modifier_config["original_folder"]) / batch_name
        processed_dir = Path(self.modifier_config["processed_folder"]) / batch_name

        # Initialize state manager
        self.state_manager = PipelineStateManager(
            staging_dir=staging_dir,
            originals_dir=originals_dir,
            processed_dir=processed_dir,
            logger=logger,
            max_retries=config.MAX_RETRY_ATTEMPTS,
            failed_log_path=failed_log_path
        )

        # Store dirs for external access
        self.staging_dir = staging_dir
        self.originals_dir = originals_dir
        self.processed_dir = processed_dir

        # Create workers
        self.backup_worker = BackupWorker(self.state_manager, logger, skip_modification=self.skip_modification)
        if not self.skip_modification:
            self.modifier_workers = [
                ModifierWorker(i + 1, self.state_manager, self.modifier_config, logger)
                for i in range(num_modifier_workers)
            ]
        else:
            self.modifier_workers = []

        self._state_saver_stop = threading.Event()
        self._state_saver_thread = None

    def start(self):
        """Start all worker threads."""
        self.log.info(f"[Pipeline] Starting pipeline for batch '{self.batch_name}'")
        self.log.info(f"[Pipeline] Staging:   {self.staging_dir}")
        self.log.info(f"[Pipeline] Originals: {self.originals_dir}")
        self.log.info(f"[Pipeline] Processed: {self.processed_dir}")

        # Recover any orphaned files from a previous crashed run
        self.state_manager.recover_orphans()

        # Start workers
        self.backup_worker.start()
        for worker in self.modifier_workers:
            worker.start()

        # Start periodic state saver
        self._state_saver_thread = threading.Thread(
            target=self._periodic_state_save, name="StateSaver", daemon=True
        )
        self._state_saver_thread.start()

        self.log.info(f"[Pipeline] All workers started ({len(self.modifier_workers)} modifier workers)")

    def notify_download(self, file_path: str, irn: str = ""):
        """
        Called by the downloader when a new PDF has been saved to staging.
        Registers the file with the state manager for processing.
        """
        filename = Path(file_path).name
        self.state_manager.register_file(filename, file_path, irn)

    def shutdown(self, wait: bool = True, timeout: float = 60.0):
        """
        Gracefully stop all workers.
        If wait=True, blocks until all current work is finished.
        """
        self.log.info("[Pipeline] Initiating shutdown...")

        # Stop the state saver first
        self._state_saver_stop.set()

        # Signal all workers to stop
        self.backup_worker.stop()
        for worker in self.modifier_workers:
            worker.stop()

        if wait:
            # Wait for backup worker to finish current work
            self.backup_worker.join(timeout=timeout)

            # Wait for modifier workers
            per_worker_timeout = timeout / max(len(self.modifier_workers), 1)
            for worker in self.modifier_workers:
                worker.join(timeout=per_worker_timeout)

        # Save final state
        self.state_manager.save_state()

        # Print summary
        summary = self.state_manager.get_summary()
        self.log.info(f"[Pipeline] Final state: {summary}")
        self.log.info("[Pipeline] Shutdown complete")

    def get_stats(self) -> dict:
        """Get current processing statistics."""
        return self.state_manager.get_summary()

    def is_output_ready(self, filename: str) -> bool:
        """Check if a specific file has been fully processed."""
        records = self.state_manager.get_files_in_state(FileState.COMPLETED)
        return any(r.filename == filename for r in records)

    def _periodic_state_save(self):
        """Periodically save pipeline state to disk for crash recovery."""
        while not self._state_saver_stop.is_set():
            self._state_saver_stop.wait(30)  # Save every 30 seconds
            if not self._state_saver_stop.is_set():
                self.state_manager.save_state()
