import sys
import json
import threading

class StateEmitter:
    """
    Emits structured JSON events to stdout for the FastAPI server to intercept.
    These events synchronize the global pipeline state with the frontend UI.
    """
    
    _lock = threading.Lock()
    
    @classmethod
    def emit(cls, event_type: str, data: dict = None):
        """
        Emit a state event.
        
        Args:
            event_type: A string identifying the type of event (e.g., 'INIT_BATCH', 'DOWNLOADER_STATUS').
            data: A dictionary containing event-specific data.
        """
        if data is None:
            data = {}
            
        payload = {
            "type": event_type,
            "data": data
        }
        
        # Lock to ensure atomic print
        with cls._lock:
            # Prefix with __STATE_EVENT__: so app.py can identify it
            sys.stdout.write(f"__STATE_EVENT__:{json.dumps(payload)}\n")
            sys.stdout.flush()
