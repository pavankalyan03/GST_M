import time
import logging
from pathlib import Path
from datetime import datetime
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from gst_downloader import config
from gst_downloader.utils.helpers import human_delay, sanitize_filename, update_failed_irn_log
from gst_downloader.core.state import StateEmitter

import json

def read_ipc_state():
    try:
        with open(config.DATA_DIR / "ipc_state.json", "r") as f:
            return json.load(f).get("status", "RUN")
    except Exception:
        return "RUN"

def write_ipc_state(state):
    try:
        with open(config.DATA_DIR / "ipc_state.json", "w") as f:
            json.dump({"status": state}, f)
    except Exception:
        pass

def wait_for_ui_prompt():
    write_ipc_state("UI_PROMPT")
    StateEmitter.emit("STATUS_UPDATE", {"status": "WAITING_FOR_LOGIN"})
    while True:
        state = read_ipc_state()
        if state in ["RUN", "RESUME", "PROMPT_CONTINUE", "CANCEL", "STOP"]:
            break
        time.sleep(1)
    
    state = read_ipc_state()
    if state == "CANCEL":
        raise KeyboardInterrupt("Cancelled by UI")
    if state == "STOP":
        raise KeyboardInterrupt("Stopped by UI")
    write_ipc_state("RUN")
    StateEmitter.emit("STATUS_UPDATE", {"status": "RUNNING"})

def check_pause_cancel(log):
    state = read_ipc_state()
    if state == "CANCEL":
        log.info("[SYSTEM] Automation cancelled by user.")
        raise KeyboardInterrupt("Cancelled by UI")
    if state == "STOP":
        log.info("[SYSTEM] Automation stopped by user.")
        raise KeyboardInterrupt("Stopped by UI")
    if state == "PAUSE":
        log.info("[SYSTEM] Automation paused. Waiting for resume...")
        StateEmitter.emit("STATUS_UPDATE", {"status": "PAUSED"})
        while read_ipc_state() == "PAUSE":
            time.sleep(1)
        StateEmitter.emit("STATUS_UPDATE", {"status": "RUNNING"})
        log.info("[SYSTEM] Automation resumed.")
    if state == "PAUSE_ERRORS":
        log.error("[SYSTEM] Automation paused due to excessive errors. Waiting for manual intervention...")
        StateEmitter.emit("STATUS_UPDATE", {"status": "PAUSED_ERRORS"})
        while read_ipc_state() == "PAUSE_ERRORS":
            time.sleep(1)
        if read_ipc_state() == "RUN":
            StateEmitter.emit("STATUS_UPDATE", {"status": "RUNNING"})
            log.info("[SYSTEM] Automation resumed.")

#  CORE AUTOMATION CLASS
# ════════════════════════════════════════════════════════════════

class GSTInvoiceDownloader:
    """
    Manages a single Playwright browser session to search for IRNs
    on the GST e-Invoice portal and download the corresponding JSONs.
    """

    def __init__(self, pw, logger: logging.Logger, on_download_success=None,
        dl_dir=config.STAGING_DIR,
        out_dir=None,
        failed_log_path=None
    ):
        """
        :param pw: playwright instance
        :param logger: Configured logger instance
        :param on_download_success: Callback function fired with (pdf_path) on success
        :param dl_dir: Directory where the browser saves downloaded PDFs
        :param out_dir: Final directory for the processed PDFs
        :param failed_log_path: Path to batch-specific failed IRNs log file
        """
        self.pw = pw
        self.log = logger
        self.browser = None
        self.context = None
        self.page = None
        self.on_download_success = on_download_success
        self.dl_path = Path(dl_dir).resolve()
        self.dl_path.mkdir(parents=True, exist_ok=True)
        self.out_dir = Path(out_dir).resolve() if out_dir else None
        self.failed_log_path = failed_log_path

        # Result tracking
        self.succeeded: list[dict] = []
        self.failed: list[dict] = []

    # ── Browser lifecycle ─────────────────────────────────────

    def launch_browser(self):
        """Open a headed (visible) Chromium window."""
        self.log.info("Launching Chromium in headed mode...")
        self.browser = self.pw.chromium.launch(
            headless=False,
            # slow_mo=80,      # Removed to unlock maximum interaction speed
        )
        self.context = self.browser.new_context(
            accept_downloads=True,
            viewport={"width": 1380, "height": 800},
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(config.ELEMENT_TIMEOUT_MS)
        
        # Force the browser to the front so the user sees it immediately
        self.page.bring_to_front()
        
        self.log.info("Browser ready")

    def navigate_to_portal(self):
        """Load the GST e-Invoice download page."""
        self.log.info(f"Navigating to {config.PORTAL_URL}...")
        self.page.goto(config.PORTAL_URL, wait_until="networkidle", timeout=60_000)
        self.log.info("Portal loaded")

    def close(self):
        """Gracefully close everything."""
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass

    # ── Login helpers ─────────────────────────────────────────

    def prompt_manual_login(self):
        """Block until the user has logged in and navigated to the right page."""
        print()
        print("=" * 62)
        print("   MANUAL LOGIN REQUIRED")
        print("=" * 62)
        print()
        print("   Complete these steps in the browser window that just opened:")
        print()
        print("   1.  Log in to the GST portal (CAPTCHA / OTP)")
        print("   2.  Go to  Download e-Invoice  →  Received  tab")
        print("   3.  Select the  'By IRN'  sub-tab")
        print("   4.  Confirm you can see the IRN input field")
        print()
        print("   Then come back here and click Continue in the UI.")
        print("=" * 62)
        self.log.info("[UI_PROMPT] Action Required: Please log in manually on the browser, then click Continue.")
        wait_for_ui_prompt()
        print()
        self.log.info("User confirmed login -- automation starting")

    def prompt_relogin(self, done: int, total: int):
        """Ask the user to re-login after a session loss or browser crash."""
        
        # Check if browser completely died/closed and needs relaunch
        browser_dead = False
        try:
            if not self.browser.is_connected() or self.page.is_closed():
                browser_dead = True
        except Exception:
            browser_dead = True
            
        if browser_dead:
            self.log.warning("Browser process died or was closed! Relaunching...")
            try:
                self.close() # Clean up old dead references just in case
            except Exception:
                pass
            self.launch_browser()
            self.navigate_to_portal()

        print()
        print("=" * 62)
        if browser_dead:
            print("   WARNING: BROWSER RESTARTED -- PLEASE LOG IN")
        else:
            print("   WARNING: SESSION EXPIRED -- PLEASE RE-LOGIN")
        print("=" * 62)
        print()
        print(f"   Progress so far: {done} / {total} IRNs")
        print(f"   The script will resume from IRN #{done + 1}.")
        print()
        print("   In the browser:")
        print("   1.  Log in to the GST portal (handle CAPTCHA / OTP)")
        print("   2.  Navigate back to  Received → By IRN")
        print()
        print("   Then click Continue in the UI.")
        print("=" * 62)
        
        if browser_dead:
            self.log.info("[UI_PROMPT] Action Required: Browser restarted. Please log in manually, then click Continue.")
        else:
            self.log.info("[UI_PROMPT] Action Required: SESSION EXPIRED. Please log in manually, then click Continue.")
            
        wait_for_ui_prompt()
        print()
        self.log.info("User confirmed re-login -- resuming")

    # ── Session validation ────────────────────────────────────

    def is_session_alive(self) -> bool:
        """
        Quick health-check: is the user still logged in and on
        the right page?
        """
        try:
            url = self.page.url.lower()
            # Redirect to a login/auth page means session died
            if any(kw in url for kw in ("login", "auth", "signin", "captcha")):
                self.log.warning("Detected redirect to login page")
                return False

            # The IRN input field should be present on the correct page
            irn_input = self.page.locator('input[placeholder*="Enter IRN"]')
            if irn_input.count() == 0:
                self.log.warning("IRN input field not found on page")
                return False

            if not irn_input.first.is_visible(timeout=5_000):
                self.log.warning("IRN input field exists but is not visible")
                return False

            return True
        except Exception as exc:
            self.log.warning(f"Session check failed: {exc}")
            return False

    # ── Search ────────────────────────────────────────────────

    def _locate_irn_input(self):
        """Find the IRN text-input using several fallback selectors."""
        selectors = [
            'input[placeholder*="Enter IRN"]',
            'input[placeholder*="IRN"]',
            '#irn',
            'input[name*="irn" i]',
            'input[id*="irn" i]',
        ]
        for sel in selectors:
            el = self.page.locator(sel)
            if el.count() > 0 and el.first.is_visible(timeout=3_000):
                return el.first
        raise RuntimeError("Cannot locate the IRN input field")

    def search_irn(self, irn: str) -> bool:
        """
        Type an IRN into the search box and click Search.

        Returns True when the search-result table appears,
        False on timeout or 'no records found'.
        """
        try:
            # Locate and fill the IRN input
            irn_input = self._locate_irn_input()
            # fill() automatically clears previous value instantly
            irn_input.fill(irn)
            self.log.info(f"  Typed IRN: {irn[:24]}...{irn[-12:]}")

            # Click "Search"
            search_btn = self.page.get_by_role("button", name="Search")
            search_btn.click()
            self.log.info("  Clicked Search -- waiting for results...")

            # Wait for the results section to appear
            # The portal renders a "Search Result" heading when data is found
            result_appeared = False
            try:
                self.page.locator("text=Search Result").wait_for(
                    state="visible", timeout=config.SEARCH_TIMEOUT_MS,
                )
                result_appeared = True
            except PlaywrightTimeout:
                pass

            if result_appeared:
                # Removed artificial sleep; Playwright's wait_for in download_invoice handles synchronization safely
                self.log.info("  Search results visible")
                return True

            # Check if the portal said "no records"
            body_text = (self.page.text_content("body") or "").lower()
            if any(phrase in body_text for phrase in
                   ("no record", "not found", "no data", "no matching")):
                self.log.warning("  Portal returned: no records for this IRN")
            else:
                self.log.warning("  Search timed out — no result section appeared")
            return False

        except PlaywrightTimeout as exc:
            self.log.error(f"  Search timeout: {exc}")
            return False
        except Exception as exc:
            self.log.error(f"  Search error: {exc}")
            return False

    # ── Download ──────────────────────────────────────────────

    def download_invoice(self, invoice_number: str, invoice_date: str = "", irn: str = "") -> bool:
        """
        Click the download control and save the resulting file.

        Tries two strategies:
         1. The per-row download icon in the Action column
         2. The green "DOWNLOAD(JSON)" button below the results
        """
        try:
            downloaded = False
            download = None

            # ── Strategy 1: per-row download icon ─────────────
            row_icon_selectors = [
                # Common Angular / Bootstrap table icon patterns
                "table tbody tr td:last-child a",
                "table tbody tr td:last-child button",
                "table tbody tr td:last-child i",
                "table tbody tr td:last-child span[class*='download']",
                ".fa-download",
                "i[class*='download']",
                "a[title*='ownload']",
                "button[title*='ownload']",
                "[class*='action'] a",
                "[class*='action'] button",
            ]

            for sel in row_icon_selectors:
                try:
                    elements = self.page.locator(sel)
                    if elements.count() > 0 and elements.first.is_visible(timeout=2_000):
                        with self.page.expect_download(timeout=config.DOWNLOAD_TIMEOUT_MS) as dl_info:
                            elements.first.click()
                        download = dl_info.value
                        downloaded = True
                        self.log.info("  Download triggered via row icon")
                        break
                except (PlaywrightTimeout, Exception):
                    continue

            # ── Strategy 2: DOWNLOAD(JSON) button ─────────────
            if not downloaded:
                self.log.info("  Row icon not found -- trying DOWNLOAD(JSON) button...")
                dl_btn_selectors = [
                    "button:has-text('DOWNLOAD(JSON)')",
                    "button:has-text('DOWNLOAD')",
                    "a:has-text('DOWNLOAD(JSON)')",
                    "a:has-text('DOWNLOAD')",
                ]
                for sel in dl_btn_selectors:
                    try:
                        btn = self.page.locator(sel)
                        if btn.count() > 0 and btn.first.is_visible(timeout=3_000):
                            with self.page.expect_download(timeout=config.DOWNLOAD_TIMEOUT_MS) as dl_info:
                                btn.first.click()
                            download = dl_info.value
                            downloaded = True
                            self.log.info("  Download triggered via DOWNLOAD(JSON) button")
                            break
                    except (PlaywrightTimeout, Exception):
                        continue

            if not downloaded or download is None:
                self.log.error("  [FAIL] Could not trigger any download action")
                return False

            # ── Save the file ─────────────────────────────────
            safe_name = sanitize_filename(invoice_number)
            if invoice_date:
                safe_name = f"{safe_name}_{invoice_date}"
            save_path = self.dl_path / f"{safe_name}.pdf"

            # Avoid overwriting an earlier download with the same name
            counter = 1
            while save_path.exists():
                save_path = self.dl_path / f"{safe_name}_{counter}.pdf"
                counter += 1

            download.save_as(str(save_path))
            self.log.info(f"  [OK] Saved -> {save_path.name}")
            
            if self.on_download_success is not None:
                self.on_download_success(str(save_path), irn)
                
            return True

        except PlaywrightTimeout:
            self.log.error("  Download timed out -- file never arrived")
            return False
        except Exception as exc:
            self.log.error(f"  Download error: {exc}")
            return False

    # ── Reset form ────────────────────────────────────────────

    def _dismiss_confirmation_popup(self):
        """
        Handle the 'Warning: You are about to clear the entered details'
        confirmation dialog by clicking the 'Proceed' button.
        """
        try:
            # Look for the Proceed button in the confirmation popup
            proceed_selectors = [
                "button:has-text('Proceed')",
                "button:has-text('proceed')",
                ".modal-footer button:has-text('Proceed')",
                ".swal2-confirm",                    # SweetAlert2
                ".modal .btn-primary",               # Bootstrap modal
                "button.confirm",
            ]
            for sel in proceed_selectors:
                btn = self.page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible(timeout=2_000):
                    btn.first.click()
                    time.sleep(0.8)
                    self.log.info("  Clicked 'Proceed' on confirmation popup")
                    return True
        except Exception:
            pass
        return False

    def reset_form(self):
        """
        Clear the search form so the next IRN can be entered.
        Tries Reset button (+ handles confirmation popup) → Back button → manual field clear.
        """
        try:
            # Try the "Reset" button first (clears the entire form)
            reset_btn = self.page.get_by_role("button", name="Reset")
            if reset_btn.is_visible(timeout=3_000):
                reset_btn.click()
                time.sleep(0.5)

                # The portal shows a confirmation popup:
                # "You are about to clear the entered details. Would you like to proceed?"
                self._dismiss_confirmation_popup()

                self.log.info("  Form reset via Reset button")
                return
        except Exception:
            pass

        try:
            # Try the "Back" button (returns from result view to form)
            back_btn = self.page.get_by_role("button", name="Back")
            if back_btn.is_visible(timeout=2_000):
                back_btn.click()
                time.sleep(0.5)

                # Back button may also trigger a confirmation popup
                self._dismiss_confirmation_popup()

                self.log.info("  Form reset via Back button")
                return
        except Exception:
            pass

        try:
            # Last resort: just clear the input field
            irn_input = self._locate_irn_input()
            irn_input.fill("")
            self.log.info("  Cleared IRN field manually")
        except Exception as exc:
            self.log.warning(f"  Reset failed (non-critical): {exc}")

    # ── Single-IRN pipeline ───────────────────────────────────

    def process_one(self, record: dict) -> bool:
        """Search → Download for a single IRN (no Reset; the next IRN overwrites the field)."""
        irn = record["irn"]
        inv = record["invoice_number"]
        inv_date = record.get("invoice_date", "")

        safe_name = sanitize_filename(inv)
        if inv_date:
            safe_name = f"{safe_name}_{inv_date}"
            
        # ── Smart Resume: Check if already processed ──
        if self.out_dir:
            final_path = self.out_dir / f"{safe_name}.pdf"
            if final_path.exists():
                self.log.info(f"  [SKIPPED] Already processed: {final_path.name}")
                return True

        self.log.info(f"  Invoice: {inv}  |  Date: {inv_date}  |  IRN: {irn[:24]}...{irn[-12:]}")

        if not self.search_irn(irn):
            return False

        ok = self.download_invoice(inv, inv_date, irn)
        return ok

    # ── Main loop ─────────────────────────────────────────────

    def run(self, records: list[dict], start_index: int = 0):
        """
        Process every record from start_index onward.
        Maintains a single session; pauses for re-login on session loss.
        """
        # Pre-scan the out_dir to build a set of already processed files for fast resume
        processed_files = set()
        if self.out_dir and self.out_dir.exists():
            processed_files = {f.name for f in self.out_dir.glob("*.pdf")}
            
        total = len(records)
        self.log.info(f"Processing {total - start_index} IRNs (starting from #{start_index + 1})")
        i = start_index
        StateEmitter.emit("STATUS_UPDATE", {"status": "RUNNING"})
        consecutive_failures = 0
        
        while i < total:
            # Fix payload explosion: Only send the next 5 IRNs instead of the full remaining queue
            remaining_queue = [r["irn"] for r in records[i:i+5]]
            StateEmitter.emit("QUEUE_UPDATE", {"queue": remaining_queue})
            
            check_pause_cancel(self.log)
            rec = records[i]
            
            irn = rec["irn"]
            
            # ── Fast Skip for already processed files ──
            safe_name = sanitize_filename(rec["invoice_number"])
            if rec.get("invoice_date"):
                safe_name = f"{safe_name}_{rec['invoice_date']}"
            expected_filename = f"{safe_name}.pdf"
            
            if self.out_dir and expected_filename in processed_files:
                progress = f"[{i + 1}/{total}]"
                self.log.info(f"{progress} {'-' * 46}")
                self.log.info(f"  [SKIPPED] Already processed: {expected_filename}")
                self.succeeded.append(rec)
                StateEmitter.emit("DOWNLOADER_SUCCESS", {"irn": irn})
                if self.failed_log_path:
                    update_failed_irn_log(self.failed_log_path, 'remove', irn)
                i += 1
                continue
                
            progress = f"[{i + 1}/{total}]"
            self.log.info(f"{progress} {'-' * 46}")
            
            StateEmitter.emit("DOWNLOADER_STATUS", {"status": "processing", "current": irn})

            # ── Session health check ──────────────────────────
            if not self.is_session_alive():
                self.log.warning("Session lost!")
                self.prompt_relogin(done=i, total=total)
                # After re-login, retry the SAME IRN — don't skip it
                continue

            # ── Try with retries ──────────────────────────────
            success = False
            for attempt in range(1, config.MAX_RETRIES_PER_IRN + 1):
                if attempt > 1:
                    self.log.info(f"  -> Retry {attempt}/{config.MAX_RETRIES_PER_IRN}")
                    time.sleep(2)
                    if not self.is_session_alive():
                        self.prompt_relogin(done=i, total=total)
                        break   # will re-enter while-loop and retry this i

                success = self.process_one(rec)
                if success:
                    break

            if success:
                self.succeeded.append(rec)
                StateEmitter.emit("DOWNLOADER_SUCCESS", {"irn": irn})
                consecutive_failures = 0
                if self.failed_log_path:
                    update_failed_irn_log(self.failed_log_path, 'remove', irn)
            else:
                self.failed.append(rec)
                self.log.warning(
                    f"  [FAIL] after {config.MAX_RETRIES_PER_IRN} attempts -- {rec['invoice_number']}"
                )
                StateEmitter.emit("DOWNLOADER_FAIL", {"irn": irn, "error": "Download failed after retries"})
                consecutive_failures += 1
                if self.failed_log_path:
                    update_failed_irn_log(self.failed_log_path, 'add', irn)

            if consecutive_failures >= 10:
                self.log.error("10 consecutive failures detected! Pausing pipeline for manual intervention.")
                write_ipc_state("PAUSE_ERRORS")
                check_pause_cancel(self.log)
                consecutive_failures = 0 # Reset after resume

            i += 1
            
            # ── Pacing ────────────────────────────────────────
            if i < total:
                StateEmitter.emit("DOWNLOADER_STATUS", {"status": "idle"})
                if i % config.BATCH_SIZE == 0:
                    self.log.info(
                        f"\n  [PAUSE] Batch pause ({config.BATCH_PAUSE_SEC}s) after {i} IRNs..."
                    )
                    time.sleep(config.BATCH_PAUSE_SEC)
                else:
                    human_delay()

        StateEmitter.emit("DOWNLOADER_STATUS", {"status": "idle"})
        self._print_summary(total)

    # ── Summary & failed-IRN log ──────────────────────────────

    def _print_summary(self, total: int):
        s, f = len(self.succeeded), len(self.failed)
        print()
        print("=" * 62)
        print("   DOWNLOAD COMPLETE")
        print("=" * 62)
        print(f"   Total processed : {s + f}")
        print(f"   Succeeded       : {s}")
        print(f"   Failed          : {f}")
        print(f"   Folder          : {self.dl_path}")
        print("=" * 62)

        if self.failed:
            print()
            print("   Failed IRNs:")
            for rec in self.failed:
                print(f"     Row {rec['row']:>4}  {rec['invoice_number']}")

            if self.failed_log_path:
                print(f"\n   Saved to {self.failed_log_path} for retry.")

        print()
        self.log.info(f"Final result: {s}/{s + f} succeeded")


