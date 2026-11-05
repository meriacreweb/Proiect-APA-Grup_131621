"""Numerical sanity checks and safe-output checks for image analysis."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from PIL import Image

from backend.app.metrics_analyzer import (
    analyze_images,
    main,
    paired_chi_square,
    save_histogram,
)


def test_identical_image_has_json_safe_infinite_psnr() -> None:
    pixels = np.random.default_rng(252).integers(0, 256, size=(16, 16, 3), dtype=np.uint8)
    source = Image.fromarray(pixels)
    metrics = analyze_images(source, source.copy())
    assert metrics["mse"] == 0
    assert metrics["ssim"] == pytest.approx(1)
    assert metrics["psnr_db"] is None
    assert metrics["psnr_infinite"] is True
    assert metrics["changed_channels"] == 0
    assert metrics["changed_channel_ratio"] == 0
    json.dumps(metrics, allow_nan=False)


def test_one_level_difference_matches_psnr_formula() -> None:
    metrics = analyze_images(Image.new("RGB", (8, 8), (0, 0, 0)), Image.new("RGB", (8, 8), (1, 1, 1)))
    assert metrics["mse"] == 1
    assert metrics["psnr_db"] == pytest.approx(10 * math.log10(255**2))
    assert metrics["psnr_infinite"] is False
    assert metrics["changed_channels"] == 8 * 8 * 3
    assert metrics["changed_channel_ratio"] == 1
    assert 0 < metrics["ssim"] < 1


def test_maximum_difference_does_not_overflow_uint8() -> None:
    metrics = analyze_images(Image.new("RGB", (8, 8), "black"), Image.new("RGB", (8, 8), "white"))
    assert metrics["mse"] == 65025
    assert metrics["psnr_db"] == pytest.approx(0)


def test_changed_channel_ratio_counts_channels_instead_of_pixels() -> None:
    before = np.zeros((8, 8, 3), dtype=np.uint8)
    after = before.copy()
    after[0, 0, 0] = 1
    metrics = analyze_images(Image.fromarray(before), Image.fromarray(after))
    assert metrics["changed_channels"] == 1
    assert metrics["changed_channel_ratio"] == pytest.approx(1 / 192)
    assert metrics["mse"] == pytest.approx(1 / 192)


@pytest.mark.parametrize("side, window", [(3, 3), (4, 3), (6, 5), (7, 7), (8, 7)])
def test_small_images_use_adaptive_odd_ssim_window(side: int, window: int) -> None:
    source = Image.new("RGB", (side, side), (25, 50, 75))
    assert analyze_images(source, source)["ssim_window_size"] == window


def test_dimension_mismatch_and_too_small_images_are_explicit() -> None:
    with pytest.raises(ValueError, match="aceleași dimensiuni"):
        analyze_images(Image.new("RGB", (8, 8)), Image.new("RGB", (9, 8)))
    with pytest.raises(ValueError, match="3 × 3"):
        analyze_images(Image.new("RGB", (2, 8)), Image.new("RGB", (2, 8)))


def test_metrics_normalize_color_modes_and_exclude_alpha() -> None:
    original = Image.new("RGBA", (8, 8), (10, 20, 30, 0))
    stego = Image.new("RGBA", (8, 8), (10, 20, 30, 255))
    metrics = analyze_images(original, stego)
    assert metrics["channels"] == 3
    assert metrics["psnr_infinite"] is True
    assert analyze_images(original.convert("L"), original.convert("L"))["ssim"] == 1


def test_paired_chi_square_balanced_pairs_and_constant_image() -> None:
    values = np.arange(256, dtype=np.uint8).reshape((16, 16))
    balanced_rgb = np.repeat(values[:, :, None], 3, axis=2)
    balanced = paired_chi_square(balanced_rgb)
    for channel in balanced.values():
        assert channel["statistic"] == 0
        assert channel["degrees_of_freedom"] == 128
        assert channel["p_value"] == 1
        assert channel["even_count"] == channel["odd_count"] == 128
        assert channel["pairs_with_expected_count_below_5"] == 128

    constant = paired_chi_square(Image.new("RGB", (16, 16), "black"))
    for channel in constant.values():
        assert channel["statistic"] == 256
        assert channel["degrees_of_freedom"] == 1
        assert channel["p_value"] < 1e-40
        assert channel["sample_count"] == 256


def test_paired_chi_square_rejects_non_rgb_or_non_byte_arrays() -> None:
    with pytest.raises(ValueError, match="RGB uint8"):
        paired_chi_square(np.zeros((8, 8), dtype=np.uint8))
    with pytest.raises(ValueError, match="RGB uint8"):
        paired_chi_square(np.zeros((8, 8, 3), dtype=np.float64))


def test_histogram_is_a_real_readable_png(tmp_path) -> None:
    original = Image.new("RGB", (16, 16), (100, 200, 50))
    stego = original.copy()
    stego.putpixel((0, 0), (101, 200, 51))
    output = save_histogram(original, stego, tmp_path / "nested" / "histogram.png")
    assert output.stat().st_size > 1000
    with Image.open(output) as rendered:
        assert rendered.format == "PNG"
        assert rendered.width > 1000
        assert rendered.height > 500
        rendered.verify()


def test_cli_writes_metrics_and_histogram_with_infinite_psnr(tmp_path) -> None:
    image_path = tmp_path / "source.png"
    Image.new("RGB", (16, 16), (18, 63, 96)).save(image_path)
    output = tmp_path / "analysis"
    assert main([str(image_path), str(image_path), "--output-dir", str(output)]) == 0
    metrics_text = (output / "metrics.json").read_text(encoding="utf-8")
    assert "Infinity" not in metrics_text
    assert "NaN" not in metrics_text
    metrics = json.loads(metrics_text)
    assert metrics["psnr_infinite"] is True
    assert metrics["psnr_db"] is None
    assert (output / "histogram.png").is_file()


def test_cli_reports_invalid_image_without_traceback(tmp_path, capsys) -> None:
    image_path = tmp_path / "bad.png"
    image_path.write_bytes(b"This is not a PNG")
    assert main([str(image_path), str(image_path), "--output-dir", str(tmp_path / "analysis")]) == 2
    output = capsys.readouterr()
    assert "Analiza nu a putut fi realizată" in output.err
    assert "Traceback" not in output.err
    assert not (tmp_path / "analysis" / "metrics.json").exists()
