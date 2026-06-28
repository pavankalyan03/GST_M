"""
Test suite for gst_invoice_downloader.py
Validates: imports, Excel reading, filename sanitization,
           Playwright browser launch, and portal page load.
"""

import sys
import os
import time

# Force UTF-8 output for console
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"

# -- Test 1: Imports -------------------------------------------
print("=" * 55)
print("  TEST 1: Verify all imports")
print("=" * 55)
try:
    import openpyxl
    print(f"  {PASS} openpyxl imported successfully")
except ImportError as e:
    print(f"  {FAIL} openpyxl import failed: {e}")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    print(f"  {PASS} playwright imported successfully")
except ImportError as e:
    print(f"  {FAIL} playwright import failed: {e}")
    sys.exit(1)

try:
    import argparse, logging, random, pathlib
    from gst_downloader import config
    from gst_downloader.utils import sanitize_filename
    from gst_downloader.excel_reader import read_irns_from_excel
    print(f"  {PASS} standard library and project imports OK")
except ImportError as e:
    print(f"  {FAIL} project import failed: {e}")
    sys.exit(1)

# -- Test 2: Excel Reading ------------------------------------
print()
print("=" * 55)
print("  TEST 2: Read IRNs from Excel file")
print("=" * 55)

EXCEL_FILE = "Invoices_rate18.xlsx"

if not os.path.exists(EXCEL_FILE):
    print(f"  {FAIL} Excel file not found: {EXCEL_FILE}")
    sys.exit(1)

logger = logging.getLogger("test")
logger.addHandler(logging.NullHandler())
records = read_irns_from_excel(EXCEL_FILE, logger)

print(f"  Sheet: 'Sheet1'  |  Rows: {len(records) + config.HEADER_ROW}  |  Cols: 24")
print(f"  Found {len(records)} IRN records:")
for r in records[:7]:
    irn = r["irn"]
    irn_short = f"{irn[:20]}...{irn[-10:]}" if len(irn) > 30 else irn
    print(f"    Row {r['row']:>2}: Invoice={r['invoice_number']:<16}  IRN={irn_short}")

if len(records) == 0:
    print(f"  {FAIL} No IRNs found!")
    sys.exit(1)
else:
    print(f"  {PASS} Excel reading passed - {len(records)} records")

# -- Test 3: Filename Sanitization ----------------------------
print()
print("=" * 55)
print("  TEST 3: Filename sanitization")
print("=" * 55)

test_cases = [
    ("LBAAAD2270203721", "LBAAAD2270203721"),
    ("INV/2026/001", "INV_2026_001"),
    ("test file (1)", "test_file__1_"),
]
all_passed = True
for inp, expected in test_cases:
    result = sanitize_filename(inp)
    status = PASS if result == expected else FAIL
    if result != expected:
        all_passed = False
    print(f"  {status} sanitize('{inp}') -> '{result}'  (expected: '{expected}')")

if all_passed:
    print(f"  {PASS} Sanitization passed")

# -- Test 4: Playwright Browser Launch -------------------------
print()
print("=" * 55)
print("  TEST 4: Playwright browser launch + portal load")
print("=" * 55)

try:
    with sync_playwright() as pw:
        print("  Launching Chromium (headed) ...")
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1380, "height": 800},
        )
        page = context.new_page()
        print(f"  {PASS} Browser launched")

        # Navigate to the GST portal
        print("  Navigating to GST e-Invoice portal ...")
        page.goto("https://einvoice.gst.gov.in/jsonDownload",
                   wait_until="networkidle", timeout=60_000)
        print(f"  {PASS} Page loaded - URL: {page.url}")
        print(f"  {PASS} Title: {page.title()}")

        # Give the Angular SPA a moment to bootstrap
        time.sleep(4)

        # Check what elements are on the page
        body_text = page.text_content("body") or ""
        has_login = "login" in body_text.lower() or "sign in" in body_text.lower()
        has_download = "download" in body_text.lower()

        print(f"  {INFO} Page has 'login' text: {has_login}")
        print(f"  {INFO} Page has 'download' text: {has_download}")

        # Try to find common page elements
        irn_input = page.locator('input[placeholder*="IRN"]')
        irn_count = irn_input.count()
        print(f"  {INFO} IRN input fields found: {irn_count}")
        if irn_count > 0:
            print(f"  {PASS} IRN input field is present")
        else:
            print(f"  {INFO} IRN input not found - likely requires login first (expected)")

        # Check for tab elements
        received_tab = page.locator("text=Received")
        print(f"  {INFO} 'Received' tab found: {received_tab.count() > 0}")

        generated_tab = page.locator("text=Generated")
        print(f"  {INFO} 'Generated' tab found: {generated_tab.count() > 0}")

        search_btn = page.get_by_role("button", name="Search")
        try:
            search_visible = search_btn.is_visible(timeout=2000)
        except Exception:
            search_visible = False
        print(f"  {INFO} 'Search' button visible: {search_visible}")

        reset_btn = page.get_by_role("button", name="Reset")
        try:
            reset_visible = reset_btn.is_visible(timeout=2000)
        except Exception:
            reset_visible = False
        print(f"  {INFO} 'Reset' button visible: {reset_visible}")

        print()
        print("  Closing browser ...")
        browser.close()
        print(f"  {PASS} Browser closed cleanly")

except Exception as e:
    print(f"  {FAIL} Playwright test failed: {e}")
    import traceback
    traceback.print_exc()

# -- Test 5: Main Script Syntax Check -----------------------------
print()
print("=" * 55)
print("  TEST 5: Main script syntax check")
print("=" * 55)

try:
    with open("main.py", "r", encoding="utf-8") as f:
        compile(f.read(), "main.py", "exec")
    print(f"  {PASS} main.py compiles without syntax errors")
except SyntaxError as e:
    print(f"  {FAIL} Syntax error in main.py: {e}")
    all_passed = False

# -- Summary ---------------------------------------------------
print()
print("=" * 55)
print("  ALL TESTS COMPLETE")
print("=" * 55)
