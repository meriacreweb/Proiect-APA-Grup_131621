"""Reproducible experiment on synthetic images; never physical measurements.

Run from the repository root::

    python -m scripts.benchmark --sizes 256 512 1024 --message-bytes 128 1024 16384

The seeded generator controls synthetic RGB pixels and ASCII message contents.
Cryptographic salts and IVs deliberately remain OS-random on every encryption.
Timing uses perf_counter while tracemalloc is enabled: it measures this local
instrumented process, not a production throughput guarantee. Traced allocation
peaks do not measure complete process RSS or every native-library allocation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import platform
import statistics
import sys
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from importlib import metadata
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from PIL import Image

# Support both `python -m scripts.benchmark` and `python scripts/benchmark.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import crypto_engine
from backend.app.constants import MAX_PIXELS
from backend.app.crypto_engine import decrypt_message, encrypt_message
from backend.app.errors import AppError
from backend.app.stego_engine import capacity, embed_payload, extract_payload

STAGES = ("encrypt", "embed", "extract", "decrypt", "metrics")
BENCHMARK_NOTES = (
    "Synthetic seeded RGB images and synthetic ASCII messages only; no physical measurements.",
    "Cryptographic salts and IVs use the application CSPRNG and are not seeded.",
    "perf_counter wall time is measured with tracemalloc enabled, which adds overhead.",
    "tracemalloc reports traced allocation peaks, not complete process RSS or all native-library memory.",
    "The PNG encoding size is informational; image generation and PNG encoding are outside stage timings.",
    "Results depend on hardware, interpreter, dependency versions, system load, and image/message sizes.",
)


def _measure[T](operation: Callable[[], T]) -> tuple[T, float, int]:
    """Measure a stage separately, always disabling tracing even on failure."""
    if tracemalloc.is_tracing():
        raise RuntimeError("Benchmark cannot own tracemalloc while an external trace is active.")
    tracemalloc.start()
    try:
        started = perf_counter()
        value = operation()
        elapsed_ms = (perf_counter() - started) * 1_000
        _, peak = tracemalloc.get_traced_memory()
        return value, elapsed_ms, peak
    finally:
        tracemalloc.stop()


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("cryptography", "Pillow", "numpy", "scipy", "scikit-image", "matplotlib", "fastapi"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    groups = sorted({(row["width"], row["height"], row["message_bytes"]) for row in rows if row["status"] == "ok"})
    for width, height, message_bytes in groups:
        group = [row for row in rows if row["status"] == "ok" and (row["width"], row["height"], row["message_bytes"]) == (width, height, message_bytes)]
        item: dict[str, Any] = {"width": width, "height": height, "message_bytes": message_bytes, "samples": len(group)}
        for stage in STAGES:
            milliseconds = [row[f"{stage}_ms"] for row in group if row.get(f"{stage}_ms") is not None]
            peaks = [row[f"{stage}_peak_traced_bytes"] for row in group if row.get(f"{stage}_peak_traced_bytes") is not None]
            if milliseconds:
                item[stage] = {
                    "mean_ms": statistics.fmean(milliseconds),
                    "median_ms": statistics.median(milliseconds),
                    "min_ms": min(milliseconds),
                    "max_ms": max(milliseconds),
                    "max_peak_traced_bytes": max(peaks),
                }
        summary.append(item)
    return summary


def run_benchmark(
    *,
    sizes: list[int],
    message_sizes: list[int],
    repeats: int,
    seed: int,
    include_metrics: bool,
) -> dict[str, Any]:
    """Evaluate a verified encrypt/embed/extract/decrypt pipeline per sample."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    # A public password is appropriate only because every benchmark input is
    # generated synthetic data. It is never a production default or user secret.
    password = "Synthetic benchmark password 2026!"
    alphabet = np.frombuffer(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ", dtype=np.uint8)
    for side in sizes:
        source = Image.fromarray(rng.integers(0, 256, (side, side, 3), dtype=np.uint8))
        max_payload_bytes = capacity(source)["max_payload_bytes"]
        for message_bytes in message_sizes:
            if message_bytes > max_payload_bytes:
                rows.append({"width": side, "height": side, "message_bytes": message_bytes, "max_payload_bytes": max_payload_bytes, "status": "skipped", "reason": "message exceeds image capacity"})
                continue
            message = bytes(rng.choice(alphabet, size=message_bytes)).decode("ascii")
            for repetition in range(1, repeats + 1):
                row: dict[str, Any] = {
                    "width": side,
                    "height": side,
                    "message_bytes": len(message.encode("utf-8")),
                    "max_payload_bytes": max_payload_bytes,
                    "utilization_ratio": message_bytes / max_payload_bytes,
                    "repetition": repetition,
                    "status": "ok",
                    "reason": "",
                }
                payload, row["encrypt_ms"], row["encrypt_peak_traced_bytes"] = _measure(partial(encrypt_message, message, password))
                stego, row["embed_ms"], row["embed_peak_traced_bytes"] = _measure(partial(embed_payload, source, payload))
                # A real PNG round trip verifies persisted output, not only the
                # temporary Pillow image returned by embed_payload.
                encoded = io.BytesIO()
                stego.save(encoded, format="PNG")
                row["stego_png_bytes"] = encoded.tell()
                encoded.seek(0)
                with Image.open(encoded) as reopened:
                    reopened.load()
                    persisted = reopened.copy()
                extracted, row["extract_ms"], row["extract_peak_traced_bytes"] = _measure(partial(extract_payload, persisted))
                recovered, row["decrypt_ms"], row["decrypt_peak_traced_bytes"] = _measure(partial(decrypt_message, extracted, password))
                if recovered != message:
                    raise RuntimeError("Benchmark failed: recovered message differs from synthetic input.")
                row["round_trip_verified"] = True
                row["metrics_ms"] = None
                row["metrics_peak_traced_bytes"] = None
                row["psnr_db"] = None
                row["ssim"] = None
                if include_metrics:
                    from backend.app.metrics_analyzer import analyze_images

                    metrics, row["metrics_ms"], row["metrics_peak_traced_bytes"] = _measure(partial(analyze_images, source, persisted))
                    row["psnr_db"] = metrics["psnr_db"]
                    row["ssim"] = metrics["ssim"]
                rows.append(row)
    return {
        "experiment_type": "synthetic-local-instrumented-benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "configuration": {
            "seed": seed,
            "image_sizes": sizes,
            "message_byte_sizes": message_sizes,
            "repeats": repeats,
            "include_metrics": include_metrics,
            "pbkdf2_iterations": getattr(crypto_engine, "PBKDF2_ITERATIONS", None),
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
            "dependencies": _dependency_versions(),
        },
        "notes": list(BENCHMARK_NOTES),
        "samples": rows,
        "summary": _summarize(rows),
    }


def _image_side(value: str) -> int:
    side = int(value)
    if not 8 <= side or side * side > MAX_PIXELS:
        raise argparse.ArgumentTypeError(f"Image side must be at least 8; total pixels must not exceed {MAX_PIXELS}.")
    return side


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark reproductibil cu imagini și mesaje sintetice.")
    parser.add_argument("--sizes", nargs="+", type=_image_side, default=[256, 512, 1024], help="Laturile imaginilor RGB pătrate")
    parser.add_argument("--message-bytes", nargs="+", type=_positive_int, default=[128, 1024, 16384])
    parser.add_argument("--repeats", type=_positive_int, default=3)
    parser.add_argument("--seed", type=int, default=252)
    parser.add_argument("--include-metrics", action="store_true", help="Măsoară suplimentar PSNR/SSIM și chi-square")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/benchmark"))
    args = parser.parse_args(argv)
    if args.seed < 0:
        parser.error("Seed must be nonnegative.")
    try:
        report = run_benchmark(sizes=args.sizes, message_sizes=args.message_bytes, repeats=args.repeats, seed=args.seed, include_metrics=args.include_metrics)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.output_dir / "benchmark.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        csv_path = args.output_dir / "benchmark.csv"
        fields = ["width", "height", "message_bytes", "max_payload_bytes", "utilization_ratio", "repetition", "status", "reason", "round_trip_verified", "stego_png_bytes"]
        fields += [field for stage in STAGES for field in (f"{stage}_ms", f"{stage}_peak_traced_bytes")]
        fields += ["psnr_db", "ssim"]
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(report["samples"])
        completed = sum(row["status"] == "ok" for row in report["samples"])
        print(f"Experimente sintetice verificate: {completed}\nJSON: {json_path}\nCSV: {csv_path}")
        return 0
    except (AppError, OSError, RuntimeError, ValueError) as exc:
        print(f"Benchmark eșuat: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
