"""Verify modified PDFs — compare original vs modified text."""
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import fitz
from pathlib import Path

# Check one original vs modified
for fname in ['BOM7-125914.pdf', 'BOM7-125915.pdf', 'CJB1-168046.pdf']:
    print(f"\n{'=' * 60}")
    print(f"  {fname}")
    print(f"{'=' * 60}")
    
    # Original
    doc_orig = fitz.open(f'downloads/{fname}')
    page_orig = doc_orig[0]
    
    # Modified
    doc_mod = fitz.open(f'modified_invoices/{fname}')
    page_mod = doc_mod[0]
    
    # Check header region (top 150px)
    orig_header = page_orig.get_text(clip=fitz.Rect(0, 0, 600, 150)).strip()
    mod_header = page_mod.get_text(clip=fitz.Rect(0, 0, 600, 150)).strip()
    
    print(f"\n  HEADER (original): {orig_header[:80]}")
    print(f"  HEADER (modified): {mod_header[:80]}")
    
    # Check recipient region
    orig_recip = page_orig.get_text(clip=fitz.Rect(280, 420, 550, 520)).strip()
    mod_recip = page_mod.get_text(clip=fitz.Rect(280, 420, 550, 520)).strip()
    
    print(f"\n  RECIPIENT (original):")
    for line in orig_recip.split('\n')[:5]:
        print(f"    {line}")
    print(f"  RECIPIENT (modified):")
    for line in mod_recip.split('\n')[:5]:
        print(f"    {line}")
    
    # Check ship-to region
    orig_ship = page_orig.get_text(clip=fitz.Rect(280, 520, 550, 640)).strip()
    mod_ship = page_mod.get_text(clip=fitz.Rect(280, 520, 550, 640)).strip()
    
    print(f"\n  SHIP-TO (original):")
    for line in orig_ship.split('\n')[:5]:
        print(f"    {line}")
    print(f"  SHIP-TO (modified):")
    for line in mod_ship.split('\n')[:5]:
        print(f"    {line}")
    
    # Check that supplier info is UNCHANGED
    orig_supplier = page_orig.get_text(clip=fitz.Rect(20, 420, 280, 520)).strip()
    mod_supplier = page_mod.get_text(clip=fitz.Rect(20, 420, 280, 520)).strip()
    supplier_unchanged = orig_supplier == mod_supplier
    print(f"\n  Supplier unchanged: {'YES' if supplier_unchanged else 'NO - WARNING!'}")
    
    # Check that page 2 (items table) is UNCHANGED
    orig_p2 = page_orig.get_text() if len(doc_orig) == 1 else doc_orig[1].get_text()
    mod_p2 = doc_mod[1].get_text() if len(doc_mod) > 1 else ""
    items_unchanged = orig_p2.strip() == mod_p2.strip()
    print(f"  Items table unchanged: {'YES' if items_unchanged else 'NO - WARNING!'}")
    
    doc_orig.close()
    doc_mod.close()
