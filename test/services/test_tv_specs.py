from unittest.mock import patch

import pytest

from app.services import tv_specs


def _row(**overrides) -> dict:
    """A default row shaped like GoogleSheetTVSpecsProvider._fetch_rows_*
    output (dict keyed by this project's real Sheet column headers,
    confirmed 2026-08-31), with overrides applied on top."""
    row = {
        "Marca": "Samsung",
        "Modelo (comercial)": "QN90D 55",
        "Tamaño pantalla (pulgadas)": "55",
        "Tipo panel (LED/QLED/OLED/Mini-LED)": "Mini-LED QLED",
        "Tasa de refresco (Hz)": "144",
        "HDR (tipos soportados)": "HDR10+, HDR10, HLG",
        "Resolución": "4K",
        "Smart TV / Sistema operativo": "Tizen",
        "Precio (€)": "1299",
        "Enlace Amazon": "https://example.com/samsung",
        "product_images_prefix": "SAMSUNG_QN90D_55/",
    }
    row.update(overrides)
    return row


class TestRowToSpecs:
    def test_parses_a_full_row(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        specs = provider._row_to_specs(_row(), row_number=2)

        assert specs is not None
        assert specs.brand == "Samsung"
        assert specs.model == "QN90D 55"
        assert specs.size_inches == 55.0
        assert specs.refresh_rate_hz == 144
        assert specs.price == 1299.0
        assert specs.currency == "EUR"  # no dedicated column, default holds
        assert specs.affiliate_url == "https://example.com/samsung"
        assert specs.product_images_prefix == "SAMSUNG_QN90D_55/"
        assert provider.row_errors == []

    def test_blank_price_becomes_none_not_zero(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        specs = provider._row_to_specs(_row(**{"Precio (€)": ""}), row_number=2)

        assert specs is not None
        assert specs.price is None

    def test_decimal_comma_is_accepted(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        specs = provider._row_to_specs(
            _row(**{"Tamaño pantalla (pulgadas)": "55,5", "Precio (€)": "1299,90"}),
            row_number=2,
        )

        assert specs is not None
        assert specs.size_inches == 55.5
        assert specs.price == 1299.90

    def test_missing_required_column_skips_row_with_error(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        specs = provider._row_to_specs(_row(**{"Marca": ""}), row_number=5)

        assert specs is None
        assert len(provider.row_errors) == 1
        assert "row 5" in provider.row_errors[0]
        assert "brand" in provider.row_errors[0]

    def test_non_numeric_size_skips_row_with_error_instead_of_raising(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        specs = provider._row_to_specs(
            _row(**{"Tamaño pantalla (pulgadas)": "not-a-number"}), row_number=7
        )

        assert specs is None
        assert len(provider.row_errors) == 1
        assert "row 7" in provider.row_errors[0]

    def test_custom_column_map_overrides_default(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(
            sheet_id="sheet-1",
            column_map={"brand": "Fabricante"},
        )
        row = _row()
        del row["Marca"]
        row["Fabricante"] = "LG"
        specs = provider._row_to_specs(row, row_number=2)

        assert specs is not None
        assert specs.brand == "LG"

    def test_pros_cons_parse_when_mapped_to_a_column(self):
        # This sheet has no Pros/Cons columns by default (see
        # DEFAULT_SHEET_COLUMN_MAP) — but the pipe-split still works once
        # someone adds the columns and maps them.
        provider = tv_specs.GoogleSheetTVSpecsProvider(
            sheet_id="sheet-1",
            column_map={"pros": "Pros", "cons": "Contras"},
        )
        row = _row(**{"Pros": "Mini-LED contrast | 144Hz", "Contras": "Pricey | Tizen ads"})
        specs = provider._row_to_specs(row, row_number=2)

        assert specs is not None
        assert specs.pros == ["Mini-LED contrast", "144Hz"]
        assert specs.cons == ["Pricey", "Tizen ads"]


class TestLoadCachingAndRefresh:
    def test_second_call_within_ttl_does_not_refetch(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(
            sheet_id="sheet-1", cache_ttl_seconds=60
        )
        with patch.object(
            provider, "_fetch_rows_via_public_csv", return_value=[_row()]
        ) as mock_fetch:
            first = provider.list_all()
            second = provider.list_all()

        assert mock_fetch.call_count == 1
        assert first == second

    def test_force_refresh_bypasses_cache(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(
            sheet_id="sheet-1", cache_ttl_seconds=60
        )
        with patch.object(
            provider, "_fetch_rows_via_public_csv", return_value=[_row()]
        ) as mock_fetch:
            provider.list_all()
            provider.list_all(force_refresh=True)

        assert mock_fetch.call_count == 2

    def test_expired_ttl_refetches_automatically(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(
            sheet_id="sheet-1", cache_ttl_seconds=0
        )
        with patch.object(
            provider, "_fetch_rows_via_public_csv", return_value=[_row()]
        ) as mock_fetch:
            provider.list_all()
            provider.list_all()

        assert mock_fetch.call_count == 2

    def test_credentials_path_uses_service_account_not_public_csv(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(
            sheet_id="sheet-1", credentials_path="/tmp/creds.json"
        )
        with patch.object(
            provider, "_fetch_rows_via_service_account", return_value=[_row()]
        ) as mock_sa, patch.object(
            provider, "_fetch_rows_via_public_csv"
        ) as mock_csv:
            provider.list_all()

        mock_sa.assert_called_once()
        mock_csv.assert_not_called()

    def test_row_errors_reset_between_loads(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(
            sheet_id="sheet-1", cache_ttl_seconds=0
        )
        with patch.object(
            provider,
            "_fetch_rows_via_public_csv",
            side_effect=[[_row(**{"Marca": ""})], [_row()]],
        ):
            provider.list_all()
            assert len(provider.row_errors) == 1
            provider.list_all()
            assert provider.row_errors == []


class TestFetchAndSearch:
    def test_fetch_matches_by_brand_and_model_case_insensitively(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        with patch.object(provider, "_fetch_rows_via_public_csv", return_value=[_row()]):
            specs = provider.fetch("samsung", "qn90d 55")
        assert specs.brand == "Samsung"

    def test_fetch_raises_not_found_for_unknown_pair(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        with patch.object(provider, "_fetch_rows_via_public_csv", return_value=[_row()]):
            with pytest.raises(tv_specs.TVSpecsNotFoundError):
                provider.fetch("LG", "C4 55")


class TestPublicCsvFetch:
    def test_html_login_page_raises_permission_error(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        mock_response = type(
            "Resp",
            (),
            {
                "status_code": 200,
                "text": "<!DOCTYPE html><html>login</html>",
                "raise_for_status": lambda self: None,
            },
        )()
        with patch("requests.get", return_value=mock_response):
            with pytest.raises(PermissionError):
                provider._fetch_rows_via_public_csv()

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_401_403_raise_permission_error(self, status_code):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        mock_response = type(
            "Resp", (), {"status_code": status_code, "text": ""}
        )()
        with patch("requests.get", return_value=mock_response):
            with pytest.raises(PermissionError):
                provider._fetch_rows_via_public_csv()

    def test_parses_real_csv_body(self):
        provider = tv_specs.GoogleSheetTVSpecsProvider(sheet_id="sheet-1")
        csv_text = "Marca,Modelo (comercial)\nSamsung,QN90D 55\n"
        mock_response = type(
            "Resp",
            (),
            {
                "status_code": 200,
                "text": csv_text,
                "raise_for_status": lambda self: None,
            },
        )()
        with patch("requests.get", return_value=mock_response):
            rows = provider._fetch_rows_via_public_csv()
        assert rows == [{"Marca": "Samsung", "Modelo (comercial)": "QN90D 55"}]


class TestGetTvSpecsProviderFactory:
    def test_google_sheets_backend_without_sheet_id_raises(self):
        cfg = type(
            "Cfg",
            (),
            {"app": {"tv_review_specs_source": "google_sheets"}, "google_sheets": {}},
        )()
        with pytest.raises(ValueError):
            tv_specs.get_tv_specs_provider(app_config=cfg)

    def test_google_sheets_backend_wires_config_through(self):
        cfg = type(
            "Cfg",
            (),
            {
                "app": {"tv_review_specs_source": "google_sheets"},
                "google_sheets": {
                    "sheet_id": "abc123",
                    "worksheet_gid": 7,
                    "credentials_path": "/tmp/creds.json",
                    "cache_ttl_seconds": 30,
                    "columns": {"brand": "Fabricante"},
                },
            },
        )()
        provider = tv_specs.get_tv_specs_provider(app_config=cfg)

        assert isinstance(provider, tv_specs.GoogleSheetTVSpecsProvider)
        assert provider.sheet_id == "abc123"
        assert provider.worksheet_gid == 7
        assert provider.credentials_path == "/tmp/creds.json"
        assert provider.cache_ttl_seconds == 30
        assert provider.column_map["brand"] == "Fabricante"
        # unspecified fields still fall back to the built-in defaults
        assert provider.column_map["model"] == "Modelo (comercial)"
