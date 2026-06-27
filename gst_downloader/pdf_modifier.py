"""
PDF Modifier for GST Invoices
==============================
Dynamically reads, configures, and modifies header names and
party-detail addresses in bulk-downloaded GST e-Invoice PDFs.

Usage:
    # Step 1 — Generate config from a sample PDF
    from gst_downloader.pdf_modifier import generate_config_file
    generate_config_file("downloads/BOM7-125914.pdf", "pdf_config.yaml")

    # Step 2 — User edits pdf_config.yaml (fill in 'new' values)

    # Step 3 — Apply to all PDFs
    from gst_downloader.pdf_modifier import batch_modify
    batch_modify("downloads", "modified_invoices", "pdf_config.yaml")
"""

import fitz  # PyMuPDF
import yaml
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
#  CONSTANTS
# ════════════════════════════════════════════════════════════════

# Font settings matching the original GST invoice PDFs
HEADER_FONT_SIZE = 22.0
PARTY_FONT_SIZE = 10.0
FONT_NAME = "helv"          # Helvetica (closest match to Roboto)
FONT_COLOR = (0, 0, 0)      # Black

# Y-coordinate boundaries to classify GSTIN anchor positions
# (determined from inspecting all 5 sample invoices)
HEADER_Y_MAX = 150           # Header GSTIN is always y < 150
RECIPIENT_Y_MIN = 400        # Recipient GSTIN is always 400 < y < 540
RECIPIENT_Y_MAX = 540
SHIP_TO_Y_MIN = 520          # Ship-To GSTIN is always y > 520


# ════════════════════════════════════════════════════════════════
#  EXTRACTION — Read fields from a PDF
# ════════════════════════════════════════════════════════════════

def _find_gstin_anchors(page, gstin: str) -> dict:
    """
    Find the 3 GSTIN occurrences on page 1 and classify them
    as 'header', 'recipient', or 'ship_to' based on y-position.

    Strategy: sort all hits by y-coordinate.
    - 1st (topmost) = header
    - 2nd           = recipient
    - 3rd           = ship_to

    Returns dict like:
        {'header': Rect, 'recipient': Rect, 'ship_to': Rect}
    """
    hits = page.search_for(gstin)
    if not hits:
        return {}

    # Sort by vertical position (topmost first)
    hits_sorted = sorted(hits, key=lambda r: r.y0)
    anchors = {}

    # Assign by order: 1st = header, 2nd = recipient, 3rd = ship_to
    labels = ['header', 'recipient', 'ship_to']
    for i, rect in enumerate(hits_sorted):
        if i < len(labels):
            anchors[labels[i]] = rect


    return anchors


def _extract_text_below_anchor(page, anchor_rect, max_lines=8, x_min=None):
    """
    Extract text lines that appear below a GSTIN anchor rect,
    in the same column (similar x-coordinate).

    Returns a list of (text, Rect) tuples for each line.
    """
    # Get all words on the page with positions
    words = page.get_text("words")  # list of (x0, y0, x1, y1, word, block, line, word_no)

    # Determine the column boundaries
    if x_min is None:
        x_min = anchor_rect.x0 - 30
    x_max = anchor_rect.x1 + 120  # allow some width extension

    # Collect words below the anchor, in the same column
    # Group by y-position into lines
    relevant_words = []
    for w in words:
        wx0, wy0, wx1, wy1, text, _, _, _ = w
        # Must be below anchor and in same x-range
        if wy0 >= anchor_rect.y1 - 2 and wx0 >= x_min - 5 and wx0 <= x_max + 50:
            # Don't go too far down (max ~120px below anchor)
            if wy0 < anchor_rect.y0 + 130:
                relevant_words.append(w)

    if not relevant_words:
        return []

    # Group words into lines by y-position (within 3px tolerance)
    relevant_words.sort(key=lambda w: (w[1], w[0]))  # sort by y, then x
    lines = []
    current_line_y = relevant_words[0][1]
    current_line_words = []

    for w in relevant_words:
        if abs(w[1] - current_line_y) < 3:
            current_line_words.append(w)
        else:
            if current_line_words:
                lines.append(current_line_words)
            current_line_words = [w]
            current_line_y = w[1]
    if current_line_words:
        lines.append(current_line_words)

    # Convert to (text, Rect) per line
    result = []
    for line_words in lines[:max_lines]:
        text = " ".join(w[4] for w in line_words)
        x0 = min(w[0] for w in line_words)
        y0 = min(w[1] for w in line_words)
        x1 = max(w[2] for w in line_words)
        y1 = max(w[3] for w in line_words)
        result.append((text, fitz.Rect(x0, y0, x1, y1)))

    return result


def _extract_header_name(page, anchor_rect):
    """Extract the business name from the header (the line below GSTIN)."""
    lines = _extract_text_below_anchor(page, anchor_rect, max_lines=3, x_min=anchor_rect.x0)
    # The header name is directly below the GSTIN, might span 1-2 lines
    name_parts = []
    name_rects = []
    for text, rect in lines:
        # Skip if it looks like a section header (e.g., "1.e-Invoice Details")
        if text.startswith("1.") or "Invoice" in text:
            break
        name_parts.append(text)
        name_rects.append(rect)

    if name_parts and name_rects:
        full_name = " ".join(name_parts)
        # Combine rects
        combined_rect = name_rects[0]
        for r in name_rects[1:]:
            combined_rect = combined_rect | r  # union
        return full_name, combined_rect
    return "", None


def _extract_recipient_fields(page, anchor_rect):
    """
    Extract recipient name and address from the Party Details section.
    Structure below GSTIN anchor:
        Line 1: Business Name
        Line 2: "Place of Supply: ..."
        Line 3: Address line 1
        Line 4: Address line 2 / City
        Line 5: Pincode
        Line 6: "State: ..."
    """
    lines = _extract_text_below_anchor(page, anchor_rect, max_lines=7)

    fields = {
        "name": {"text": "", "rect": None},
        "address": {"lines": [], "rects": [], "combined_text": ""},
    }

    if not lines:
        return fields

    # First line after GSTIN is always the name
    fields["name"]["text"] = lines[0][0]
    fields["name"]["rect"] = lines[0][1]

    # Section labels that should NOT be treated as address text
    section_labels = {"Ship To", "Dispatch From", "Supplier", "Recipient"}

    # Collect address lines (skip metadata and stop at section boundaries)
    for text, rect in lines[1:]:
        if text.startswith("Place of Supply"):
            continue
        if text.startswith("State:"):
            break  # State is the last field in each section
        # Stop if we hit a section label
        if text.strip() in section_labels:
            break
        fields["address"]["lines"].append(text)
        fields["address"]["rects"].append(rect)

    if fields["address"]["lines"]:
        fields["address"]["combined_text"] = "\n".join(fields["address"]["lines"])

    return fields


def _extract_ship_to_fields(page, anchor_rect):
    """
    Extract Ship To name and address.
    Structure below GSTIN anchor:
        Line 1: Business Name
        Line 2: Full address line
        Line 3: City
        Line 4: Pincode
        Line 5: "State: ..."
    """
    lines = _extract_text_below_anchor(page, anchor_rect, max_lines=7)

    fields = {
        "name": {"text": "", "rect": None},
        "address": {"lines": [], "rects": [], "combined_text": ""},
    }

    if not lines:
        return fields

    # First line is the name
    fields["name"]["text"] = lines[0][0]
    fields["name"]["rect"] = lines[0][1]

    # Remaining lines are the address (stop at "State:")
    for text, rect in lines[1:]:
        if text.startswith("State:"):
            break  # State is the last field
        fields["address"]["lines"].append(text)
        fields["address"]["rects"].append(rect)

    if fields["address"]["lines"]:
        fields["address"]["combined_text"] = "\n".join(fields["address"]["lines"])

    return fields


def extract_fields_from_pdf(pdf_path: str) -> dict:
    """
    Open a GST invoice PDF and extract all modifiable fields.

    Returns a dict with extracted values for header, recipient, and ship_to.
    """
    pdf_path = str(pdf_path)
    doc = fitz.open(pdf_path)
    page = doc[0]

    # Find the GSTIN in the header to use as anchor
    # The header GSTIN is the large text at top (Block 0)
    blocks = page.get_text("blocks")
    header_block = blocks[0] if blocks else None

    gstin = ""
    if header_block:
        first_line = header_block[4].strip().split("\n")[0].strip()
        # GSTIN is typically 15 chars, alphanumeric
        if len(first_line) == 15 and first_line.isalnum():
            gstin = first_line

    if not gstin:
        doc.close()
        raise ValueError(f"Could not find GSTIN in header of {pdf_path}")

    # Find GSTIN anchors
    anchors = _find_gstin_anchors(page, gstin)
    if len(anchors) < 3:
        logger.warning(
            f"Expected 3 GSTIN anchors, found {len(anchors)} in {pdf_path}. "
            f"Found sections: {list(anchors.keys())}"
        )

    result = {
        "source_file": Path(pdf_path).name,
        "gstin": gstin,
        "header": {"name": "", "name_rect": None},
        "recipient": {"name": "", "address": ""},
        "ship_to": {"name": "", "address": ""},
    }

    # ── Extract Header ────────────────────────────────────
    if "header" in anchors:
        name, rect = _extract_header_name(page, anchors["header"])
        result["header"]["name"] = name
        result["header"]["name_rect"] = rect

    # ── Extract Recipient ─────────────────────────────────
    if "recipient" in anchors:
        fields = _extract_recipient_fields(page, anchors["recipient"])
        result["recipient"]["name"] = fields["name"]["text"]
        result["recipient"]["address"] = fields["address"]["combined_text"]

    # ── Extract Ship To ───────────────────────────────────
    if "ship_to" in anchors:
        fields = _extract_ship_to_fields(page, anchors["ship_to"])
        result["ship_to"]["name"] = fields["name"]["text"]
        result["ship_to"]["address"] = fields["address"]["combined_text"]

    doc.close()
    return result


# ════════════════════════════════════════════════════════════════
#  CONFIG FILE GENERATION
# ════════════════════════════════════════════════════════════════

_CONFIG_TEMPLATE = """# ╔════════════════════════════════════════════════════════════════╗
# ║           GST INVOICE PDF — MODIFICATION CONFIG              ║
# ╠════════════════════════════════════════════════════════════════╣
# ║                                                              ║
# ║   HOW TO USE:                                                ║
# ║   1. Look at the 'original' values (from your PDF)           ║
# ║   2. Type your new values in the 'new' fields                ║
# ║   3. Leave 'new' as "" to keep the original (no change)      ║
# ║   4. Run:  python modify_pdfs.py --apply                    ║
# ║                                                              ║
# ║   Generated from: {source_file:<40s}  ║
# ╚════════════════════════════════════════════════════════════════╝


# Your GSTIN (used to locate your details in every PDF)
gstin: "{gstin}"


# ┌────────────────────────────────────────────────────────────────┐
# │  HEADER  —  The large business name at the top of page 1      │
# └────────────────────────────────────────────────────────────────┘

header_name:
  original: "{header_name}"
  new: ""


# ┌────────────────────────────────────────────────────────────────┐
# │  RECIPIENT  —  Your details in the "Party Details" section     │
# │                (right column, under "Recipient")               │
# └────────────────────────────────────────────────────────────────┘

recipient_name:
  original: "{recipient_name}"
  new: ""

recipient_address:
  original: |
{recipient_address}
  new: ""


# ┌────────────────────────────────────────────────────────────────┐
# │  SHIP TO  —  Your shipping address (right column)             │
# │              under "Ship To" in Party Details                  │
# └────────────────────────────────────────────────────────────────┘

ship_to_name:
  original: "{ship_to_name}"
  new: ""

ship_to_address:
  original: |
{ship_to_address}
  new: ""


# ┌────────────────────────────────────────────────────────────────┐
# │  SETTINGS                                                      │
# └────────────────────────────────────────────────────────────────┘

# Folder containing the original PDFs
input_folder: "downloads"

# Folder where modified PDFs will be saved (originals are NEVER touched)
output_folder: "modified_invoices"
"""


def generate_config_file(sample_pdf_path: str, output_path: str = "pdf_config.yaml"):
    """
    Read a sample GST invoice PDF and generate a user-friendly
    YAML config file pre-filled with the current values.

    The user just needs to fill in the 'new' fields.
    """
    fields = extract_fields_from_pdf(sample_pdf_path)

    # Format multi-line address with proper YAML indentation
    def indent_address(addr_text):
        if not addr_text:
            return "    (no address found)"
        lines = addr_text.split("\n")
        return "\n".join(f"    {line}" for line in lines)

    config_text = _CONFIG_TEMPLATE.format(
        source_file=fields["source_file"],
        gstin=fields["gstin"],
        header_name=fields["header"]["name"],
        recipient_name=fields["recipient"]["name"],
        recipient_address=indent_address(fields["recipient"]["address"]),
        ship_to_name=fields["ship_to"]["name"],
        ship_to_address=indent_address(fields["ship_to"]["address"]),
    )

    Path(output_path).write_text(config_text, encoding="utf-8")
    print(f"  ✓ Config file created: {output_path}")
    print(f"  ✓ Source PDF: {fields['source_file']}")
    print(f"  ✓ GSTIN: {fields['gstin']}")
    print(f"  ✓ Header name: {fields['header']['name']}")
    print()
    print(f"  → Now edit '{output_path}' and fill in the 'new' values.")
    print(f"  → Then run: python modify_pdfs.py --apply")

    return output_path


# ════════════════════════════════════════════════════════════════
#  PDF MODIFICATION
# ════════════════════════════════════════════════════════════════

def _load_config(config_path: str) -> dict:
    """Load and validate the YAML config file."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if not cfg:
        raise ValueError(f"Config file is empty: {config_path}")
    if "gstin" not in cfg:
        raise ValueError(f"Config file must contain 'gstin' field: {config_path}")

    return cfg


def _get_replacement(cfg_section: dict) -> str | None:
    """
    Get the 'new' value from a config section.
    Returns None if 'new' is empty/missing (means keep original).
    """
    if not cfg_section or not isinstance(cfg_section, dict):
        return None
    new_val = cfg_section.get("new", "")
    if isinstance(new_val, str) and new_val.strip():
        return new_val.strip()
    return None


def _redact_rect(page, rect, h_padding=2, v_padding=0):
    """
    Add a white redaction annotation over the given rect.

    Uses horizontal-only padding by default to avoid bleeding
    into adjacent text lines (GST invoice lines are ~11.7px tall).
    """
    padded = fitz.Rect(
        rect.x0 - h_padding,
        rect.y0 - v_padding,
        rect.x1 + h_padding,
        rect.y1 + v_padding,
    )
    annot = page.add_redact_annot(padded)
    annot.set_colors(fill=(1, 1, 1))  # white fill
    annot.update()


def _redact_and_replace_text(page, search_text, new_text, region_y_min, region_y_max,
                              region_x_min=0, fontsize=PARTY_FONT_SIZE):
    """
    Find text on the page within a y-region, redact it, and insert new text.
    For single-line text fields (name, etc.).
    """
    instances = page.search_for(search_text)
    target_rects = [
        r for r in instances
        if region_y_min <= r.y0 <= region_y_max and r.x0 >= region_x_min
    ]

    if not target_rects:
        return False

    for rect in target_rects:
        _redact_rect(page, rect)

    page.apply_redactions()

    # Insert new text at the first matching position
    for rect in target_rects:
        page.insert_text(
            fitz.Point(rect.x0, rect.y1 - 2),  # baseline position
            new_text,
            fontsize=fontsize,
            fontname=FONT_NAME,
            color=FONT_COLOR,
        )

    return True


def _redact_and_replace_multiline(page, original_lines, new_text,
                                   region_y_min, region_y_max,
                                   region_x_min=280, fontsize=PARTY_FONT_SIZE):
    """
    Find and replace multi-line text (addresses).
    Searches for each line, redacts them all, then inserts
    the new multi-line text as a textbox.
    """
    all_rects = []

    for line in original_lines:
        line = line.strip()
        if not line:
            continue
        instances = page.search_for(line)
        matched = [
            r for r in instances
            if region_y_min <= r.y0 <= region_y_max and r.x0 >= region_x_min
        ]
        all_rects.extend(matched)

    if not all_rects:
        return False

    # Redact all matched rects
    for rect in all_rects:
        _redact_rect(page, rect)

    page.apply_redactions()

    # Compute the bounding box of all redacted lines
    combined_x0 = min(r.x0 for r in all_rects)
    combined_y0 = min(r.y0 for r in all_rects)
    combined_x1 = max(r.x1 for r in all_rects)
    combined_y1 = max(r.y1 for r in all_rects)

    # Insert the new text as a textbox in the same region
    text_rect = fitz.Rect(combined_x0, combined_y0, combined_x1 + 30, combined_y1 + 5)
    page.insert_textbox(
        text_rect,
        new_text,
        fontsize=fontsize,
        fontname=FONT_NAME,
        color=FONT_COLOR,
        align=fitz.TEXT_ALIGN_LEFT,
    )

    return True


def modify_single_pdf(input_path: str, output_path: str, config: dict) -> dict:
    """
    Apply the config modifications to a single GST invoice PDF.

    Uses a 3-phase approach to avoid cascading corruption:
      Phase 1: Extract ALL target rects from the original, unmodified page
      Phase 2: Apply ALL redactions at once
      Phase 3: Insert ALL new text

    Returns a result dict: {"file": ..., "status": "ok"/"error", "changes": [...]}
    """
    input_path = str(input_path)
    output_path = str(output_path)
    result = {"file": Path(input_path).name, "status": "ok", "changes": [], "errors": []}

    try:
        doc = fitz.open(input_path)
        page = doc[0]
        gstin = config["gstin"]

        # Find the 3 GSTIN anchors
        anchors = _find_gstin_anchors(page, gstin)
        if not anchors:
            result["status"] = "error"
            result["errors"].append(f"GSTIN '{gstin}' not found in PDF")
            doc.close()
            return result

        # ══════════════════════════════════════════════════
        # PHASE 1: Extract all target rects from ORIGINAL page
        # ══════════════════════════════════════════════════
        # Each job: (redact_rects, insert_point_or_rect, new_text, fontsize, is_textbox, change_msg)
        jobs = []

        # ── Header Name ──────────────────────────────
        new_header = _get_replacement(config.get("header_name"))
        if new_header and "header" in anchors:
            name_text, name_rect = _extract_header_name(page, anchors["header"])
            if name_rect:
                jobs.append({
                    "redact_rects": [name_rect],
                    "insert_pos": fitz.Point(name_rect.x0, name_rect.y1 - 2),
                    "text": new_header,
                    "fontsize": HEADER_FONT_SIZE,
                    "is_textbox": False,
                    "change_msg": f"Header name: '{name_text}' -> '{new_header}'",
                })
            else:
                result["errors"].append("Could not locate header name rect")

        # ── Recipient Name + Address ─────────────────
        if "recipient" in anchors:
            recip_fields = _extract_recipient_fields(page, anchors["recipient"])

            new_recip_name = _get_replacement(config.get("recipient_name"))
            if new_recip_name:
                name_rect = recip_fields["name"]["rect"]
                old_name = recip_fields["name"]["text"]
                if name_rect:
                    jobs.append({
                        "redact_rects": [name_rect],
                        "insert_pos": fitz.Point(name_rect.x0, name_rect.y1 - 2),
                        "text": new_recip_name,
                        "fontsize": PARTY_FONT_SIZE,
                        "is_textbox": False,
                        "change_msg": f"Recipient name: '{old_name}' -> '{new_recip_name}'",
                    })

            new_recip_addr = _get_replacement(config.get("recipient_address"))
            if new_recip_addr:
                addr_rects = recip_fields["address"]["rects"]
                old_addr_lines = recip_fields["address"]["lines"]
                if addr_rects:
                    combined = fitz.Rect(
                        min(r.x0 for r in addr_rects),
                        min(r.y0 for r in addr_rects),
                        max(r.x1 for r in addr_rects) + 30,
                        max(r.y1 for r in addr_rects) + 5,
                    )
                    jobs.append({
                        "redact_rects": list(addr_rects),
                        "insert_pos": combined,
                        "text": new_recip_addr,
                        "fontsize": PARTY_FONT_SIZE,
                        "is_textbox": True,
                        "change_msg": f"Recipient address: '{' / '.join(old_addr_lines)}' -> '{new_recip_addr}'",
                    })

        # ── Ship To Name + Address ───────────────────
        if "ship_to" in anchors:
            ship_fields = _extract_ship_to_fields(page, anchors["ship_to"])

            new_ship_name = _get_replacement(config.get("ship_to_name"))
            if new_ship_name:
                name_rect = ship_fields["name"]["rect"]
                old_name = ship_fields["name"]["text"]
                if name_rect:
                    jobs.append({
                        "redact_rects": [name_rect],
                        "insert_pos": fitz.Point(name_rect.x0, name_rect.y1 - 2),
                        "text": new_ship_name,
                        "fontsize": PARTY_FONT_SIZE,
                        "is_textbox": False,
                        "change_msg": f"Ship-To name: '{old_name}' -> '{new_ship_name}'",
                    })

            new_ship_addr = _get_replacement(config.get("ship_to_address"))
            if new_ship_addr:
                addr_rects = ship_fields["address"]["rects"]
                old_addr_lines = ship_fields["address"]["lines"]
                if addr_rects:
                    combined = fitz.Rect(
                        min(r.x0 for r in addr_rects),
                        min(r.y0 for r in addr_rects),
                        max(r.x1 for r in addr_rects) + 30,
                        max(r.y1 for r in addr_rects) + 5,
                    )
                    jobs.append({
                        "redact_rects": list(addr_rects),
                        "insert_pos": combined,
                        "text": new_ship_addr,
                        "fontsize": PARTY_FONT_SIZE,
                        "is_textbox": True,
                        "change_msg": f"Ship-To address: '{' / '.join(old_addr_lines)}' -> '{new_ship_addr}'",
                    })

        # ══════════════════════════════════════════════════
        # PHASE 2: Apply ALL redactions at once
        # ══════════════════════════════════════════════════
        for job in jobs:
            for rect in job["redact_rects"]:
                _redact_rect(page, rect)

        page.apply_redactions()  # single apply_redactions call

        # ══════════════════════════════════════════════════
        # PHASE 3: Insert ALL new text
        # ══════════════════════════════════════════════════
        for job in jobs:
            if job["is_textbox"]:
                # The text might have multiple lines (\n).
                # Insert them line by line to avoid insert_textbox bounding box limitations.
                lines = str(job["text"]).split("\n")
                
                # job["insert_pos"] is a Rect for textboxes. We start at its top-left.
                x = job["insert_pos"].x0
                # Start y slightly below the top of the rect (baseline)
                y = job["insert_pos"].y0 + job["fontsize"] - 2
                
                for line_text in lines:
                    line_text = line_text.strip()
                    if line_text:
                        page.insert_text(
                            fitz.Point(x, y),
                            line_text,
                            fontsize=job["fontsize"],
                            fontname=FONT_NAME,
                            color=FONT_COLOR,
                        )
                    y += job["fontsize"] + 1.5  # line spacing
            else:
                page.insert_text(
                    job["insert_pos"],
                    job["text"],
                    fontsize=job["fontsize"],
                    fontname=FONT_NAME,
                    color=FONT_COLOR,
                )
            result["changes"].append(job["change_msg"])

        # ── Save ──────────────────────────────────────────
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path)
        doc.close()

        if not result["changes"]:
            result["status"] = "skipped"

    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result

    return result


# ════════════════════════════════════════════════════════════════
#  BATCH PROCESSING
# ════════════════════════════════════════════════════════════════

def batch_modify(input_dir: str, output_dir: str, config_path: str) -> list[dict]:
    """
    Apply PDF modifications to ALL PDFs in input_dir.
    Saves modified copies to output_dir. Never touches originals.

    Returns a list of result dicts for each file.
    """
    config = _load_config(config_path)
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use config-level folder overrides if present
    if "input_folder" in config and config["input_folder"]:
        input_dir = Path(config["input_folder"])
    if "output_folder" in config and config["output_folder"]:
        output_dir = Path(config["output_folder"])
        output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"  No PDF files found in {input_dir}")
        return []

    print(f"\n{'=' * 62}")
    print(f"  GST Invoice PDF Modifier")
    print(f"{'=' * 62}")
    print(f"  Input folder  : {input_dir}")
    print(f"  Output folder : {output_dir}")
    print(f"  PDFs found    : {len(pdf_files)}")
    print(f"  Config        : {config_path}")
    print(f"{'=' * 62}\n")

    results = []
    ok_count = 0
    err_count = 0
    skip_count = 0

    for i, pdf_path in enumerate(pdf_files, 1):
        out_path = output_dir / pdf_path.name
        print(f"  [{i}/{len(pdf_files)}] {pdf_path.name} ... ", end="", flush=True)

        result = modify_single_pdf(pdf_path, out_path, config)
        results.append(result)

        if result["status"] == "ok":
            ok_count += 1
            changes_summary = ", ".join(result["changes"][:2])
            if len(result["changes"]) > 2:
                changes_summary += f" (+{len(result['changes'])-2} more)"
            print(f"✓  {changes_summary}")
        elif result["status"] == "skipped":
            skip_count += 1
            print("—  no changes needed")
        else:
            err_count += 1
            print(f"✗  {'; '.join(result['errors'])}")

    # ── Summary ───────────────────────────────────────────
    print(f"\n{'=' * 62}")
    print(f"  COMPLETE")
    print(f"{'=' * 62}")
    print(f"  Modified : {ok_count}")
    print(f"  Skipped  : {skip_count}")
    print(f"  Errors   : {err_count}")
    print(f"  Output   : {output_dir.resolve()}")
    print(f"{'=' * 62}\n")

    # ── Write audit log ───────────────────────────────────
    try:
        import pandas as pd
        log_path = output_dir / "modification_results.xlsx"
        rows = []
        for r in results:
            rows.append({
                "File": r["file"],
                "Status": r["status"],
                "Changes": "; ".join(r.get("changes", [])),
                "Errors": "; ".join(r.get("errors", [])),
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        pd.DataFrame(rows).to_excel(str(log_path), index=False)
        print(f"  Audit log: {log_path}")
    except ImportError:
        # pandas not available — write CSV instead
        import csv
        log_path = output_dir / "modification_results.csv"
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["File", "Status", "Changes", "Errors", "Timestamp"])
            writer.writeheader()
            for r in results:
                writer.writerow({
                    "File": r["file"],
                    "Status": r["status"],
                    "Changes": "; ".join(r.get("changes", [])),
                    "Errors": "; ".join(r.get("errors", [])),
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
        print(f"  Audit log: {log_path}")

    return results
