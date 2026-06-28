import json
import sys

transcript_path = r"C:\Users\SASI KOTHA\.gemini\antigravity-ide\brain\1ada8a98-bd95-4962-a457-ca4adfd8a61c\.system_generated\logs\transcript_full.jsonl"

found_content = None

with open(transcript_path, 'r', encoding='utf-8') as f:
    for line in f:
        try:
            data = json.loads(line)
            if "tool_calls" in data:
                for call in data["tool_calls"]:
                    if call.get("name") == "default_api:write_to_file":
                        args = call.get("arguments", {})
                        if isinstance(args, str):
                            args = json.loads(args)
                        
                        target_file = args.get("TargetFile", "")
                        if "pdf_modifier.py" in target_file:
                            found_content = args.get("CodeContent")
                            # Keep looking so we get the most recent BEFORE my edits?
                            # Wait, the FIRST one is Claude's creation!
                            if found_content:
                                break
                if found_content:
                    break
        except Exception as e:
            pass

if found_content:
    with open(r"c:\Users\SASI KOTHA\Desktop\GST_M\pdf_modifier_v2.py", "w", encoding="utf-8") as out:
        out.write(found_content)
    print("Successfully restored original pdf_modifier.py from Claude into pdf_modifier_v2.py.")
else:
    print("Could not find the original creation of pdf_modifier.py in transcript.")
