# GST Automation — Setup Guide

Follow these steps to set up the automation correctly on a fresh Windows system.

## 1. Install Python

1. Download Python 3.12 (or latest) from the official website: https://www.python.org/downloads/
2. **IMPORTANT**: During the installation setup window, make sure to check the box that says **"Add Python to PATH"** before clicking Install.

## 2. Install Required Python Packages

Once Python is installed, open **Command Prompt** (or PowerShell) and navigate to the folder containing this project. Run the following command:

```bash
pip install -r requirements.txt
```

This will automatically install all the necessary libraries:
- **playwright** — For automating the web browser
- **openpyxl** — For reading Excel files
- **pymupdf** — For modifying the PDFs
- **ruamel.yaml** — For safely reading/saving the Settings file
- **fastapi, uvicorn, python-multipart** — For running the Web UI

## 3. Install Playwright Browsers

The automation uses a special browser engine to scrape the GST portal. You MUST install it by running this command in the terminal:

```bash
playwright install chromium
```

## 4. How to Start the App

To run the automation, simply double-click the `run.bat` file, or open the terminal in the project folder and type:

```bash
python app.py
```

A browser window will automatically pop up with the Web UI!

## 5. How to Use

1. Open the application (it opens in your browser)
2. Upload your monthly Excel file (drag & drop or click to browse)
3. Give the batch a meaningful name (e.g., "June_2026_Invoices")
4. Click **Proceed** to start
5. Log in to the GST portal when prompted
6. The app will download and modify all invoices automatically
7. Find your processed files in the `processed/` folder
