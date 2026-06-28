"""
GST Invoice Automation — Main Entry Point
===========================================

Orchestrates the full pipeline:
  1. Preprocess Excel → filter relevant invoices
  2. Read IRN records
  3. Start the processing pipeline (backup + modification workers)
  4. Launch Playwright browser for downloads
  5. Graceful shutdown with state persistence
"""

import os
import sys
import argparse
from pathlib import Path

# Force UTF-8 output on Windows to avoid UnicodeEncodeError with special chars
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl is not installed. Run: pip install openpyxl")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("ERROR: playwright is not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

from gst_downloader import config
from gst_downloader.utils.logger import setup_logging
from gst_downloader.processing.excel_reader import read_irns_from_excel
from gst_downloader.core.downloader import GSTInvoiceDownloader
from gst_downloader.processing.excel_preprocessor import preprocess_excel
from gst_downloader.core.pipeline import ProcessingPipeline
from gst_downloader.processing.pdf_modifier import _load_config
from gst_downloader.core.state import StateEmitter


def parse_args():
    ap = argparse.ArgumentParser(
        description="GST e-Invoice Bulk Downloader — downloads and modifies invoices via Playwright",
    )
    ap.add_argument(
        "--cleaned-excel", required=True,
        help=f"Path to the preprocessed Excel file",
    )
    ap.add_argument(
        "--start-from", type=int, default=1, dest="start_from",
        help="Excel row number to start from (useful for resuming). Default: 1 (first data row)",
    )
    ap.add_argument(
        "--delay-min", type=float, default=config.MIN_DELAY_SEC,
        help=f"Minimum delay between searches in seconds (default: {config.MIN_DELAY_SEC})",
    )
    ap.add_argument(
        "--delay-max", type=float, default=config.MAX_DELAY_SEC,
        help=f"Maximum delay between searches in seconds (default: {config.MAX_DELAY_SEC})",
    )
    ap.add_argument(
        "--batch-name", default=None, dest="batch_name",
        help="Name for this processing batch (used as subfolder name). "
             "Defaults to the Excel file stem.",
    )
    ap.add_argument(
        "--retry-failed", action="store_true",
        help="Only process IRNs listed in the batch's failed_irns.txt file",
    )
    return ap.parse_args()


def main():
    args = parse_args()
    logger = setup_logging()

    logger.info("=" * 50)
    logger.info("  GST e-Invoice Bulk Downloader")
    logger.info("=" * 50)

    # Update global delay values if overridden via CLI
    config.MIN_DELAY_SEC = args.delay_min
    config.MAX_DELAY_SEC = args.delay_max

    # ── 0. Compute Dynamic Paths ──────────────────────────────
    config_path = config.PDF_CONFIG_FILE
    
    # Load config to get the processed_excel_folder
    try:
        modifier_config = _load_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)
        
    processed_excel_folder = Path(modifier_config["processed_excel_folder"])
    processed_excel_folder.mkdir(parents=True, exist_ok=True)

    cleaned_excel_path = Path(args.cleaned_excel)
    batch_name = args.batch_name or cleaned_excel_path.stem

    logger.info(f"Batch name: {batch_name}")

    if not cleaned_excel_path.exists():
        logger.error(f"Excel file not found: {cleaned_excel_path}")
        sys.exit(1)

    # ── 1. Read Excel ─────────────────────────────────────────
    records = read_irns_from_excel(str(cleaned_excel_path), logger)
    if not records:
        logger.error("No IRNs found — nothing to do.")
        sys.exit(1)
        
    failed_log_path = Path(modifier_config["processed_folder"]) / batch_name / "failed_irns.txt"
    
    if args.retry_failed:
        logger.info(f"Retry mode active. Looking for failed IRNs at {failed_log_path}")
        if not failed_log_path.exists():
            logger.info("No failed IRNs file found for this batch. Nothing to retry.")
            sys.exit(0)
            
        with open(failed_log_path, "r", encoding="utf-8") as f:
            failed_irns = set(line.strip() for line in f if line.strip())
            
        records = [r for r in records if r["irn"] in failed_irns]
        logger.info(f"Filtered to {len(records)} failed IRNs for retry.")
        
        if not records:
            logger.info("No matching failed IRNs found in the excel file.")
            sys.exit(0)
        
    StateEmitter.emit("INIT_BATCH", {"total_records": len(records)})

    # ── 2. Resolve --start-from ───────────────────────────────
    start_index = 0
    if args.start_from > 1:
        for idx, rec in enumerate(records):
            if rec["row"] >= args.start_from:
                start_index = idx
                break
        else:
            logger.warning(f"Row {args.start_from} not found -- starting from the beginning")

    if start_index > 0:
        logger.info(f"[UI_JUMP] {start_index}")
        logger.info(f"Resuming from row {records[start_index]['row']} (index {start_index})")

    # ── 3. Initialize Processing Pipeline ─────────────────────
    pipeline = ProcessingPipeline(
        batch_name=batch_name,
        config_path=config_path,
        logger=logger,
        failed_log_path=str(failed_log_path)
    )
    pipeline.start()

    def on_download(path_str, irn=""):
        """Callback fired when a PDF is downloaded to staging."""
        pipeline.notify_download(path_str, irn)

    # ── 4. Launch Playwright and run ──────────────────────────
    with sync_playwright() as pw:
        dl = GSTInvoiceDownloader(
            pw, logger,
            on_download_success=on_download,
            dl_dir=pipeline.staging_dir,
            out_dir=pipeline.processed_dir,
            failed_log_path=str(failed_log_path)
        )
        try:
            dl.launch_browser()
            dl.navigate_to_portal()
            dl.prompt_manual_login()
            dl.run(records, start_index=start_index)
        except KeyboardInterrupt:
            logger.info("\nInterrupted by user (Ctrl+C)")
            StateEmitter.emit("PIPELINE_CANCELLED")
            dl._print_summary(len(records))
        except Exception as exc:
            logger.error(f"Fatal error: {exc}", exc_info=True)
        finally:
            if hasattr(dl, 'browser') and dl.browser:
                try:
                    dl.browser.close()
                except Exception:
                    pass
            dl._print_summary(len(records))

            # Shutdown pipeline — wait for all workers to finish current tasks
            logger.info("Waiting for pipeline workers to complete...")
            pipeline.shutdown(wait=True, timeout=120)

            # Print final stats
            stats = pipeline.get_stats()
            logger.info(f"Pipeline stats: {stats}")
            
            try:
                import json
                with open(config.DATA_DIR / "ipc_state.json", "r") as f:
                    final_state = json.load(f).get("status", "")
            except Exception:
                final_state = ""
                
            if final_state not in ("CANCEL", "STOP"):
                StateEmitter.emit("PIPELINE_COMPLETE")

            dl.close()


if __name__ == "__main__":
    main()
