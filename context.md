# Project Context & Architecture: GST Invoice Modifier

## 1. What Claude Implemented So Far
Before my session began, Claude set up the foundation for the PDF modification feature:
- Created the initial `gst_downloader/pdf_modifier.py` script.
- Implemented the strategy of using the 15-character GSTIN as a geographic "anchor" to reliably find the positions of the Header, Recipient, and Ship-To sections.
- Set up PyMuPDF (`fitz`) to extract text based on these geographic boundaries.
- Wrote the `generate_config_file` logic to read an original PDF and produce a user-friendly `pdf_config.yaml` template where the user can specify the "new" replacement values.
- Built the initial batch modification logic using `page.add_redact_annot` to white out old text and `page.insert_textbox` to overlay new text.

## 2. Understanding of Existing Architecture & Workflow
The core architecture consists of:
1. **The Core App (`gst_downloader`)**: Currently handles downloading and parsing invoices (which was built earlier).
2. **The PDF Modifier Module (`pdf_modifier.py`)**: A standalone feature that processes the downloaded PDFs. It operates in two steps:
   - **Step 1 (Generate Config)**: Reads a sample PDF, locates the dynamic party details using the GSTIN anchors, extracts them, and generates a YAML config file.
   - **Step 2 (Apply)**: Reads the user's filled-out YAML config and loops through all PDFs in the `downloads/` folder. For each PDF, it finds the anchors, redacts the original names/addresses, and overlays the user's new text, saving the result to `modified_invoices/`.
3. **The CLI Wrapper (`modify_pdfs.py`)**: A command-line interface that exposes `--generate-config`, `--apply`, `--dry-run`, and `--inspect` to the user.

## 3. The Problem & Bugs Encountered
During my testing of the modification logic across all 5 sample PDFs, I found several critical edge cases where the initial implementation broke down:
- **Anchor Overlap:** The hardcoded Y-coordinate boundaries for the Recipient (400-540) and Ship-To (>520) overlapped. In `BOM7-125914.pdf`, an anchor appeared at Y=529.9, causing it to be assigned incorrectly.
- **Extraction Bleed:** The address extraction grabbed unrelated labels (like "State:" or "Ship To") because it merely fetched 7 lines below the anchor without understanding semantic boundaries.
- **Redaction Collateral Damage:** The `_redact_rect` function used a 2-pixel padding in all directions. Because GST invoice text lines are very tight (~11px tall), this vertical padding erased portions of the adjacent GSTIN number.
- **Insertion Failures & State Corruption:** 
  1. `insert_textbox` silently refuses to draw text if the new text is physically taller than the redacted original bounding box.
  2. Modifying the page *during* extraction caused subsequent extractions (e.g., finding the address after redacting the name) to fail because the document state changed.

## 4. Proposed Changes
To fix these issues safely without destroying Claude's original work:
1. **Create `pdf_modifier_v2.py`**: I will build a new, robust version of the modifier module.
   - It will use relative sorting (1st anchor = header, 2nd = recipient, 3rd = ship-to) instead of hardcoded overlapping Y-ranges.
   - It will stop extracting address lines dynamically when it hits known boundaries (e.g., "State:", "Ship To").
   - It will use horizontal-only padding for redactions to protect adjacent lines.
   - It will use a **3-Phase Architecture**: (1) Extract all coordinates on an untouched page, (2) Apply all redactions at once, (3) Insert all new text line-by-line using `insert_text` to guarantee rendering.
2. **Update the CLI (`modify_pdfs.py`)**: Change the import to point to the `v2` module.
3. **Verify Output**: Run the verification script across all 5 PDFs to prove that the GSTIN, supplier info, and item tables remain 100% untouched, and only the target addresses are changed.
4. **Cleanup**: Once verified, archive or delete the broken `pdf_modifier.py` and finalize the codebase.

## 5. Progress Updates
- [x] Initial architecture assessment and bug identification.
- [x] Present plan to user for approval.
- [x] Implement `pdf_modifier_v2.py`.
- [x] Wire up CLI and run batch processing test.
- [x] Verify PDFs.
- [x] Clean up deprecated code (Kept Claude's original file, updated CLI to bypass it).
