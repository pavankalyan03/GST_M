@echo off
echo Starting GST Invoice Automation...
cd %~dp0
set PYTHONPATH=%cd%
python gst_downloader\app.py
pause
