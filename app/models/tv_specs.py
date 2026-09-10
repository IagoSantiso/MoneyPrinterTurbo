"""Structured TV specs used to feed the "tech reviewer" script variant.

Instead of relying only on a free-text ``video_subject``, the TV
review/comparison pipeline can build its script prompt from concrete,
structured facts about one or more televisions: brand, model, screen size,
panel type, refresh rate, HDR support and price. This keeps the LLM
grounded in real numbers instead of hallucinating specs, which matters a
lot for an affiliate-monetized channel.

This module only defines the data shape. Where the data actually comes
from (a maintained JSON/CSV file, a scraper, or an affiliate API) is a
pluggable concern implemented in ``app.services.tv_specs``.
"""

from __future__ import annotations

from typing import Optional

import pydantic
from pydantic import BaseModel, Field


class TVSpecs(BaseModel):
    """One television's specs, as fed into the review script prompt."""

    model_config = pydantic.ConfigDict(extra="ignore")

    brand: str
    model: str
    size_inches: float = Field(gt=0)
    panel_type: str  # e.g. "QLED", "OLED", "Mini-LED", "LED"
    refresh_rate_hz: int = Field(gt=0)
    hdr: str = ""  # e.g. "HDR10+, Dolby Vision", or "" if unsupported
    resolution: str = "4K"  # e.g. "4K", "8K"
    smart_platform: str = ""  # e.g. "Google TV", "Tizen", "webOS"
    price: Optional[float] = None
    currency: str = "EUR"
    ideal_for: str = ""  # e.g. "gaming", "home cinema", "budget living room"
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    affiliate_url: str = ""
    # Free-form source attribution (retailer name, API name, "manual"...),
    # useful for debugging / auditing which source produced this record.
    source: str = ""
    # Cloudflare R2 object-key prefix holding this TV's real product
    # photos/videos, e.g. "SAMSUNG_QN90D_55/" (see app.services.tv_product_media).
    # Left blank until photos are uploaded; the pipeline falls back to
    # generic stock footage whenever this is empty or the prefix has no
    # objects.
    product_images_prefix: str = ""

    def display_name(self) -> str:
        return f"{self.brand} {self.model} ({self.size_inches:g}\")".strip()


class TVComparison(BaseModel):
    """Two or more TVs being compared in the same short."""

    items: list[TVSpecs] = Field(min_length=1)
    comparison_angle: str = ""  # e.g. "best for gaming under 800€"
