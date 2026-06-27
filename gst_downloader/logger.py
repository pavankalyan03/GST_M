import os
import logging
import sys
from datetime import datetime

def setup_logging() -> logging.Logger:
    """Configure dual logging: console + timestamped log file."""
    os.makedirs("logs", exist_ok=True)
    log_filename = os.path.join("logs", f"gst_download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_filename, encoding="utf-8"),
        ],
    )
    logger = logging.getLogger("gst_downloader")
    logger.info(f"Log file: {log_filename}")
    return logger
