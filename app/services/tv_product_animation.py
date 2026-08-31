"""Turns a static TV product photo into a short AI-generated video clip,
via WaveSpeed's hosted Seedance image-to-video model — "v2" of the TV
review pipeline's product visuals, replacing the Ken Burns pan/zoom (v1)
with real camera-style motion generated per photo.

Endpoint contract (confirmed against the live API on 2026-08-31, using
validation-only requests that fail before any billed inference runs —
see the request/response shapes below). Both models tried so far share
the same request shape:

    POST https://api.wavespeed.ai/api/v3/{model_id}
    {
      "prompt": "...",
      "image": "<https URL, local file path, or data: URI>",
      "duration": <model-specific range, see _MODEL_PROFILES>,
      "resolution": "480p" | "720p" | "1080p" | "4k" (default "720p"),
      "<audio field, model-specific>": false
    }
    -> {"code": 200, "data": {"id": "...", "status": "created", "urls": {"get": "..."}}}

Models tried, and their pricing tier on WaveSpeed's public model catalog
(https://wavespeed.ai/api/models?search=..., base_price/discount_rate —
the actual $ isn't exposed without running a real generation):
  - bytedance/seedance-2.0-fast/image-to-video: duration 4-15s, audio
    field "generate_audio". First real test: $1.44 for a 4s clip, poor
    motion (camera mostly just orbited the product, no scene variety).
    base_price 500000, discount 80%.
  - alibaba/wan-3.0/image-to-video: duration 2-30s (much more headroom
    for a longer, more varied single clip), audio field "enable_audio".
    base_price 500000, discount 95% (highest discount in the catalog —
    real $/clip still unconfirmed).
  - wavespeed-ai/minimax-h3/image-to-video: lowest base_price in the
    catalog (200000, discount 50%) — not yet integrated/tested here,
    duration bounds unknown; probe with a validation-only request (see
    the Seedance/Wan discovery method above) before wiring it in.

Then poll ``GET .../predictions/{id}/result`` (via the existing
``material._wait_for_wavespeed_prediction`` helper — same polling/retry
contract as the project's existing text-to-video WaveSpeed integration)
until ``status`` is ``completed`` (outputs: [<video URL>]) or a terminal
failure state.

Fallback contract: any failure here (no API key, bad photo, remote
error, timeout) returns None. Callers must fall back to the existing
Ken Burns treatment of the original photo — this is explicitly meant to
degrade, never to break a run over one animation failure.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Optional

from loguru import logger

from app.utils import utils

# alibaba/wan-3.0 is the current default: highest discount rate in
# WaveSpeed's catalog, and 2-30s duration gives much more room for a
# single varied clip than Seedance's 4-15s. Switch back to Seedance (or
# add minimax-h3 here once its duration bounds are probed) by passing
# model_id= explicitly or setting wavespeed_image_to_video_model.
DEFAULT_IMAGE_TO_VIDEO_MODEL = "alibaba/wan-3.0/image-to-video"
DEFAULT_DURATION_SECONDS = 6
DEFAULT_RESOLUTION = "720p"
_ALLOWED_RESOLUTIONS = frozenset({"480p", "720p", "1080p", "4k"})

# (min_duration, max_duration, audio_field_name_or_None). audio_field is
# set to False in the payload so the clip's own generated audio never
# overlaps the pipeline's voiceover track; None means the model doesn't
# expose one (leave the field out entirely rather than guess a name).
_MODEL_PROFILES = {
    "bytedance/seedance-2.0-fast/image-to-video": (4, 15, "generate_audio"),
    "alibaba/wan-3.0/image-to-video": (2, 30, "enable_audio"),
    # duration is actually an enum {3..15}, not a free range; clamping to
    # this min/max still keeps requested values valid since they're all
    # integers. No audio field — this model doesn't generate a soundtrack.
    "wavespeed-ai/minimax-h3/image-to-video": (3, 15, None),
}
_DEFAULT_PROFILE = (4, 15, None)  # used for any model not listed above

DEFAULT_ANIMATION_PROMPT_TEMPLATE = (
    "Professional commercial product video of a {brand} {model} television. "
    "Continuous dynamic camera movement across the whole clip — start with "
    "a slow push-in toward the screen, then transition into a subtle orbit "
    "or parallax reveal of the sides and stand, varying the framing "
    "throughout rather than a single static rotation. Studio lighting, "
    "shallow depth of field, cinematic, high quality, no text overlays, "
    "no people."
)


def build_product_animation_prompt(brand: str, model: str) -> str:
    return DEFAULT_ANIMATION_PROMPT_TEMPLATE.format(brand=brand, model=model)


def _image_to_video_config(app_config=None) -> dict:
    from app.config import config as app_config_module

    cfg = app_config or app_config_module
    return cfg.app


def animate_product_photo_with_wavespeed(
    image_path: Path,
    prompt: str,
    duration: int = DEFAULT_DURATION_SECONDS,
    resolution: str = DEFAULT_RESOLUTION,
    model_id: str | None = None,
    video_aspect=None,
    app_config=None,
) -> Optional[Path]:
    """Animates one product photo via a WaveSpeed image-to-video model and
    downloads the result locally. Returns the local clip path on success,
    or None on any failure (never raises) — the caller is expected to fall
    back to Ken Burns on the original photo.
    """
    # Imported lazily so importing this module doesn't require app.config
    # to be initialized (mirrors the pattern used elsewhere in this
    # feature, e.g. app.services.tv_specs.get_tv_specs_provider).
    from app.services import material as material_service

    try:
        api_key = material_service.get_api_key("wavespeed_api_keys")
    except ValueError as exc:
        logger.warning(f"skip WaveSpeed animation, not configured: {exc}")
        return None

    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        logger.warning(f"cannot read product photo {image_path} for animation: {exc}")
        return None

    if not model_id:
        cfg = _image_to_video_config(app_config)
        model_id = str(
            cfg.get("wavespeed_image_to_video_model", "") or DEFAULT_IMAGE_TO_VIDEO_MODEL
        ).strip().strip("/")

    min_duration, max_duration, audio_field = _MODEL_PROFILES.get(
        model_id, _DEFAULT_PROFILE
    )
    resolution = resolution if resolution in _ALLOWED_RESOLUTIONS else DEFAULT_RESOLUTION
    duration = max(min_duration, min(int(duration or DEFAULT_DURATION_SECONDS), max_duration))
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    image_data_uri = (
        f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    )

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "prompt": prompt,
        "image": image_data_uri,
        "duration": duration,
        "resolution": resolution,
    }
    if audio_field:
        # Avoid a generated soundtrack fighting the pipeline's own
        # voiceover once this clip is muxed into the final video.
        payload[audio_field] = False

    logger.info(
        f"animating product photo with WaveSpeed: model={model_id}, "
        f"photo={image_path.name}, duration={duration}s, resolution={resolution}"
    )

    try:
        submit_response = material_service.requests.post(
            f"{material_service.WAVESPEED_API_BASE_URL}/{model_id}",
            json=payload,
            headers=headers,
            proxies=material_service.config.proxy,
            verify=material_service._get_tls_verify(),
            timeout=(30, 60),
        )
    except Exception as exc:
        # Unlike the text-to-video path, a submission failure here doesn't
        # need to halt the whole pipeline (this is one photo of several,
        # not a chain of paid keyword generations) — log and fall back.
        logger.warning(
            f"WaveSpeed animation submission failed for {image_path.name}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    try:
        submit_body = submit_response.json()
    except Exception as exc:
        logger.warning(
            f"WaveSpeed animation submission returned an unreadable response "
            f"for {image_path.name}: {exc}"
        )
        return None

    submit_data = submit_body.get("data") if isinstance(submit_body, dict) else None
    if not isinstance(submit_body, dict) or submit_body.get("code") != 200:
        logger.warning(
            f"WaveSpeed animation rejected for {image_path.name}: "
            f"{(submit_body or {}).get('message')}"
        )
        return None

    prediction_id = (
        str(submit_data.get("id") or "") if isinstance(submit_data, dict) else ""
    )
    if not prediction_id:
        logger.warning(
            f"WaveSpeed animation accepted without a prediction id for "
            f"{image_path.name}"
        )
        return None

    logger.info(f"WaveSpeed animation prediction created: id={prediction_id}")

    try:
        result_data = material_service._wait_for_wavespeed_prediction(
            prediction_id=prediction_id,
            headers=headers,
            api_key=api_key,
        )
    except material_service.WaveSpeedUnconfirmedTaskError as exc:
        # The remote task may still complete/have completed and billed —
        # log the id so it can be found in the WaveSpeed dashboard, but
        # don't fail the run over one photo's animation.
        logger.warning(
            f"WaveSpeed animation status unknown for {image_path.name}: {exc}"
        )
        return None

    if result_data is None:
        logger.warning(f"WaveSpeed animation failed for {image_path.name}")
        return None

    outputs = result_data.get("outputs")
    output_url = next(
        (
            item
            for item in (outputs if isinstance(outputs, list) else [])
            if isinstance(item, str) and item.startswith(("http://", "https://"))
        ),
        None,
    )
    if not output_url:
        logger.warning(
            f"WaveSpeed animation completed without a downloadable output for "
            f"{image_path.name}: id={prediction_id}"
        )
        return None

    # Must land inside storage/local_videos: preprocess_video()'s path
    # guard (app/utils/file_security.py) only accepts materials that
    # resolve inside that directory — same reason tv_product_media.py's
    # R2 cache lives there instead of a sibling directory.
    save_dir = utils.storage_dir(
        os.path.join("local_videos", "tv_product_media_cache", "wavespeed_animated"),
        create=True,
    )
    try:
        local_path = material_service.save_video(output_url, save_dir=save_dir)
    except Exception as exc:
        logger.warning(
            f"failed to download WaveSpeed animation output for "
            f"{image_path.name}: {exc}"
        )
        return None

    logger.success(
        f"animated product photo: {image_path.name} -> {local_path} "
        f"(id={prediction_id})"
    )
    return Path(local_path)


def animate_product_photos(
    photo_paths: list[Path],
    brand: str,
    model: str,
    duration: int = DEFAULT_DURATION_SECONDS,
    resolution: str = DEFAULT_RESOLUTION,
    model_id: str | None = None,
    app_config=None,
) -> list[Path]:
    """Animates each photo in order, returning one path per input photo:
    the generated clip on success, or the original photo unchanged on
    failure (so preprocess_video()'s existing Ken Burns path picks it up
    automatically — no separate fallback wiring needed by the caller).
    """
    prompt = build_product_animation_prompt(brand, model)
    results = []
    for photo_path in photo_paths:
        animated = animate_product_photo_with_wavespeed(
            photo_path,
            prompt=prompt,
            duration=duration,
            resolution=resolution,
            model_id=model_id,
            app_config=app_config,
        )
        results.append(animated or photo_path)
    return results
