@echo off
echo Installing requirements...
pip install -r config\requirements.txt
echo.
echo Installing Playwright Browser...
playwright install chromium
echo.
echo Setup Complete! You can now start the app using run.bat
pause
