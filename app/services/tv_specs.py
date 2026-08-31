"""Pluggable source of TV specs for the review/comparison script variant.

The macroprompt driving this project explicitly asked to *not* decide the
concrete specs data source unilaterally (scraping vs. an affiliate API vs.
a self-maintained database) — that decision is pending user confirmation.

What's implemented here is the abstraction every future backend plugs
into, plus one working implementation (``LocalJSONTVSpecsProvider``) that
reads a JSON/CSV file you maintain yourself. That keeps the pipeline fully
testable offline today and is also, standalone, one legitimate answer to
"where do the specs come from" — the self-maintained-database option.

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
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from loguru import logger

from app.models.tv_specs import TVSpecs


class TVSpecsNotFoundError(LookupError):
    """Raised when no matching TV specs record exists in the source."""


class TVSpecsProvider(ABC):
    """Interface every TV specs backend must implement."""

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


def get_tv_specs_provider(app_config=None) -> TVSpecsProvider:
    """Factory reading ``tv_review.specs_source`` from config.toml.

    Only "local_json" is implemented today. "scraping" and "affiliate_api"
    are placeholders reserved for the source you choose in Fase 2.3 of the
    macroprompt; wire the real backend in here once decided.
    """
    from app.config import config as app_config_module

    cfg = app_config or app_config_module
    backend = cfg.app.get("tv_review_specs_source", "local_json")

    if backend == "local_json":
        path = cfg.app.get(
            "tv_review_specs_path", "resource/tv_specs/example.json"
        )
        return LocalJSONTVSpecsProvider(path)

    if backend in ("scraping", "affiliate_api"):
        raise NotImplementedError(
            f"tv_review_specs_source={backend!r} is a placeholder pending a "
            "decision on which retailer/API to integrate (see "
            "NETWORK_LIMITATION.md and the Fase 2.3 discussion). Implement "
            "a TVSpecsProvider subclass in app/services/tv_specs.py and "
            "register it here once decided."
        )

    raise ValueError(f"unknown tv_review_specs_source: {backend!r}")
