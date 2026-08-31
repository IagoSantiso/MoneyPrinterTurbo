"""Pluggable source of TV specs for the review/comparison script variant.

The macroprompt driving this project explicitly asked to *not* decide the
concrete specs data source unilaterally (scraping vs. an affiliate API vs.
a self-maintained database) — that decision is pending user confirmation.

What's implemented here is the abstraction every future backend plugs
into, plus two working implementations:

- ``LocalJSONTVSpecsProvider`` reads a JSON/CSV file you maintain
  yourself (or that was exported from the Sheet at some point). Fully
  offline-testable, but it's a snapshot: edits to the source Sheet never
  reach it until someone re-exports the file by hand.
- ``GoogleSheetTVSpecsProvider`` reads the Sheet directly on every call
  (behind a short TTL cache), so new rows/edits/new R2 prefixes show up
  without a manual re-export step. See its docstring for setup.

Once a decision is made on the live/scraping/API source, add a new
``TVSpecsProvider`` subclass below (e.g. ``AmazonPAAPITVSpecsProvider``,
``RetailerScraperTVSpecsProvider``) and register it in
``get_tv_specs_provider``. Nothing else in the script-generation code
needs to change — ``generate_tv_review_script`` only depends on the
``TVSpecsProvider`` interface, not on a specific backend.
"""

from __future__ import annotations

import csv
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import pydantic
from loguru import logger

from app.models.tv_specs import TVSpecs


class TVSpecsNotFoundError(LookupError):
    """Raised when no matching TV specs record exists in the source."""


class TVSpecsProvider(ABC):
    """Interface every TV specs backend must implement."""

    # Populated by the last list_all()/fetch() call with a human-readable
    # message per row/record that couldn't be parsed into a TVSpecs (e.g. a
    # missing required column). Providers that can't fail partially (like
    # LocalJSONTVSpecsProvider, which just lets the exception propagate)
    # leave this empty. Callers that want to surface soft failures (the
    # WebUI's TV Review Lab) read this after calling list_all().
    row_errors: list[str] = []

    @abstractmethod
    def fetch(self, brand: str, model: str) -> TVSpecs:
        """Return the specs for one exact brand/model pair."""

    @abstractmethod
    def list_all(self) -> list[TVSpecs]:
        """Return every TV specs record the backend currently holds."""

    def search(self, query: str) -> list[TVSpecs]:
        """Loose text match over brand/model/ideal_for; used by the CLI."""
        query_lower = query.strip().lower()
        if not query_lower:
            return self.list_all()
        return [
            specs
            for specs in self.list_all()
            if query_lower in specs.brand.lower()
            or query_lower in specs.model.lower()
            or query_lower in specs.ideal_for.lower()
        ]


class LocalJSONTVSpecsProvider(TVSpecsProvider):
    """Reads TV specs from a JSON (list of objects) or CSV file you own.

    This is the "base de datos propia" option: no external calls, no
    scraping/ToS concerns, fully offline-testable. Point ``path`` at
    ``resource/tv_specs/example.json`` for a quick smoke test, or at your
    own curated file for production use.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._cache: Optional[list[TVSpecs]] = None

    def _load(self) -> list[TVSpecs]:
        if self._cache is not None:
            return self._cache

        if not self.path.exists():
            raise FileNotFoundError(f"TV specs file not found: {self.path}")

        if self.path.suffix.lower() == ".csv":
            with self.path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = []
                for row in reader:
                    for list_field in ("pros", "cons"):
                        raw = row.get(list_field) or ""
                        row[list_field] = [
                            item.strip() for item in raw.split("|") if item.strip()
                        ]
                    if row.get("price"):
                        row["price"] = float(row["price"])
                    rows.append(row)
        else:
            with self.path.open(encoding="utf-8") as f:
                rows = json.load(f)

        self._cache = [TVSpecs(**row) for row in rows]
        logger.info(f"loaded {len(self._cache)} TV specs records from {self.path}")
        return self._cache

    def list_all(self) -> list[TVSpecs]:
        return list(self._load())

    def fetch(self, brand: str, model: str) -> TVSpecs:
        for specs in self._load():
            if (
                specs.brand.lower() == brand.lower()
                and specs.model.lower() == model.lower()
            ):
                return specs
        raise TVSpecsNotFoundError(f"no TV specs found for {brand} {model}")


# TVSpecs fields the Sheet is expected to carry, mapped to a default guess
# at the column header. "brand" and "model" match the header names
# scripts/upload_tv_assets.py already looks for ("Marca" / "Modelo
# (comercial)") in this project's sheet; the rest are best-effort guesses.
# Override any of these under [google_sheets.columns] in config.toml to
# match your actual header row — a field whose header isn't found is
# simply left at its TVSpecs default (blank/0), except brand/model/
# size_inches/panel_type/refresh_rate_hz, which are required by TVSpecs:
# a row missing one of those is skipped (see GoogleSheetTVSpecsProvider).
DEFAULT_SHEET_COLUMN_MAP: dict[str, str] = {
    "brand": "Marca",
    "model": "Modelo (comercial)",
    "size_inches": "Tamaño (pulgadas)",
    "panel_type": "Panel",
    "refresh_rate_hz": "Frecuencia (Hz)",
    "hdr": "HDR",
    "resolution": "Resolución",
    "smart_platform": "Smart TV",
    "price": "Precio",
    "currency": "Moneda",
    "ideal_for": "Ideal para",
    "pros": "Pros",
    "cons": "Contras",
    "affiliate_url": "URL afiliado",
    "product_images_prefix": "product_images_prefix",
    "source": "Fuente",
}

_REQUIRED_SHEET_FIELDS = ("brand", "model", "size_inches", "panel_type", "refresh_rate_hz")


class GoogleSheetTVSpecsProvider(TVSpecsProvider):
    """Reads TV specs directly from a Google Sheet, live.

    Fixes the "the UI never sees my Sheet edits / new R2 folder" problem
    ``LocalJSONTVSpecsProvider`` has by construction: that one only ever
    reads whatever was manually exported into
    ``resource/tv_specs/*.json``, so a new row (or a new
    ``product_images_prefix`` pointing at a freshly-uploaded R2 folder)
    stays invisible until someone re-runs the export. This provider talks
    to the Sheet itself, cached for ``cache_ttl_seconds`` to avoid hitting
    the Sheets API on every Streamlit rerun.

    Two read strategies, tried in this order:

    1. **Service account** (``credentials_path`` set): uses ``gspread`` +
       a service account JSON key with at least Viewer access on the
       Sheet. Works for a private sheet. Same credential format as
       ``--sheet-credentials`` in ``scripts/upload_tv_assets.py`` — point
       both at the same file to reuse one service account for read+write.
    2. **Public CSV export** (no ``credentials_path``): fetches
       ``.../export?format=csv``, which only works if the Sheet is shared
       as "Anyone with the link" (or published to the web). Zero setup,
       but Google serves an HTML login page instead of CSV for a private
       sheet — detected and reported as a clear error rather than a
       confusing parse failure.

    Row parsing is best-effort per row: a row missing a required column
    (brand/model/size_inches/panel_type/refresh_rate_hz) or with a value
    that fails validation is skipped and recorded in ``row_errors``
    instead of failing the whole catalog load, since one bad row in a
    hand-edited Sheet is expected, not exceptional.
    """

    def __init__(
        self,
        sheet_id: str,
        worksheet_gid: int = 0,
        credentials_path: str = "",
        column_map: Optional[dict[str, str]] = None,
        cache_ttl_seconds: float = 60.0,
    ):
        self.sheet_id = sheet_id
        self.worksheet_gid = worksheet_gid
        self.credentials_path = credentials_path
        self.column_map = {**DEFAULT_SHEET_COLUMN_MAP, **(column_map or {})}
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Optional[list[TVSpecs]] = None
        self._cached_at: float = 0.0
        self.row_errors: list[str] = []

    def _fetch_rows_via_service_account(self) -> list[dict]:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(
            self.credentials_path, scopes=scopes
        )
        gc = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(self.sheet_id)
        worksheet = spreadsheet.get_worksheet_by_id(self.worksheet_gid)
        # get_all_records() reads row 1 as headers and returns one dict per
        # data row, keyed by header — exactly the shape _row_to_specs wants.
        return worksheet.get_all_records()

    def _fetch_rows_via_public_csv(self) -> list[dict]:
        import csv as _csv
        import io

        import requests

        url = (
            f"https://docs.google.com/spreadsheets/d/{self.sheet_id}"
            f"/export?format=csv&gid={self.worksheet_gid}"
        )
        resp = requests.get(url, timeout=30)
        if resp.status_code in (401, 403):
            raise PermissionError(
                f"Sheet {self.sheet_id!r} isn't publicly readable "
                f"(HTTP {resp.status_code}). Either share it as 'Anyone "
                "with the link' -> Viewer, or set [google_sheets] "
                "credentials_path to a service account JSON key with "
                "Viewer access."
            )
        resp.raise_for_status()
        text = resp.text
        # A private (or otherwise inaccessible) sheet doesn't 4xx here —
        # Google redirects to an HTML login/consent page and answers 200,
        # which would otherwise parse as one giant single-column "CSV".
        if text.lstrip().lower().startswith(("<!doctype html", "<html")):
            raise PermissionError(
                f"Sheet {self.sheet_id!r} isn't publicly readable (got an "
                "HTML login page instead of CSV). Either share it as "
                "'Anyone with the link' -> Viewer, or set "
                "[google_sheets] credentials_path to a service account "
                "JSON key with Viewer access."
            )
        return list(_csv.DictReader(io.StringIO(text)))

    def _row_to_specs(self, row: dict, row_number: int) -> Optional[TVSpecs]:
        def col(field: str) -> str:
            header = self.column_map.get(field, "")
            return str(row.get(header, "")).strip() if header else ""

        missing = [f for f in _REQUIRED_SHEET_FIELDS if not col(f)]
        if missing:
            self.row_errors.append(
                f"row {row_number}: skipped, missing required column(s) "
                f"{missing} — check [google_sheets.columns] against your "
                "sheet's header row"
            )
            return None

        pros_raw = col("pros")
        cons_raw = col("cons")
        price_raw = col("price")
        try:
            data = {
                "brand": col("brand"),
                "model": col("model"),
                "size_inches": float(col("size_inches").replace(",", ".")),
                "panel_type": col("panel_type"),
                "refresh_rate_hz": int(float(col("refresh_rate_hz").replace(",", "."))),
                "hdr": col("hdr"),
                "resolution": col("resolution") or "4K",
                "smart_platform": col("smart_platform"),
                "price": (
                    float(price_raw.replace(",", ".")) if price_raw else None
                ),
                "currency": col("currency") or "EUR",
                "ideal_for": col("ideal_for"),
                "pros": [p.strip() for p in pros_raw.split("|") if p.strip()],
                "cons": [c.strip() for c in cons_raw.split("|") if c.strip()],
                "affiliate_url": col("affiliate_url"),
                "product_images_prefix": col("product_images_prefix"),
                "source": col("source") or f"google_sheet:{self.sheet_id}",
            }
            return TVSpecs(**data)
        except (ValueError, pydantic.ValidationError) as exc:
            self.row_errors.append(f"row {row_number}: skipped, {exc}")
            return None

    def _load(self, force_refresh: bool = False) -> list[TVSpecs]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._cache is not None
            and (now - self._cached_at) < self.cache_ttl_seconds
        ):
            return self._cache

        if self.credentials_path:
            rows = self._fetch_rows_via_service_account()
        else:
            rows = self._fetch_rows_via_public_csv()

        self.row_errors = []
        specs = []
        for i, row in enumerate(rows, start=2):  # row 1 is the header
            parsed = self._row_to_specs(row, i)
            if parsed is not None:
                specs.append(parsed)

        self._cache = specs
        self._cached_at = now
        logger.info(
            f"loaded {len(specs)} TV specs record(s) from Google Sheet "
            f"{self.sheet_id!r} ({len(self.row_errors)} row(s) skipped)"
        )
        return specs

    def list_all(self, force_refresh: bool = False) -> list[TVSpecs]:
        return list(self._load(force_refresh=force_refresh))

    def fetch(self, brand: str, model: str) -> TVSpecs:
        for specs in self._load():
            if (
                specs.brand.lower() == brand.lower()
                and specs.model.lower() == model.lower()
            ):
                return specs
        raise TVSpecsNotFoundError(f"no TV specs found for {brand} {model}")


def get_tv_specs_provider(app_config=None) -> TVSpecsProvider:
    """Factory reading ``tv_review.specs_source`` from config.toml.

    "local_json" (default) and "google_sheets" are implemented.
    "scraping" and "affiliate_api" are placeholders reserved for the
    source you choose in Fase 2.3 of the macroprompt; wire the real
    backend in here once decided.
    """
    from app.config import config as app_config_module

    cfg = app_config or app_config_module
    backend = cfg.app.get("tv_review_specs_source", "local_json")

    if backend == "local_json":
        path = cfg.app.get(
            "tv_review_specs_path", "resource/tv_specs/example.json"
        )
        return LocalJSONTVSpecsProvider(path)

    if backend == "google_sheets":
        gs = getattr(cfg, "google_sheets", {}) or {}
        sheet_id = gs.get("sheet_id", "")
        if not sheet_id:
            raise ValueError(
                "tv_review_specs_source='google_sheets' but [google_sheets] "
                "sheet_id is empty in config.toml"
            )
        return GoogleSheetTVSpecsProvider(
            sheet_id=sheet_id,
            worksheet_gid=int(gs.get("worksheet_gid", 0) or 0),
            credentials_path=gs.get("credentials_path", ""),
            column_map=gs.get("columns"),
            cache_ttl_seconds=float(gs.get("cache_ttl_seconds", 60) or 0),
        )

    if backend in ("scraping", "affiliate_api"):
        raise NotImplementedError(
            f"tv_review_specs_source={backend!r} is a placeholder pending a "
            "decision on which retailer/API to integrate (see "
            "NETWORK_LIMITATION.md and the Fase 2.3 discussion). Implement "
            "a TVSpecsProvider subclass in app/services/tv_specs.py and "
            "register it here once decided."
        )

    raise ValueError(f"unknown tv_review_specs_source: {backend!r}")
