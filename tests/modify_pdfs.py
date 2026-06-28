"""
GST Invoice PDF Modifier — CLI
================================
Modify header names and party-detail addresses in bulk GST invoices.

Usage:
    # Step 1: Generate config from a sample PDF
    python modify_pdfs.py --generate-config

    # Step 1 (alt): Generate from a specific PDF
    python modify_pdfs.py --generate-config downloads/BOM7-125914.pdf

    # Step 2: Edit pdf_config.yaml — fill in the 'new' values

    # Step 3: Apply modifications to all PDFs
    python modify_pdfs.py --apply

    # Step 3 (alt): Use a custom config file
    python modify_pdfs.py --apply --config my_config.yaml

    # Preview: Dry-run on first 3 files
    python modify_pdfs.py --dry-run
"""

import sys
import argparse
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from gst_downloader.pdf_modifier_v2 import (
    generate_config_file,
    batch_modify,
    extract_fields_from_pdf,
    modify_single_pdf,
    _load_config,
)


def cmd_generate_config(args):
    """Generate a config file from a sample PDF."""
    if args.sample_pdf:
        sample = Path(args.sample_pdf)
    else:
        # Auto-detect: pick the first PDF in downloads/
        dl_dir = Path("downloads")
        pdfs = sorted(dl_dir.glob("*.pdf"))
        if not pdfs:
            print("  ERROR: No PDF files found in downloads/")
            print("  Specify a sample PDF: python modify_pdfs.py --generate-config path/to/file.pdf")
            sys.exit(1)
        sample = pdfs[0]

    print(f"\n  Scanning sample PDF: {sample}\n")

    if not sample.exists():
        print(f"  ERROR: File not found: {sample}")
        sys.exit(1)

    generate_config_file(str(sample), args.config)
    print()


def cmd_apply(args):
    """Apply config modifications to all PDFs."""
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"\n  ERROR: Config file not found: {config_path}")
        print(f"  Generate one first: python modify_pdfs.py --generate-config\n")
        sys.exit(1)

    batch_modify("downloads", "modified_invoices", str(config_path))


def cmd_dry_run(args):
    """Preview what changes would be applied (first 3 files only)."""
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"\n  ERROR: Config file not found: {config_path}")
        print(f"  Generate one first: python modify_pdfs.py --generate-config\n")
        sys.exit(1)

    config = _load_config(str(config_path))
    input_dir = Path(config.get("input_folder", "downloads"))
    pdf_files = sorted(input_dir.glob("*.pdf"))[:3]

    if not pdf_files:
        print(f"  No PDF files found in {input_dir}")
        return

    print(f"\n{'=' * 62}")
    print(f"  DRY RUN — Preview (first {len(pdf_files)} files)")
    print(f"{'=' * 62}\n")

    for pdf_path in pdf_files:
        print(f"  File: {pdf_path.name}")
        fields = extract_fields_from_pdf(str(pdf_path))

        # Show what would change
        checks = [
            ("Header name", fields["header"]["name"], config.get("header_name", {})),
            ("Recipient name", fields["recipient"]["name"], config.get("recipient_name", {})),
            ("Recipient address", fields["recipient"]["address"], config.get("recipient_address", {})),
            ("Ship-To name", fields["ship_to"]["name"], config.get("ship_to_name", {})),
            ("Ship-To address", fields["ship_to"]["address"], config.get("ship_to_address", {})),
        ]

        for label, current, cfg_section in checks:
            new_val = ""
            if isinstance(cfg_section, dict):
                new_val = cfg_section.get("new", "")
                if isinstance(new_val, str):
                    new_val = new_val.strip()

            if new_val:
                current_short = current[:40] + "..." if len(current) > 40 else current
                new_short = new_val[:40] + "..." if len(new_val) > 40 else new_val
                print(f"    ✏  {label}:")
                print(f"       FROM: {current_short}")
                print(f"       TO  : {new_short}")
            else:
                print(f"    —  {label}: (no change)")

        print()

    print(f"  To apply changes, run: python modify_pdfs.py --apply\n")


def cmd_inspect(args):
    """Inspect a PDF and show all extractable fields."""
    if args.sample_pdf:
        sample = Path(args.sample_pdf)
    else:
        dl_dir = Path("downloads")
        pdfs = sorted(dl_dir.glob("*.pdf"))
        if not pdfs:
            print("  No PDFs found in downloads/")
            sys.exit(1)
        sample = pdfs[0]

    print(f"\n  Inspecting: {sample}\n")
    fields = extract_fields_from_pdf(str(sample))

    print(f"  GSTIN          : {fields['gstin']}")
    print(f"  Header Name    : {fields['header']['name']}")
    print(f"  Recipient Name : {fields['recipient']['name']}")
    print(f"  Recipient Addr : {fields['recipient']['address']}")
    print(f"  Ship-To Name   : {fields['ship_to']['name']}")
    print(f"  Ship-To Addr   : {fields['ship_to']['address']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="GST Invoice PDF Modifier — modify header & address in bulk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python modify_pdfs.py --generate-config                    Generate config from first PDF
  python modify_pdfs.py --generate-config downloads/X.pdf    Generate from specific PDF
  python modify_pdfs.py --apply                              Apply changes to all PDFs
  python modify_pdfs.py --dry-run                            Preview without modifying
  python modify_pdfs.py --inspect                            Show fields from a PDF
        """,
    )

    # Mutually exclusive actions
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate-config", dest="generate_config", action="store_true",
        help="Generate pdf_config.yaml from a sample PDF",
    )
    group.add_argument(
        "--apply", action="store_true",
        help="Apply modifications from pdf_config.yaml to all PDFs",
    )
    group.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Preview what changes would be applied (first 3 files only)",
    )
    group.add_argument(
        "--inspect", action="store_true",
        help="Inspect a PDF and show all extractable fields",
    )

    parser.add_argument(
        "sample_pdf", nargs="?", default=None,
        help="Path to a specific PDF (for --generate-config and --inspect)",
    )
    parser.add_argument(
        "--config", default="pdf_config.yaml",
        help="Path to config file (default: pdf_config.yaml)",
    )

    args = parser.parse_args()

    if args.generate_config:
        cmd_generate_config(args)
    elif args.apply:
        cmd_apply(args)
    elif args.dry_run:
        cmd_dry_run(args)
    elif args.inspect:
        cmd_inspect(args)


if __name__ == "__main__":
    main()
