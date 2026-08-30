#!/usr/bin/env python3
"""Upload local TV product photos/videos to Cloudflare R2 and (optionally)
update the matching row's product_images_prefix column in the Google Sheet.

Usage:

    uv run python scripts/upload_tv_assets.py ~/tv-photos

Expects a local folder with one subfolder per TV model, named exactly like
the R2 prefix you want to use, e.g.:

    ~/tv-photos/
      SAMSUNG_QN90D_55/
        IMG_2087.jpg
        IMG_2088.jpg
        IMG_2091.jpg
      LG_C4_55/
        IMG_3001.jpg
        IMG_3002.jpg

Plain camera filenames work fine — no need to rename anything. A leading
number in the filename (01_..., IMG_2087...) is used for ordering when
present, but the pipeline doesn't depend on a fixed photo order: each
photo gets its own Ken Burns clip and the video assembly step decides
duration/transitions per clip to fit the generated script.

Each subfolder is uploaded to R2 under that same key prefix (via boto3,
same credentials as the pipeline's [r2] config in config.toml) and,
if --sheet-id is given, the Sheet's product_images_prefix column is
updated for the row matching brand+model (read from --sheet-id via the
Google Sheets API, matched by the sheet's own Marca/Modelo columns).

Google Sheets update is optional and off by default: it needs a service
account with edit access to the sheet, which is separate from R2's
credentials. Pass --sheet-id and --sheet-credentials to enable it; without
them, the script only uploads to R2 and prints the prefixes you'd need to
paste into the Sheet by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# add project root to python path so `app.*` imports resolve when this
# script is run directly (uv run python scripts/upload_tv_assets.py ...)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _iter_model_folders(root: Path):
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            yield child


def upload_folder_to_r2(client, bucket_name: str, folder: Path, prefix: str) -> int:
    """Uploads every file directly under `folder` to R2 as `<prefix><filename>`.
    Returns the number of files uploaded."""
    uploaded = 0
    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue
        key = f"{prefix.rstrip('/')}/{file_path.name}"
        client.upload_file(str(file_path), bucket_name, key)
        uploaded += 1
        print(f"  uploaded {file_path.name} -> {key}")
    return uploaded


def update_sheet_prefix(
    sheet_id: str,
    credentials_path: str,
    brand: str,
    model: str,
    prefix: str,
) -> bool:
    """Best-effort: finds the row matching brand+model (columns 'Marca' and
    'Modelo (comercial)', matching this project's existing sheet layout) and
    writes `prefix` into its product_images_prefix column, adding that
    column if it doesn't exist yet. Returns True if a row was updated.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print(
            "  skip Sheet update: install gspread + google-auth "
            "(uv add gspread google-auth) to enable this, or omit "
            "--sheet-id/--sheet-credentials to upload-only.",
            file=sys.stderr,
        )
        return False

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    gc = gspread.authorize(creds)
    worksheet = gc.open_by_key(sheet_id).sheet1

    header = worksheet.row_values(1)
    try:
        brand_col = header.index("Marca") + 1
        model_col = header.index("Modelo (comercial)") + 1
    except ValueError:
        print(
            "  skip Sheet update: couldn't find 'Marca'/'Modelo (comercial)' "
            "columns in row 1 — adjust update_sheet_prefix() to your sheet's "
            "actual header names.",
            file=sys.stderr,
        )
        return False

    if "product_images_prefix" in header:
        prefix_col = header.index("product_images_prefix") + 1
    else:
        prefix_col = len(header) + 1
        worksheet.update_cell(1, prefix_col, "product_images_prefix")

    all_values = worksheet.get_all_values()
    for row_index, row in enumerate(all_values[1:], start=2):
        row_brand = row[brand_col - 1] if len(row) >= brand_col else ""
        row_model = row[model_col - 1] if len(row) >= model_col else ""
        if row_brand.strip().lower() == brand.lower() and row_model.strip().lower().startswith(
            model.lower()
        ):
            worksheet.update_cell(row_index, prefix_col, prefix)
            return True

    print(
        f"  skip Sheet update: no row found for brand={brand!r} model={model!r} "
        "(check spelling against the sheet's Marca/Modelo columns)",
        file=sys.stderr,
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "local_folder",
        help="local folder containing one subfolder per TV model",
    )
    parser.add_argument(
        "--sheet-id",
        default="",
        help="Google Sheet ID to update product_images_prefix in (optional)",
    )
    parser.add_argument(
        "--sheet-credentials",
        default="",
        help="path to a Google service-account JSON key (required with --sheet-id)",
    )
    args = parser.parse_args()

    if args.sheet_id and not args.sheet_credentials:
        parser.error("--sheet-id requires --sheet-credentials")

    root = Path(args.local_folder).expanduser()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    # Imported here (not at module load) so `--help` doesn't require a
    # configured config.toml.
    from app.config import config
    from app.services.tv_product_media import get_r2_client, R2NotConfiguredError

    try:
        client = get_r2_client()
    except R2NotConfiguredError as exc:
        parser.error(str(exc))
        return 2

    bucket_name = config.r2.get("bucket_name", "")
    if not bucket_name:
        parser.error("R2 is not configured: set [r2] bucket_name in config.toml")
        return 2

    total_files = 0
    updated_rows = 0
    prefixes: list[str] = []

    for folder in _iter_model_folders(root):
        prefix = f"{folder.name}/"
        print(f"{folder.name}:")
        count = upload_folder_to_r2(client, bucket_name, folder, prefix)
        total_files += count
        prefixes.append(prefix)

        if count == 0:
            print("  (no files found, skipped)")
            continue

        if args.sheet_id:
            # Folder name convention: BRAND_MODEL[_SIZE], e.g. SAMSUNG_QN90D_55.
            # Brand is everything before the first underscore; model is the
            # rest minus a trailing _<digits> size suffix, if present.
            parts = folder.name.split("_")
            brand = parts[0]
            model_parts = parts[1:]
            if model_parts and model_parts[-1].isdigit():
                model_parts = model_parts[:-1]
            model = " ".join(model_parts)
            if update_sheet_prefix(
                args.sheet_id, args.sheet_credentials, brand, model, prefix
            ):
                updated_rows += 1

    print()
    print(f"Summary: {total_files} file(s) uploaded across {len(prefixes)} model folder(s).")
    if args.sheet_id:
        print(f"Sheet rows updated: {updated_rows}/{len(prefixes)}")
    else:
        print("Sheet not updated (no --sheet-id given). Paste these prefixes manually:")
        for prefix in prefixes:
            print(f"  {prefix}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
