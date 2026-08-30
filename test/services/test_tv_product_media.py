from unittest.mock import MagicMock, patch

import pytest

from app.services import tv_product_media as tpm


class TestSortKey:
    def test_numeric_prefix_sorts_numerically_not_lexically(self):
        keys = ["SAMSUNG/10_extra.jpg", "SAMSUNG/2_design.jpg", "SAMSUNG/01_screen.jpg"]
        assert sorted(keys, key=tpm._sort_key) == [
            "SAMSUNG/01_screen.jpg",
            "SAMSUNG/2_design.jpg",
            "SAMSUNG/10_extra.jpg",
        ]

    def test_files_without_numeric_prefix_sort_last(self):
        keys = ["SAMSUNG/zz_notes.txt", "SAMSUNG/01_screen.jpg"]
        assert sorted(keys, key=tpm._sort_key) == [
            "SAMSUNG/01_screen.jpg",
            "SAMSUNG/zz_notes.txt",
        ]


class TestListProductMediaKeysViaApi:
    def test_blank_prefix_returns_empty_without_touching_r2(self):
        with patch.object(tpm, "get_r2_client") as mock_get_client:
            assert tpm.list_product_media_keys_via_api("") == []
            assert tpm.list_product_media_keys_via_api("   ") == []
        mock_get_client.assert_not_called()

    def test_missing_bucket_name_raises_not_configured(self):
        with patch.object(tpm, "_r2_config", return_value={}):
            with pytest.raises(tpm.R2NotConfiguredError):
                tpm.list_product_media_keys_via_api("SAMSUNG_QN90D_55/")

    def test_lists_and_sorts_objects_under_prefix(self):
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "SAMSUNG_QN90D_55/02_design.jpg"},
                {"Key": "SAMSUNG_QN90D_55/01_screen.jpg"},
                {"Key": "SAMSUNG_QN90D_55/"},  # folder placeholder, must be skipped
            ],
            "IsTruncated": False,
        }
        with patch.object(
            tpm, "_r2_config", return_value={"bucket_name": "tv-assets"}
        ), patch.object(tpm, "get_r2_client", return_value=mock_client):
            keys = tpm.list_product_media_keys_via_api("SAMSUNG_QN90D_55/")

        assert keys == [
            "SAMSUNG_QN90D_55/01_screen.jpg",
            "SAMSUNG_QN90D_55/02_design.jpg",
        ]

    def test_paginates_until_not_truncated(self):
        mock_client = MagicMock()
        mock_client.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": "P/01_a.jpg"}],
                "IsTruncated": True,
                "NextContinuationToken": "token-2",
            },
            {"Contents": [{"Key": "P/02_b.jpg"}], "IsTruncated": False},
        ]
        with patch.object(
            tpm, "_r2_config", return_value={"bucket_name": "tv-assets"}
        ), patch.object(tpm, "get_r2_client", return_value=mock_client):
            keys = tpm.list_product_media_keys_via_api("P/")

        assert keys == ["P/01_a.jpg", "P/02_b.jpg"]
        assert mock_client.list_objects_v2.call_count == 2


class TestListProductMediaUrlsViaPublicProbe:
    def test_blank_prefix_or_base_url_returns_empty(self):
        assert tpm.list_product_media_urls_via_public_probe("", "https://x.com") == []
        assert tpm.list_product_media_urls_via_public_probe("P/", "") == []

    def test_stops_at_first_matching_extension_per_index(self):
        def fake_head(url, timeout):
            resp = MagicMock()
            resp.status_code = 200 if url.endswith("01.jpg") else 404
            return resp

        with patch("requests.head", side_effect=fake_head):
            urls = tpm.list_product_media_urls_via_public_probe(
                "SAMSUNG/", "https://cdn.example.com"
            )

        assert urls == ["https://cdn.example.com/SAMSUNG/01.jpg"]


class TestDownloadAndCacheProductMedia:
    def test_blank_prefix_returns_empty_without_any_r2_call(self):
        with patch.object(tpm, "list_product_media_keys_via_api") as mock_list:
            assert tpm.download_and_cache_product_media("") == []
            assert tpm.download_and_cache_product_media("   ") == []
        mock_list.assert_not_called()

    def test_no_objects_found_returns_empty_for_fallback(self):
        with patch.object(tpm, "list_product_media_keys_via_api", return_value=[]):
            assert tpm.download_and_cache_product_media("EMPTY_PREFIX/") == []
