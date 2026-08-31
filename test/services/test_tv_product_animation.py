import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import tv_product_animation as tpa
from app.utils import utils


def _write_tiny_jpeg(path: Path) -> Path:
    # Not a real JPEG — animate_product_photo_with_wavespeed only needs
    # readable bytes; it never decodes the image locally.
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    return path


def _write_real_clip_with_audio(path: Path) -> Path:
    subprocess.run(
        [
            utils.get_ffmpeg_binary(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


class TestBuildProductAnimationPrompt:
    def test_includes_brand_and_model(self):
        prompt = tpa.build_product_animation_prompt("Samsung", "QN90D")
        assert "Samsung QN90D" in prompt
        assert "camera movement" in prompt
        assert "no people" in prompt


class TestAnimateProductPhotoWithWavespeed:
    def test_returns_none_without_api_key(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        with patch("app.services.material.get_api_key", side_effect=ValueError("not set")):
            result = tpa.animate_product_photo_with_wavespeed(photo, prompt="test")
        assert result is None

    def test_returns_none_for_unreadable_photo(self, tmp_path):
        missing_photo = tmp_path / "does-not-exist.jpg"
        with patch("app.services.material.get_api_key", return_value="key-123"):
            result = tpa.animate_product_photo_with_wavespeed(missing_photo, prompt="test")
        assert result is None

    def test_returns_none_when_submission_rejected(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 400, "message": "bad request"}
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ):
            result = tpa.animate_product_photo_with_wavespeed(photo, prompt="test")
        assert result is None

    def test_returns_none_when_submission_accepted_without_id(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 200, "data": {}}
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ):
            result = tpa.animate_product_photo_with_wavespeed(photo, prompt="test")
        assert result is None

    def test_returns_none_when_prediction_never_completes(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 200,
            "data": {"id": "pred-123"},
        }
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ), patch(
            "app.services.material._wait_for_wavespeed_prediction", return_value=None
        ):
            result = tpa.animate_product_photo_with_wavespeed(photo, prompt="test")
        assert result is None

    def test_returns_none_when_status_unknown(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 200,
            "data": {"id": "pred-123"},
        }
        from app.services.material import WaveSpeedUnconfirmedTaskError

        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ), patch(
            "app.services.material._wait_for_wavespeed_prediction",
            side_effect=WaveSpeedUnconfirmedTaskError("unknown", prediction_id="pred-123"),
        ):
            result = tpa.animate_product_photo_with_wavespeed(photo, prompt="test")
        assert result is None

    def test_downloads_output_on_success(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_submit_response = MagicMock()
        mock_submit_response.json.return_value = {
            "code": 200,
            "data": {"id": "pred-123"},
        }
        fake_local_path = str(tmp_path / "downloaded.mp4")
        Path(fake_local_path).write_bytes(b"fake-mp4-bytes")

        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_submit_response
        ), patch(
            "app.services.material._wait_for_wavespeed_prediction",
            return_value={"outputs": ["https://cdn.wavespeed.ai/out.mp4"]},
        ), patch(
            "app.services.material.save_video", return_value=fake_local_path
        ) as mock_save:
            result = tpa.animate_product_photo_with_wavespeed(photo, prompt="test")

        assert result == Path(fake_local_path)
        mock_save.assert_called_once()
        call_args = mock_save.call_args
        assert call_args[0][0] == "https://cdn.wavespeed.ai/out.mp4"

    def test_returns_none_when_no_downloadable_output(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_submit_response = MagicMock()
        mock_submit_response.json.return_value = {
            "code": 200,
            "data": {"id": "pred-123"},
        }
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_submit_response
        ), patch(
            "app.services.material._wait_for_wavespeed_prediction",
            return_value={"outputs": []},
        ):
            result = tpa.animate_product_photo_with_wavespeed(photo, prompt="test")
        assert result is None

    def test_clamps_duration_and_resolution_to_supported_values(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 400, "message": "rejected"}
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ) as mock_post:
            tpa.animate_product_photo_with_wavespeed(
                photo, prompt="test", duration=99, resolution="8k"
            )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["duration"] == 15  # clamped to minimax-h3's platform max
        assert payload["resolution"] == "480p"  # invalid value falls back to default

    def test_default_model_has_no_audio_field_to_disable(self, tmp_path):
        # minimax-h3 (the default) always returns an audio track with no
        # request-level toggle — muting happens after download instead
        # (see test_downloads_output_on_success's audio-stripping check).
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 400, "message": "rejected"}
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ) as mock_post:
            tpa.animate_product_photo_with_wavespeed(photo, prompt="test")
        payload = mock_post.call_args.kwargs["json"]
        assert "enable_audio" not in payload
        assert "generate_audio" not in payload

    def test_disables_audio_field_when_wan_model_id_used(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 400, "message": "rejected"}
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ) as mock_post:
            tpa.animate_product_photo_with_wavespeed(
                photo, prompt="test", model_id="alibaba/wan-3.0/image-to-video"
            )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["enable_audio"] is False

    def test_uses_seedance_audio_field_when_model_id_overridden(self, tmp_path):
        photo = _write_tiny_jpeg(tmp_path / "photo.jpg")
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 400, "message": "rejected"}
        with patch("app.services.material.get_api_key", return_value="key-123"), patch(
            "app.services.material.requests.post", return_value=mock_response
        ) as mock_post:
            tpa.animate_product_photo_with_wavespeed(
                photo,
                prompt="test",
                model_id="bytedance/seedance-2.0-fast/image-to-video",
            )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["generate_audio"] is False
        assert "enable_audio" not in payload


class TestStripAudioTrack:
    def test_removes_audio_stream_from_a_real_clip(self, tmp_path):
        clip = _write_real_clip_with_audio(tmp_path / "clip.mp4")

        result = tpa._strip_audio_track(clip)

        probe = subprocess.run(
            [utils.get_ffmpeg_binary(), "-i", str(result)],
            capture_output=True,
            text=True,
        )
        assert "Audio:" not in probe.stderr
        assert "Video:" in probe.stderr
        assert not clip.exists()  # original (with audio) was replaced

    def test_returns_original_path_when_ffmpeg_fails(self, tmp_path):
        not_a_video = tmp_path / "not-a-video.mp4"
        not_a_video.write_bytes(b"not actually a video file")

        result = tpa._strip_audio_track(not_a_video)

        assert result == not_a_video
        assert not_a_video.exists()  # left untouched on failure


class TestAnimateProductPhotos:
    def test_falls_back_to_original_photo_on_failure(self, tmp_path):
        photo1 = _write_tiny_jpeg(tmp_path / "photo1.jpg")
        photo2 = _write_tiny_jpeg(tmp_path / "photo2.jpg")

        with patch.object(
            tpa, "animate_product_photo_with_wavespeed", return_value=None
        ):
            results = tpa.animate_product_photos(
                [photo1, photo2], brand="Xiaomi", model="TV F Pro 43"
            )

        # every input photo is accounted for, unanimated ones pass through
        # unchanged so preprocess_video()'s Ken Burns path picks them up
        assert results == [photo1, photo2]

    def test_uses_animated_clip_when_available(self, tmp_path):
        photo1 = _write_tiny_jpeg(tmp_path / "photo1.jpg")
        animated_clip = tmp_path / "animated.mp4"

        with patch.object(
            tpa, "animate_product_photo_with_wavespeed", return_value=animated_clip
        ):
            results = tpa.animate_product_photos(
                [photo1], brand="Xiaomi", model="TV F Pro 43"
            )

        assert results == [animated_clip]
