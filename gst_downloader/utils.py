import time
import random
from gst_downloader import config

def human_delay(min_s: float = None, max_s: float = None):
    """Sleep for a random duration to simulate human pacing."""
    if min_s is None:
        min_s = config.MIN_DELAY_SEC
    if max_s is None:
        max_s = config.MAX_DELAY_SEC
    time.sleep(random.uniform(min_s, max_s))

def sanitize_filename(name: str) -> str:
    """Remove characters that are unsafe in Windows/Linux filenames."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)
