import pytest

from app.models.tv_specs import TVSpecs
from app.services.tv_review_script import (
    TV_REVIEW_SYSTEM_PROMPT,
    build_tv_review_facts_block,
    build_tv_review_subject,
)
from app.services.tv_specs import (
    LocalJSONTVSpecsProvider,
    TVSpecsNotFoundError,
)

EXAMPLE_SPECS_PATH = "resource/tv_specs/example.json"


def _samsung_specs() -> TVSpecs:
    return TVSpecs(
        brand="Samsung",
        model="QN90D",
        size_inches=55,
        panel_type="Mini-LED QLED",
        refresh_rate_hz=144,
        hdr="HDR10+",
        price=1299.0,
        currency="EUR",
        ideal_for="gaming",
    )


class TestLocalJSONTVSpecsProvider:
    def test_loads_example_file(self):
        provider = LocalJSONTVSpecsProvider(EXAMPLE_SPECS_PATH)
        records = provider.list_all()
        assert len(records) == 2
        assert {r.brand for r in records} == {"Samsung", "LG"}

    def test_fetch_is_case_insensitive(self):
        provider = LocalJSONTVSpecsProvider(EXAMPLE_SPECS_PATH)
        specs = provider.fetch("samsung", "qn90d")
        assert specs.brand == "Samsung"
        assert specs.model == "QN90D"

    def test_fetch_missing_raises(self):
        provider = LocalJSONTVSpecsProvider(EXAMPLE_SPECS_PATH)
        with pytest.raises(TVSpecsNotFoundError):
            provider.fetch("Sony", "Nonexistent")

    def test_search_matches_ideal_for(self):
        provider = LocalJSONTVSpecsProvider(EXAMPLE_SPECS_PATH)
        results = provider.search("cinema")
        assert len(results) == 1
        assert results[0].brand == "LG"

    def test_missing_file_raises(self, tmp_path):
        provider = LocalJSONTVSpecsProvider(tmp_path / "missing.json")
        with pytest.raises(FileNotFoundError):
            provider.list_all()


class TestBuildTvReviewSubject:
    def test_single_tv(self):
        subject = build_tv_review_subject([_samsung_specs()])
        assert subject == 'Samsung QN90D (55") review'

    def test_comparison(self):
        specs = [
            _samsung_specs(),
            TVSpecs(
                brand="LG",
                model="C4",
                size_inches=55,
                panel_type="OLED",
                refresh_rate_hz=120,
                hdr="Dolby Vision",
                price=1399.0,
            ),
        ]
        subject = build_tv_review_subject(specs)
        assert "vs" in subject
        assert "Samsung QN90D" in subject
        assert "LG C4" in subject


class TestBuildTvReviewFactsBlock:
    def test_single_tv_includes_all_required_facts(self):
        facts = build_tv_review_facts_block([_samsung_specs()])
        assert "Panel: Mini-LED QLED" in facts
        assert "Refresh rate: 144Hz" in facts
        assert "Price: 1299 EUR" in facts
        assert "Ideal for: gaming" in facts

    def test_missing_price_is_flagged_not_omitted(self):
        specs = _samsung_specs().model_copy(update={"price": None})
        facts = build_tv_review_facts_block([specs])
        assert "not available, do not invent one" in facts

    def test_comparison_angle_only_added_for_multiple_tvs(self):
        single = build_tv_review_facts_block([_samsung_specs()], "gaming")
        assert "Comparison angle" not in single

        lg = TVSpecs(
            brand="LG",
            model="C4",
            size_inches=55,
            panel_type="OLED",
            refresh_rate_hz=120,
        )
        multi = build_tv_review_facts_block(
            [_samsung_specs(), lg], "best for gaming under 1500 EUR"
        )
        assert "Comparison angle: best for gaming under 1500 EUR" in multi


def test_system_prompt_enforces_hook_benefits_cta_structure():
    assert "HOOK" in TV_REVIEW_SYSTEM_PROMPT
    assert "BENEFITS" in TV_REVIEW_SYSTEM_PROMPT
    assert "CTA" in TV_REVIEW_SYSTEM_PROMPT
    assert "link in bio" in TV_REVIEW_SYSTEM_PROMPT
    assert "Only use the specs/price facts given" in TV_REVIEW_SYSTEM_PROMPT


def test_system_prompt_maps_specs_to_benefits_honestly():
    assert "Mini LED / OLED panel" in TV_REVIEW_SYSTEM_PROMPT
    assert "High refresh rate (Hz)" in TV_REVIEW_SYSTEM_PROMPT
    assert "Honesty rule" in TV_REVIEW_SYSTEM_PROMPT
    assert "claim the opposite" in TV_REVIEW_SYSTEM_PROMPT
