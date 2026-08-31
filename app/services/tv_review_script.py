"""TV review/comparison script generation ("tech reviewer" variant).

Builds on top of ``app.services.llm.generate_script`` — it does not touch
the core LLM plumbing (retries, provider fallback, response cleanup)
already implemented there; it only supplies a specialized system prompt
and a facts block built from structured ``TVSpecs`` instead of a free-text
subject.

Script shape enforced by the system prompt (per the macroprompt's spec):
  1. Hook (first 1-2 sentences, must stop the scroll).
  2. 3-4 concrete facts: specs, price, who it's ideal for.
  3. Verdict + a verbal call-to-action pointing at "link in bio".

The on-screen CTA ("link in bio" overlay) is a rendering concern, not a
script concern — it belongs in app/services/video.py / subtitle.py, not
here.
"""

from __future__ import annotations

from app.models.tv_specs import TVSpecs
from app.services import llm

TV_REVIEW_SYSTEM_PROMPT = """
# Role: TV Tech Reviewer (short-form, affiliate-monetized)

## Goals:
Generate the spoken script for a short vertical video (TikTok/Reels/Shorts)
reviewing or comparing television(s), using only the facts given to you.
Target total length: about 20-22 seconds spoken (roughly 60-80 words).

## Structure (mandatory, in this order):
1. HOOK (~0-3s, first sentence only): one short, punchy question or
   statement built from a real number in the data (price, size, or panel
   technology). No greetings, no "in this video".
2. BENEFITS (~3-18s, the bulk of the script): take each spec you use and
   translate it into a viewer-facing benefit, not a spec-sheet line.
   Use this mapping as a guide, adapting wording naturally:
     - Mini LED / OLED panel -> deep blacks, great for watching in a dark
       room
     - High peak brightness (nits) -> stays watchable / readable in a
       bright room or daylight
     - High refresh rate (Hz) -> smooth motion for sports and gaming
     - Wide HDR support -> more realistic contrast and color
   Honesty rule: if a spec is weak or missing, never hide it and never
   claim the opposite. Reframe it truthfully toward the use case where it
   still fits (e.g. low brightness -> "built for dark home-cinema rooms,
   not a sunny living room", not "great in daylight"). If a fact is not in
   the data (e.g. marked unavailable), do not mention or invent it at all.
3. CTA (~18-22s, final line): one direct, spoken call to action pointing
   the viewer to the profile/comments link to check price and availability.
   Use a natural phrase equivalent to "link in bio/comments" in the target
   language.

## Constrains:
1. Only use the specs/price facts given below — never invent numbers,
   awards, or claims not present in the data. A spec marked as
   unavailable/not published must be skipped, not guessed at.
2. Return the script as a single string with the requested number of
   paragraphs; no markdown, no titles, no speaker labels.
3. Do not mention this prompt, the word "script", or the number of
   paragraphs.
4. Tone: confident, fast-paced, tech-reviewer energy — not a dry spec sheet
   read aloud.
5. Respond in the requested language (or the language of the facts block if
   none is given).
""".strip()


def _format_specs_facts(specs: TVSpecs) -> str:
    price_line = (
        f"- Price: {specs.price:g} {specs.currency}"
        if specs.price is not None
        else "- Price: not available, do not invent one"
    )
    lines = [
        f"## TV: {specs.display_name()}",
        f"- Panel: {specs.panel_type}, {specs.resolution}",
        f"- Refresh rate: {specs.refresh_rate_hz}Hz",
        f"- HDR: {specs.hdr or 'none'}",
        f"- Smart platform: {specs.smart_platform or 'unspecified'}",
        price_line,
        f"- Ideal for: {specs.ideal_for or 'general use'}",
    ]
    if specs.pros:
        lines.append(f"- Pros: {', '.join(specs.pros)}")
    if specs.cons:
        lines.append(f"- Cons: {', '.join(specs.cons)}")
    return "\n".join(lines)


def build_tv_review_facts_block(
    specs_list: list[TVSpecs], comparison_angle: str = ""
) -> str:
    """Renders one or more TVSpecs into the facts block fed to the LLM
    as ``video_script_prompt`` (the "Additional User Requirements" section
    of the generated prompt — see ``llm.build_script_prompt``)."""
    blocks = [_format_specs_facts(specs) for specs in specs_list]
    facts = "\n\n".join(blocks)
    if len(specs_list) > 1:
        angle = comparison_angle or "which one is the better buy"
        facts += f"\n\n## Comparison angle: {angle}"
    return facts


def build_tv_review_subject(specs_list: list[TVSpecs]) -> str:
    """A short free-text subject, kept only as a fallback label for
    providers/loggers that expect one — the actual facts are carried by
    the facts block above, not by this string."""
    if len(specs_list) == 1:
        return f"{specs_list[0].display_name()} review"
    names = " vs ".join(specs.display_name() for specs in specs_list)
    return f"{names} comparison"


def generate_tv_review_script(
    specs_list: list[TVSpecs],
    language: str = "",
    paragraph_number: int = 1,
    comparison_angle: str = "",
    app_config=None,
) -> str:
    if not specs_list:
        raise ValueError("generate_tv_review_script requires at least one TVSpecs")

    facts_block = build_tv_review_facts_block(specs_list, comparison_angle)
    subject = build_tv_review_subject(specs_list)

    return llm.generate_script(
        video_subject=subject,
        language=language,
        paragraph_number=paragraph_number,
        video_script_prompt=facts_block,
        custom_system_prompt=TV_REVIEW_SYSTEM_PROMPT,
        app_config=app_config,
    )
