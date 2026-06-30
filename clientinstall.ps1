Remove-Item -Recurse -Force build, dist, obfuscated_build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path obfuscated_build
Copy-Item -Recurse gst_downloader obfuscated_build\gst_downloader
Copy-Item -Recurse config obfuscated_build\config

$env:PATH += ";C:\Users\SASI KOTHA\AppData\Roaming\Python\Python312\Scripts"

pyarmor gen -O obfuscated_build -r gst_downloader


# $env:PLAYWRIGHT_BROWSERS_PATH="0"
# python -m playwright install chromium

cd obfuscated_build
python -m PyInstaller -y --name "GST_Automation" --onedir --add-data "gst_downloader/web/static;gst_downloader/web/static" --add-data "config/pdf_config.yaml;config" --collect-submodules gst_downloader --hidden-import gst_downloader.config --hidden-import gst_downloader.main --hidden-import gst_downloader.core --hidden-import gst_downloader.core.downloader --hidden-import gst_downloader.core.pipeline --hidden-import gst_downloader.core.state --hidden-import gst_downloader.processing --hidden-import gst_downloader.processing.excel_preprocessor --hidden-import gst_downloader.processing.excel_reader --hidden-import gst_downloader.processing.pdf_modifier --hidden-import gst_downloader.utils --hidden-import gst_downloader.utils.helpers --hidden-import gst_downloader.utils.logger --hidden-import gst_downloader.web --collect-all fastapi --collect-all uvicorn --collect-all starlette --collect-all pydantic --collect-all fitz --collect-all ruamel.yaml --collect-all psutil --collect-all openpyxl --collect-all playwright gst_downloader/app.py
