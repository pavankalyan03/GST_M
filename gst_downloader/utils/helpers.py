import time
import random
import threading
from pathlib import Path
from gst_downloader import config

_failed_log_lock = threading.Lock()

def update_failed_irn_log(log_path: str, action: str, irn: str):
    """
    Thread-safe update to the batch-specific failed IRNs log.
    action: 'add' or 'remove'
    """
    if not log_path or not irn:
        return
        
    path = Path(log_path)
    
    with _failed_log_lock:
        if action == 'add':
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = set()
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    existing = set(line.strip() for line in f if line.strip())
            
            if irn not in existing:
                with open(path, 'a', encoding='utf-8') as f:
                    f.write(f"{irn}\n")
                    
        elif action == 'remove':
            if not path.exists():
                return
            with open(path, 'r', encoding='utf-8') as f:
                lines = [line.strip() for line in f if line.strip()]
                
            if irn in lines:
                lines.remove(irn)
                if lines:
                    with open(path, 'w', encoding='utf-8') as f:
                        for line in lines:
                            f.write(f"{line}\n")
                else:
                    path.unlink() # Delete file if empty

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
