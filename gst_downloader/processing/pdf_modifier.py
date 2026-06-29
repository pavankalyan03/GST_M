"""
PDF Modifier — Clean Line-Level Approach
==========================================

Modifies GST Invoice PDFs by:
1. Locating GSTIN anchors (header, recipient, ship_to) via positional sorting
2. Extracting individual field text with precise per-line bounding rects
3. Redacting only the specific lines that need replacing (no block-level wipes)
4. Inserting replacement text at exact original coordinates

This avoids white-rectangle overlays, preserving table borders, grid lines,
and keeping file sizes close to the original.
"""

import fitz  # PyMuPDF
import yaml
import csv
import traceback
import random
from pathlib import Path

# ════════════════════════════════════════════════════════════════
#  STYLING CONSTANTS
# ════════════════════════════════════════════════════════════════

FONT_NAME = "helv"
HEADER_FONT_SIZE = 22
PARTY_FONT_SIZE = 9
FONT_COLOR = (0, 0, 0)
# The actual font file shipped with the project
from gst_downloader import config

HEADER_CUSTOM_FONT_FILE = str(config.BASE_DIR / "assets" / "helvmn.ttf")
HEADER_CUSTOM_FONT_NAME = "helv-med"

# ════════════════════════════════════════════════════════════════
#  ANCHOR DETECTION
# ════════════════════════════════════════════════════════════════

def _find_gstin_anchors(page, gstin: str) -> dict:
    """
    Find the GSTIN occurrences on page 1 and classify them
    as 'header', 'recipient', or 'ship_to' based on y-position.

    The GST portal sometimes fragments the GSTIN text across multiple
    bounding boxes on the same line, so we group hits by y-coordinate
    and merge them into single anchor rects.
    """
    hits = page.search_for(gstin)
    if not hits:
        return {}

    # Group hits by y0 to handle fragmented text boxes
    grouped_hits = []
    for rect in hits:
        matched = False
        for group in grouped_hits:
            if abs(group[0].y0 - rect.y0) < 5:
                group.append(rect)
                matched = True
                break
        if not matched:
            grouped_hits.append([rect])

    # Combine rects in each group to form a single anchor rect
    combined_hits = []
    for group in grouped_hits:
        combined = fitz.Rect(group[0])
        for r in group[1:]:
            combined = combined | r
        combined_hits.append(combined)

    hits_sorted = sorted(combined_hits, key=lambda r: r.y0)
    anchors = {}
    labels = ['header', 'recipient', 'ship_to']
    for i, rect in enumerate(hits_sorted):
        if i < len(labels):
            anchors[labels[i]] = rect

    return anchors


# ════════════════════════════════════════════════════════════════
#  TEXT EXTRACTION — Per-line with bounding rects
# ════════════════════════════════════════════════════════════════

def _extract_text_below_anchor(page, anchor_rect, max_lines=15, column="full"):
    """
    Extract text lines below an anchor rect, returning each line's text
    and its precise bounding rectangle.

    column='full': keep all lines regardless of x-position
    column='right': keep lines starting at x >= anchor.x0 - 20
    column='left': keep lines starting at x < anchor.x0 - 20
    """
    search_rect = fitz.Rect(
        0,
        anchor_rect.y1,
        600,
        anchor_rect.y1 + 250
    )

    words = page.get_text("words", clip=search_rect)
    if not words:
        return []

    # Filter words by column BEFORE grouping into lines
    column_words = []
    # Use the physical page midpoint for a reliable column split
    page_midpoint = page.rect.width / 2
    for w in words:
        if column == "right" and w[0] < page_midpoint - 20:
            continue
        if column == "left" and w[0] >= page_midpoint - 20:
            continue
        column_words.append(w)

    # Group words into lines by y-coordinate proximity
    lines = []
    current_line_words = []
    current_y = None

    for w in sorted(column_words, key=lambda w: (w[1], w[0])):
        if current_y is None:
            current_y = w[1]

        if abs(w[1] - current_y) > 4:
            if current_line_words:
                lines.append(current_line_words)
            current_line_words = [w]
            current_y = w[1]
        else:
            current_line_words.append(w)

    if current_line_words:
        lines.append(current_line_words)

    lines = lines[:max_lines]
    result = []
    for line_words in lines:
        line_words.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in line_words)
        lx0 = min(w[0] for w in line_words)
        ly0 = min(w[1] for w in line_words)
        lx1 = max(w[2] for w in line_words)
        ly1 = max(w[3] for w in line_words)
        result.append((text, fitz.Rect(lx0, ly0, lx1, ly1)))

    return result


def _extract_header_name(page, anchor_rect):
    """Extract the business name from the header (the line(s) below GSTIN)."""
    lines = _extract_text_below_anchor(page, anchor_rect, max_lines=3, column="full")
    name_parts = []
    name_rects = []
    for text, rect in lines:
        if text.startswith("1.") or "Invoice" in text:
            break
        name_parts.append(text)
        name_rects.append(rect)

    if name_parts and name_rects:
        full_name = " ".join(name_parts)
        combined_rect = fitz.Rect(name_rects[0])
        for r in name_rects[1:]:
            combined_rect = combined_rect | r
        return full_name, combined_rect, fitz.Rect(name_rects[0])
    return "", None, None


def _extract_section_fields(page, anchor_rect, column="right"):
    """
    Extract name and address lines from a Party Details section (Recipient or Ship-To).

    Returns a dict with:
        - name: {"text": str, "rect": Rect}
        - address: {"lines": [str], "rects": [Rect], "combined_text": str}
    """
    lines = _extract_text_below_anchor(page, anchor_rect, max_lines=15, column=column)
    fields = {
        "name": {"text": "", "rect": None},
        "address": {"lines": [], "rects": [], "combined_text": ""},
    }

    if not lines:
        return fields

    # First line is the name
    fields["name"]["text"] = lines[0][0]
    fields["name"]["rect"] = lines[0][1]

    # Subsequent lines are address, stopping at section boundaries
    section_labels = {"Ship To", "Dispatch From", "Supplier", "Recipient"}

    for text, rect in lines[1:]:
        stripped = text.strip()
        if stripped in section_labels:
            break
        fields["address"]["lines"].append(text)
        fields["address"]["rects"].append(rect)
        if stripped.startswith("State:"):
            break

    if fields["address"]["lines"]:
        fields["address"]["combined_text"] = "\n".join(fields["address"]["lines"])

    return fields


def extract_fields_from_pdf(pdf_path: str) -> dict:
    """Extract all relevant fields from a pristine PDF for config generation."""
    doc = fitz.open(pdf_path)
    page = doc[0]

    # Find the GSTIN to use as anchor
    text = page.get_text()
    gstin = None
    for line in text.split('\n'):
        if line.startswith("GSTIN: "):
            parts = line.split()
            if len(parts) > 1 and len(parts[1]) == 15:
                gstin = parts[1]
                break
        elif len(line.strip()) == 15 and line.strip().isalnum():
            gstin = line.strip()
            break

    if not gstin:
        doc.close()
        raise ValueError(f"Could not auto-detect a 15-character GSTIN in {pdf_path}")

    anchors = _find_gstin_anchors(page, gstin)

    result = {
        "source_file": Path(pdf_path).name,
        "gstin": gstin,
        "header": {"name": ""},
        "recipient": {"name": "", "address": ""},
        "ship_to": {"name": "", "address": ""},
    }

    if "header" in anchors:
        name, _, _ = _extract_header_name(page, anchors["header"])
        result["header"]["name"] = name

    if "recipient" in anchors:
        fields = _extract_section_fields(page, anchors["recipient"], "right")
        result["recipient"]["name"] = fields["name"]["text"]
        result["recipient"]["address"] = fields["address"]["combined_text"]

    if "ship_to" in anchors:
        fields = _extract_section_fields(page, anchors["ship_to"], "right")
        result["ship_to"]["name"] = fields["name"]["text"]
        result["ship_to"]["address"] = fields["address"]["combined_text"]

    doc.close()
    return result


# ════════════════════════════════════════════════════════════════
#  CONFIG GENERATION
# ════════════════════════════════════════════════════════════════

_CONFIG_TEMPLATE = """
# ╔════════════════════════════════════════════════════════════════╗
# ║           GST INVOICE PDF — MODIFICATION CONFIG                ║
# ╠════════════════════════════════════════════════════════════════╣
# ║                                                                ║
# ║   HOW TO USE:                                                  ║
# ║   1. Look at the 'original' values (from your PDF)             ║
# ║   2. Type your new values in the 'new' fields                  ║
# ║   3. Leave 'new' as "" to keep the original (no change)        ║
# ║   4. Run:  python modify_pdfs.py --apply                       ║
# ║                                                                ║
# ║   Generated from: {source_file:<30}                            ║
# ╚════════════════════════════════════════════════════════════════╝


# Your GSTIN (used to locate your details in every PDF)
gstin: "{gstin}"


# ┌────────────────────────────────────────────────────────────────┐
# │  HEADER  —  The large business name at the top of page 1       │
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

# Folder containing the original PDFs (backup copies)
original_folder: ""

# Folder where modified PDFs will be saved
processed_folder: ""

# Folder where the processed/cleaned Excel files will be saved
processed_excel_folder: ""

# Add subtle invisible noise to the top-right corner (helps mask automated edits)
add_subtle_noise: false
"""


def generate_config_file(sample_pdf_path: str, output_path: str = "pdf_config.yaml"):
    """Generate a YAML config file from a sample PDF's extracted fields."""
    fields = extract_fields_from_pdf(sample_pdf_path)

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
#  CONFIG LOADING & HELPERS
# ════════════════════════════════════════════════════════════════

def _load_config(config_path: str) -> dict:
    """Load and validate the YAML config file."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not cfg:
        raise ValueError(f"Config file is empty: {config_path}")
    if "gstin" not in cfg:
        raise ValueError(f"Config file must contain 'gstin' field: {config_path}")
    
    # Ensure no defaults are relied upon for directories
    for required_dir in ["original_folder", "processed_folder", "processed_excel_folder"]:
        if required_dir not in cfg or not str(cfg.get(required_dir)).strip():
            raise ValueError(f"Config file must contain a valid '{required_dir}'")
            
    return cfg


def _get_replacement(cfg_section: dict | str) -> str | None:
    """Get the 'new' replacement value from a config section, or None if empty."""
    if not cfg_section:
        return None
        
    if isinstance(cfg_section, dict):
        new_val = cfg_section.get("new", "")
    elif isinstance(cfg_section, str):
        new_val = cfg_section
    else:
        return None
        
    if isinstance(new_val, str) and new_val.strip():
        return new_val.strip()
    return None


# ════════════════════════════════════════════════════════════════
#  REDACTION — Per-line, tight-fit rectangles
# ════════════════════════════════════════════════════════════════

def _redact_rect(page, rect, h_padding=2, v_padding=1):
    """
    Add a redaction annotation over a single text line's bounding rect.
    Uses minimal padding to avoid touching adjacent content.
    """
    padded = fitz.Rect(
        rect.x0 - h_padding,
        rect.y0 - v_padding,
        rect.x1 + h_padding,
        rect.y1 + v_padding,
    )
    annot = page.add_redact_annot(padded)
    annot.set_colors(fill=(1, 1, 1))  # white fill — just the line area
    annot.update()


# ════════════════════════════════════════════════════════════════
#  NOISE & WATERMARKING
# ════════════════════════════════════════════════════════════════
def _add_subtle_noise_to_top_right(page):
    """Add small visible black noise squares (pixels) to a specific area without bloating file size."""
    page_rect = page.rect

    # Square size (80 x 80 points)
    square_size = 80

    # Margin from the page edges
    right_margin = 55
    top_margin = 55

    # Define square at top-right
    noise_area = fitz.Rect(
        page_rect.width - right_margin - square_size,
        top_margin,
        page_rect.width - right_margin,
        top_margin + square_size
    )

    width = int(noise_area.width)
    height = int(noise_area.height)

    # Batch all black pixels into a single Shape object
    shape_black = page.new_shape()

    # Generate sparse visible black square pixels
    for _ in range(400):
        x = random.randint(0, max(1, width - 1))
        y = random.randint(0, max(1, height - 1))

        px = noise_area.x0 + x
        py = noise_area.y0 + y

        pixel_rect = fitz.Rect(px, py, px + 1.5, py + 1.5)
        shape_black.draw_rect(pixel_rect)

    # Commit all black pixels as a single PDF object
    shape_black.finish(
        color=(0.0, 0.0, 0.0),
        fill=(0.0, 0.0, 0.0),
        width=0.2,
        fill_opacity=1.0,
        stroke_opacity=1.0,
    )
    shape_black.commit()



# ════════════════════════════════════════════════════════════════
#  PDF MODIFICATION ENGINE — Per-line redact & insert
# ════════════════════════════════════════════════════════════════

def modify_single_pdf(input_path: str, output_path: str, config: dict) -> dict:
    """
    Modify a single GST Invoice PDF according to the config.

    Strategy:
      Phase 1 — Extract all text and rects from the untouched page
      Phase 2 — Add per-line redaction annotations for lines that need changing
      Phase 3 — Apply all redactions at once
      Phase 4 — Insert replacement text at original positions

    This approach keeps redaction rectangles tight to each line,
    preserving table borders, cell backgrounds, and adjacent content.
    """
    input_path = str(input_path)
    output_path = str(output_path)
    result = {"file": Path(input_path).name, "status": "ok", "changes": [], "errors": []}

    try:
        doc = fitz.open(input_path)

        # Validate: check for encryption/password protection
        if doc.is_encrypted:
            result["status"] = "error"
            result["errors"].append(f"PDF is password-protected: {Path(input_path).name}")
            doc.close()
            return result

        if len(doc) == 0:
            result["status"] = "error"
            result["errors"].append(f"PDF has no pages: {Path(input_path).name}")
            doc.close()
            return result

        page = doc[0]
        gstin = config["gstin"]

        anchors = _find_gstin_anchors(page, gstin)
        if not anchors:
            result["status"] = "error"
            result["errors"].append(f"GSTIN '{gstin}' not found in PDF")
            doc.close()
            return result

        # =========================================================
        # PHASE 1: Extract all targets from the untouched page
        # =========================================================
        jobs = []

        # ── Header Name ──────────────────────────────────────
        header_font_to_use = FONT_NAME
        if Path(HEADER_CUSTOM_FONT_FILE).exists():
            header_font_to_use = HEADER_CUSTOM_FONT_NAME

        new_header = _get_replacement(config.get("header_name"))
        if new_header and "header" in anchors:
            name_text, name_rect, first_line_rect = _extract_header_name(page, anchors["header"])
            if name_rect:
                jobs.append({
                    "redact_rects": [name_rect],
                    "insert_pos": fitz.Point(first_line_rect.x0, first_line_rect.y1 - 2),
                    "text": new_header,
                    "fontsize": HEADER_FONT_SIZE,
                    "fontname": header_font_to_use,
                    "is_multiline": False,
                    "change_msg": f"Header name: '{name_text}' -> '{new_header}'",
                })
            else:
                result["errors"].append("Could not locate header name rect")

        # ── Recipient Fields ─────────────────────────────────
        if "recipient" in anchors:
            recip_fields = _extract_section_fields(page, anchors["recipient"], "right")

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
                        "fontname": FONT_NAME,
                        "is_multiline": False,
                        "change_msg": f"Recipient name: '{old_name}' -> '{new_recip_name}'",
                    })

            new_recip_addr = _get_replacement(config.get("recipient_address"))
            if new_recip_addr:
                addr_rects = recip_fields["address"]["rects"]
                old_addr_lines = recip_fields["address"]["lines"]
                if addr_rects:
                    # Compute the combined bounding box for insertion positioning
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
                        "fontname": FONT_NAME,
                        "is_multiline": True,
                        "change_msg": f"Recipient address: '{' / '.join(old_addr_lines)}' -> '{new_recip_addr[:50]}...'",
                    })

        # ── Ship-To Fields ───────────────────────────────────
        if "ship_to" in anchors:
            ship_fields = _extract_section_fields(page, anchors["ship_to"], "right")

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
                        "fontname": FONT_NAME,
                        "is_multiline": False,
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
                        "fontname": FONT_NAME,
                        "is_multiline": True,
                        "change_msg": f"Ship-To address: '{' / '.join(old_addr_lines)}' -> '{new_ship_addr[:50]}...'",
                    })

        if not jobs:
            result["status"] = "skipped"
            doc.close()
            return result

        # =========================================================
        # PHASE 2: Add redaction annotations (per-line, tight-fit)
        # =========================================================
        for job in jobs:
            for rect in job["redact_rects"]:
                _redact_rect(page, rect)

        # =========================================================
        # PHASE 3: Apply all redactions at once
        # =========================================================
        page.apply_redactions()

        # =========================================================
        # PHASE 4: Insert replacement text
        # =========================================================
        if Path(HEADER_CUSTOM_FONT_FILE).exists():
            page.insert_font(fontname=HEADER_CUSTOM_FONT_NAME, fontfile=HEADER_CUSTOM_FONT_FILE)

        for job in jobs:
            if job["is_multiline"]:
                # Line-by-line rendering for multiline address blocks
                lines = str(job["text"]).split("\n")
                insert_rect = job["insert_pos"]
                x = insert_rect.x0
                y = insert_rect.y0 + job["fontsize"] - 2

                for line_text in lines:
                    line_text = line_text.strip()
                    if line_text:
                        page.insert_text(
                            fitz.Point(x, y),
                            line_text,
                            fontsize=job["fontsize"],
                            fontname=job.get("fontname", FONT_NAME),
                            color=FONT_COLOR,
                        )
                    y += job["fontsize"] + 1.5
            else:
                # Single-line text (header name, recipient name, etc.)
                page.insert_text(
                    job["insert_pos"],
                    job["text"],
                    fontsize=job["fontsize"],
                    fontname=job.get("fontname", FONT_NAME),
                    color=FONT_COLOR,
                )
            result["changes"].append(job["change_msg"])

        # =========================================================
        # PHASE 5: Add subtle noise (if configured)
        # =========================================================
        if config.get("add_subtle_noise", False):
            try:
                _add_subtle_noise_to_top_right(page)
                result["changes"].append("Subtle noise added to top right corner")
            except Exception as e:
                result["errors"].append(f"Noise addition failed: {str(e)}")

        # Save modified PDF
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path, garbage=4, deflate=True)
        doc.close()

    except fitz.FileDataError as exc:
        result["status"] = "error"
        result["errors"].append(f"Corrupt or unreadable PDF: {exc}")
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(traceback.format_exc())

    return result


# ════════════════════════════════════════════════════════════════
#  BATCH PROCESSING (CLI usage)
# ════════════════════════════════════════════════════════════════

def batch_modify(input_dir: str, output_dir: str, config_path: str):
    """Modify all PDFs in input_dir and save to output_dir."""
    print(f"\n{'='*62}")
    print(f"  GST Invoice PDF Modifier")
    print(f"{'='*62}")

    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(in_path.glob("*.pdf"))
    if not pdf_files:
        print(f"  No PDFs found in {input_dir}")
        return

    config = _load_config(config_path)

    print(f"  Input folder  : {input_dir}")
    print(f"  Output folder : {output_dir}")
    print(f"  PDFs found    : {len(pdf_files)}")
    print(f"  Config        : {config_path}")
    print(f"{'='*62}\n")

    results = []

    for i, pdf in enumerate(pdf_files, 1):
        out_pdf = out_path / pdf.name
        res = modify_single_pdf(str(pdf), str(out_pdf), config)
        results.append(res)

        status_mark = "✓" if res["status"] == "ok" else ("-" if res["status"] == "skipped" else "✗")
        msg = ""
        if res["changes"]:
            msg = res["changes"][0]
            if len(res["changes"]) > 1:
                msg += f" (+{len(res['changes'])-1} more)"
        elif res["errors"]:
            msg = f"ERROR: {res['errors'][0][:80]}"

        print(f"  [{i}/{len(pdf_files)}] {pdf.name} ... {status_mark}  {msg}")

    print(f"\n{'='*62}")
    print(f"  COMPLETE")
    print(f"{'='*62}")
    print(f"  Modified : {sum(1 for r in results if r['status'] == 'ok')}")
    print(f"  Skipped  : {sum(1 for r in results if r['status'] == 'skipped')}")
    print(f"  Errors   : {sum(1 for r in results if r['status'] == 'error')}")
    print(f"  Output   : {out_path.absolute()}")
    print(f"{'='*62}\n")

    log_path = out_path / "modification_results.csv"
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "status", "changes", "errors"])
        for r in results:
            writer.writerow([r["file"], r["status"], " | ".join(r["changes"]), " | ".join(r["errors"])])

    print(f"  Audit log: {log_path.absolute()}\n")
