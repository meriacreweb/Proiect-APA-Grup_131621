"""Deterministic, in-memory images and isolated API instances."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def rgb_image() -> Image.Image:
    image = Image.new("RGB", (64, 64))
    image.putdata(
        [((x * 13 + y) % 256, (x + y * 17) % 256, (x * 7 + y * 3) % 256)
         for y in range(64) for x in range(64)]
    )
    return image


@pytest.fixture
def png_bytes(rgb_image: Image.Image) -> bytes:
    stream = io.BytesIO()
    rgb_image.save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def password() -> str:
    return "Parolă lungă de test 🔐 2026"


@pytest.fixture
def client():
    from backend.app.config import Settings
    from backend.app.main import create_app

    application = create_app(Settings(rate_limit_requests=1000))
    with TestClient(application) as test_client:
        yield test_client
