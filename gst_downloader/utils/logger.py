import os
import logging
import sys
from datetime import datetime

def setup_logging() -> logging.Logger:
    """Configure dual logging: console + timestamped log file."""
    from gst_downloader import config

    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(exist_ok=True, parents=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = log_dir / f"gst_download_{timestamp}.log"

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
