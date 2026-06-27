# ════════════════════════════════════════════════════════════════
#  CONFIGURATION — Adjust these values to match your environment
# ════════════════════════════════════════════════════════════════

# File paths
DEFAULT_EXCEL_FILE = "Invoices_rate18.xlsx"
DOWNLOAD_DIR = "downloads"
FAILED_LOG = "failed_irns.txt"

# Excel structure (1-based column indices)
IRN_COLUMN = 23          # Column W — 64-char IRN hash
INVOICE_NUM_COLUMN = 3   # Column C — Invoice Number (used for file naming)
HEADER_ROW = 1           # Row that contains the column headers

# Portal
PORTAL_URL = "https://einvoice.gst.gov.in/jsonDownload"

# Timing — human-like behaviour
MIN_DELAY_SEC = 1        # Shortest pause between consecutive IRN searches
MAX_DELAY_SEC = 2        # Longest  pause between consecutive IRN searches
BATCH_SIZE = 50          # Take a longer break after this many downloads
BATCH_PAUSE_SEC = 20     # Duration of the longer break (seconds)
SEARCH_TIMEOUT_MS = 30_000    # Max wait for search results to appear
DOWNLOAD_TIMEOUT_MS = 60_000  # Max wait for a file download to complete
ELEMENT_TIMEOUT_MS = 15_000   # Max wait for a UI element to appear

# Retry
MAX_RETRIES_PER_IRN = 2  # Attempts per IRN before marking it as failed
