# GST e-Invoice Bulk Downloader

## Overview
The **GST e-Invoice Bulk Downloader** is an automated web scraping tool built with Python and Playwright. It is designed to read Invoice Reference Numbers (IRNs) from an Excel spreadsheet, log into the GST e-Invoice portal (with manual login assistance), and sequentially download JSON e-Invoices.

## What We Did So Far
1. **Core Automation with Playwright (`gst_downloader/core.py`)**: 
   - Scripted a browser automation sequence that launches Chromium.
   - It navigates to the GST portal and waits for the user to manually enter their credentials and CAPTCHA.
   - Once logged in, it loops through a list of IRNs, inputs them into the search field, clicks search, and intercepts the resulting JSON file downloads.
2. **Excel Data Extraction (`gst_downloader/excel_reader.py`)**:
   - Implemented an integration with `openpyxl` to extract IRNs (Column W) and corresponding Invoice Numbers (Column C) from a provided Excel spreadsheet (e.g., `Invoices_rate18.xlsx`).
3. **Configuration and Tuning (`gst_downloader/config.py`)**:
   - Centralized configurations such as file paths, portal URL, and UI timeout/delay settings. 
   - Introduced randomized human-like delays and batch pausing to prevent aggressive scraping blocks from the portal.
4. **Resiliency and Logging (`gst_downloader/logger.py` & `main.py`)**:
   - Built a robust CLI using `argparse` allowing users to resume from a specific row and override delay settings.
   - Added comprehensive logging to track successes, failures, and connection timeouts, alongside saving failed IRNs to `failed_irns.txt`.
5. **Testing Suite (`test_downloader.py`)**:
   - Created a standalone test script to verify environment dependencies, validate Excel structure, ensure proper filename sanitization, and confirm that Playwright can successfully open the browser and load the portal.

## How It Works

### 1. Prerequisites
Ensure you have the required dependencies installed:
```bash
pip install -r requirements.txt
playwright install chromium
```
*Dependencies include `playwright` for browser automation and `openpyxl` for Excel parsing.*

### 2. Configuration
You can modify the default settings in `gst_downloader/config.py`:
- `DEFAULT_EXCEL_FILE`: The default Excel file name to parse (default: `Invoices_rate18.xlsx`).
- **Excel Structure**: Define which columns contain the IRN and Invoice Number.
- **Timing / Delays**: Adjust `MIN_DELAY_SEC` and `MAX_DELAY_SEC` to change how fast the bot searches. Setting this too low may result in rate-limiting.
- **Batching**: Adjust `BATCH_SIZE` and `BATCH_PAUSE_SEC` to simulate a human taking a break.

### 3. Usage
Run the main script from the terminal:
```bash
python main.py
```

**Command-line Arguments:**
- `--excel`: Specify a custom path to the Excel file.
- `--start-from`: Resume the download process from a specific Excel row number (e.g., if the script crashed at row 25, use `--start-from 25`).
- `--delay-min`: Override the minimum delay between searches.
- `--delay-max`: Override the maximum delay between searches.

**Execution Flow:**
1. **Launch**: The script opens a visible browser window.
2. **Login Phase**: The script navigates to the GST JSON Download portal and pauses. **You must manually log in and solve any CAPTCHAs.**
3. **Automation Phase**: Once you are on the search page, the script detects the UI and takes over. It iterates through the loaded IRNs, inputs them, and downloads the files to the `downloads/` directory.
4. **Completion**: A summary of successful and failed downloads is printed in the terminal.

### 4. Testing
To verify that everything is set up correctly without actually downloading data, you can run the test suite:
```bash
python test_downloader.py
```
This will check the imports, read the Excel file (to ensure the format matches), test the sanitization function, and attempt to open the Playwright browser.
