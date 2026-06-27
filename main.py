import os
import sys
import argparse


# Resume from row 25
# python main.py --start-from 25

# Use custom delays (slower)
# python main.py --delay-min 5 --delay-max 10

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
from gst_downloader.logger import setup_logging
from gst_downloader.excel_reader import read_irns_from_excel
from gst_downloader.core import GSTInvoiceDownloader
from gst_downloader.excel_preprocessor import preprocess_excel

def parse_args():
    ap = argparse.ArgumentParser(
        description="GST e-Invoice Bulk Downloader — downloads invoices one-by-one via Playwright",
    )
    ap.add_argument(
        "--excel", default=config.DEFAULT_EXCEL_FILE,
        help=f"Path to the Excel file with IRNs (default: {config.DEFAULT_EXCEL_FILE})",
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

    # ── 0. Preprocess Excel ───────────────────────────────────
    if os.path.exists(config.RAW_EXCEL_FILE):
        try:
            preprocess_excel(config.RAW_EXCEL_FILE, args.excel, logger)
        except Exception as e:
            logger.error(f"Failed to preprocess {config.RAW_EXCEL_FILE}: {e}")
            sys.exit(1)

    # ── 1. Read Excel ─────────────────────────────────────────
    records = read_irns_from_excel(args.excel, logger)
    if not records:
        logger.error("No IRNs found — nothing to do.")
        sys.exit(1)

    # ── 2. Resolve --start-from ───────────────────────────────
    #    The user passes an Excel row number; convert to a list index.
    start_index = 0
    if args.start_from > 1:
        # Find the record whose row number matches
        for idx, rec in enumerate(records):
            if rec["row"] >= args.start_from:
                start_index = idx
                break
        else:
            logger.warning(f"Row {args.start_from} not found -- starting from the beginning")
        logger.info(f"Resuming from row {records[start_index]['row']} (index {start_index})")

    # ── 3. Launch Playwright and run ──────────────────────────
    with sync_playwright() as pw:
        dl = GSTInvoiceDownloader(pw, logger)
        try:
            dl.launch_browser()
            dl.navigate_to_portal()
            dl.prompt_manual_login()
            dl.run(records, start_index=start_index)
        except KeyboardInterrupt:
            logger.info("\nInterrupted by user (Ctrl+C)")
            dl._print_summary(len(records))
        except Exception as exc:
            logger.error(f"Fatal error: {exc}", exc_info=True)
            dl._print_summary(len(records))
        finally:
            print("Press ENTER to close the browser...")
            try:
                input()
            except EOFError:
                pass
            dl.close()


if __name__ == "__main__":
    main()
