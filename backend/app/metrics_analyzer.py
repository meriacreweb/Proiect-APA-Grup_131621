"""RGB quality metrics and an explicitly exploratory LSB histogram analysis.

Run from the project root::

    python -m backend.app.metrics_analyzer cover.png stego.png --output-dir analysis

PSNR = 10 log10(255^2 / MSE), where MSE is the mean squared error over
all RGB channel values, calculated in float64 to prevent uint8 overflow.
Identical images have infinite PSNR; JSON represents that value with null and
``psnr_infinite=true``. SSIM uses scikit-image's uniform-window implementation
with an explicit 255 data range, averaged over the three channels. The default
7x7 window is reduced to the largest valid odd size for smaller images.

The paired-value chi-square calculation compares counts in (0, 1), (2, 3),
..., (254, 255). This is an educational heuristic, not a calibrated detector.
Natural image statistics and localized/sequential embedding affect the result.
No returned p-value measures the probability that an image contains a secret.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.stats import chi2
from skimage.metrics import structural_similarity

from .errors import AppError

CHANNEL_NAMES = ("red", "green", "blue")
CHI_SQUARE_INTERPRETATION = (
    "Exploratory paired-value histogram heuristic; a p-value is not the "
    "probability of hidden data and does not establish steganographic or "
    "cryptographic security. Full-image histograms can dilute localized "
    "embedding, and natural images can produce similar distributions. "
    "Sparse pair counts weaken the asymptotic chi-square approximation."
)


def _rgb_arrays(original: Image.Image, stego: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Normalize color modes without resizing, resampling, or color alteration."""
    if original.size != stego.size:
        raise ValueError("Imaginile trebuie să aibă aceleași dimensiuni pentru analiză.")
    if min(original.size) < 3:
        raise ValueError("Analiza SSIM necesită imagini de cel puțin 3 × 3 pixeli.")
    return (
        np.asarray(original.convert("RGB"), dtype=np.uint8),
        np.asarray(stego.convert("RGB"), dtype=np.uint8),
    )


def paired_chi_square(image: Image.Image | np.ndarray) -> dict[str, Any]:
    """Return one paired-bin chi-square statistic per RGB channel.

    With a pair's expected even/odd counts both equal to half the pair total,
    Pearson's two-bin contribution simplifies to (even-odd)^2/(even+odd).
    Each nonempty pair contributes one degree of freedom. Empty pairs are
    ignored; no one-degree correction is applied across independently
    conditioned pairs. Expected counts below five are reported explicitly.
    """
    rgb = np.asarray(image.convert("RGB") if isinstance(image, Image.Image) else image)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("Testul chi-square necesită un tablou RGB uint8.")
    channels: dict[str, Any] = {}
    for index, name in enumerate(CHANNEL_NAMES):
        histogram = np.bincount(rgb[:, :, index].reshape(-1), minlength=256)
        even = histogram[0::2].astype(np.float64)
        odd = histogram[1::2].astype(np.float64)
        totals = even + odd
        nonempty = totals > 0
        degrees = int(np.count_nonzero(nonempty))
        statistic = float(np.sum((even[nonempty] - odd[nonempty]) ** 2 / totals[nonempty]))
        channels[name] = {
            "statistic": statistic,
            "degrees_of_freedom": degrees,
            "p_value": float(chi2.sf(statistic, degrees)) if degrees else None,
            "sample_count": int(histogram.sum()),
            "nonempty_pairs": degrees,
            "pairs_with_expected_count_below_5": int(np.count_nonzero(nonempty & (totals < 10))),
            "even_count": int(histogram[0::2].sum()),
            "odd_count": int(histogram[1::2].sum()),
        }
    return channels


def analyze_images(original: Image.Image, stego: Image.Image) -> dict[str, Any]:
    """Compare decoded RGB images and return a strictly JSON-safe result.

    Alpha is outside the RGB steganographic channel and is excluded. Metadata
    and compressed PNG/BMP file sizes do not affect these decoded-pixel metrics.
    A large PSNR or SSIM describes visual fidelity, not resistance to detection.
    """
    source, result = _rgb_arrays(original, stego)
    difference = source.astype(np.float64) - result.astype(np.float64)
    mse = float(np.mean(np.square(difference)))
    identical = mse == 0.0
    shortest_side = min(source.shape[:2])
    window_size = min(7, shortest_side if shortest_side % 2 else shortest_side - 1)
    ssim = float(
        structural_similarity(source, result, channel_axis=2, data_range=255, win_size=window_size)
    )
    changed = int(np.count_nonzero(source != result))
    return {
        "psnr_db": None if identical else float(10.0 * math.log10(255.0**2 / mse)),
        "psnr_infinite": identical,
        "ssim": ssim,
        "mse": mse,
        "width": original.width,
        "height": original.height,
        "channels": 3,
        "changed_channels": changed,
        "changed_channel_ratio": changed / source.size,
        "ssim_window_size": window_size,
        "chi_square": {
            "method": "paired-value-pearson-full-image-rgb",
            "formula": "sum((count_even - count_odd)^2 / (count_even + count_odd))",
            "interpretation": CHI_SQUARE_INTERPRETATION,
            "original": paired_chi_square(source),
            "stego": paired_chi_square(result),
        },
    }


def save_histogram(original: Image.Image, stego: Image.Image, output: str | Path) -> Path:
    """Save RGB histograms and even/odd LSB counts without a GUI backend."""
    # The Agg canvas avoids global pyplot state and works on a headless server.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    source, result = _rgb_arrays(original, stego)
    figure = Figure(figsize=(14, 7.5), constrained_layout=True)
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 3)
    for index, (name, color) in enumerate(zip(CHANNEL_NAMES, ("#dc2626", "#16a34a", "#2563eb"), strict=True)):
        before = np.bincount(source[:, :, index].reshape(-1), minlength=256)
        after = np.bincount(result[:, :, index].reshape(-1), minlength=256)
        axes[0, index].plot(np.arange(256), before, color=color, linewidth=1.3, label="Original")
        axes[0, index].plot(np.arange(256), after, color="#171717", linewidth=0.9, alpha=0.75, label="Stego")
        axes[0, index].set(title=f"{name.capitalize()} channel", xlabel="Channel value", ylabel="Count", xlim=(0, 255))
        axes[0, index].legend()
        positions = np.arange(2)
        axes[1, index].bar(positions - 0.18, (before[0::2].sum(), before[1::2].sum()), 0.36, color=color, label="Original")
        axes[1, index].bar(positions + 0.18, (after[0::2].sum(), after[1::2].sum()), 0.36, color="#737373", label="Stego")
        axes[1, index].set(title=f"{name.capitalize()}: LSB distribution", ylabel="Count")
        axes[1, index].set_xticks(positions, ("LSB = 0", "LSB = 1"))
        axes[1, index].legend()
    figure.suptitle("RGB histogram and LSB comparison — exploratory analysis")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, format="png", dpi=150)
    figure.clear()
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analizează fidelitatea RGB și distribuția LSB a două imagini lossless.")
    parser.add_argument("original", type=Path, help="Imaginea originală PNG/BMP")
    parser.add_argument("stego", type=Path, help="Imaginea stego PNG/BMP")
    parser.add_argument("--output-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args(argv)
    try:
        # Reuse the API's bounded, verified decoder rather than trusting a file
        # extension or bypassing the decompression-bomb protections.
        from .image_io import load_image_bytes

        original = load_image_bytes(args.original.read_bytes())
        stego = load_image_bytes(args.stego.read_bytes())
        metrics = analyze_images(original, stego)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        histogram_path = save_histogram(original, stego, args.output_dir / "histogram.png")
        metrics_path = args.output_dir / "metrics.json"
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Metrici: {metrics_path}\nHistogramă: {histogram_path}")
        return 0
    except (AppError, OSError, ValueError) as exc:
        print(f"Analiza nu a putut fi realizată: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
