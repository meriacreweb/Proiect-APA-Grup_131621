"""Exact wire format, bounded extraction, and lossless image handling."""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from backend.app.crypto_engine import EncryptedPayload
from backend.app.errors import AppError
from backend.app.image_io import load_image_bytes
from backend.app.image_io import png_bytes as encode_png
from backend.app.stego_engine import (
    build_packet,
    capacity,
    embed_payload,
    extract_payload,
)


def sample_payload(size=29):
    return EncryptedPayload(
        salt=bytes(range(16)), iv=bytes(range(12)), tag=bytes(range(16, 32)),
        ciphertext=bytes((i * 71 + 9) % 256 for i in range(size)),
    )


def independent_embed(image: Image.Image, packet: bytes) -> Image.Image:
    """Reference row-major, RGB-only, most-significant-bit-first encoding."""
    result = image.copy()
    channels = bytearray(result.tobytes())
    stride = len(result.getbands())
    for bit_index in range(len(packet) * 8):
        channel_index = (bit_index // 3) * stride + bit_index % 3
        bit = (packet[bit_index // 8] >> (7 - bit_index % 8)) & 1
        channels[channel_index] = (channels[channel_index] & 0xFE) | bit
    return Image.frombytes(result.mode, result.size, bytes(channels))


def test_capacity_subtracts_exact_binary_header(rgb_image):
    info = capacity(rgb_image)
    assert info["width"] == 64
    assert info["height"] == 64
    assert info["total_capacity_bytes"] == (64 * 64 * 3) // 8
    assert info["header_bytes"] == 52
    assert info["max_payload_bytes"] == 1484
    assert info["max_characters_ascii"] == 1484
    assert info["max_characters_utf8_4byte"] == 371


def test_tiny_image_has_zero_message_capacity():
    info = capacity(Image.new("RGB", (4, 4)))
    assert info["max_payload_bytes"] == 0
    with pytest.raises(AppError):
        embed_payload(Image.new("RGB", (4, 4)), sample_payload(0))


def test_packet_matches_external_big_endian_protocol():
    payload = sample_payload()
    expected = struct.pack(
        ">4s16s12s16sI", b"STEG", payload.salt, payload.iv, payload.tag,
        len(payload.ciphertext),
    ) + payload.ciphertext
    assert build_packet(payload) == expected
    assert len(expected) == 52 + 29


def test_external_lsb_fixture_can_be_extracted(rgb_image):
    payload = sample_payload()
    packet = (
        b"STEG" + payload.salt + payload.iv + payload.tag
        + len(payload.ciphertext).to_bytes(4, "big") + payload.ciphertext
    )
    assert extract_payload(independent_embed(rgb_image, packet)) == payload


def test_embed_exactly_matches_independent_bit_order(rgb_image):
    payload = sample_payload()
    packet = build_packet(payload)
    actual = embed_payload(rgb_image, payload)
    assert actual.tobytes() == independent_embed(rgb_image, packet).tobytes()
    assert rgb_image.tobytes() != actual.tobytes()
    assert extract_payload(actual) == payload


def test_alpha_and_all_unused_channels_are_preserved():
    image = Image.new("RGBA", (32, 32))
    image.putdata([(i % 256, i * 3 % 256, i * 7 % 256, i * 11 % 256) for i in range(1024)])
    original = image.tobytes()
    payload = sample_payload(31)
    result = embed_payload(image, payload)
    assert result.mode == "RGBA"
    changed = result.tobytes()
    used_bits = (52 + 31) * 8
    for pixel_index in range(1024):
        for channel in range(4):
            offset = pixel_index * 4 + channel
            if channel == 3 or pixel_index * 3 + channel >= used_bits:
                assert original[offset] == changed[offset]
            else:
                assert abs(original[offset] - changed[offset]) <= 1
                assert original[offset] & 0xFE == changed[offset] & 0xFE
    assert image.tobytes() == original  # embedding does not mutate the caller's image


def test_payload_can_fill_exact_capacity_and_one_extra_byte_fails(rgb_image):
    max_payload = capacity(rgb_image)["max_payload_bytes"]
    payload = sample_payload(max_payload)
    assert extract_payload(embed_payload(rgb_image, payload)) == payload
    with pytest.raises(AppError) as captured:
        embed_payload(rgb_image, sample_payload(max_payload + 1))
    assert captured.value.status_code == 422


@pytest.mark.parametrize("declared_length", [1485, 4_500_001, 0xFFFFFFFF])
def test_untrusted_payload_length_is_rejected_before_reading(rgb_image, declared_length):
    payload = sample_payload(0)
    packet = struct.pack(
        ">4s16s12s16sI", b"STEG", payload.salt, payload.iv, payload.tag, declared_length
    )
    with pytest.raises(AppError):
        extract_payload(independent_embed(rgb_image, packet))


def test_random_image_and_invalid_magic_are_rejected(rgb_image):
    with pytest.raises(AppError):
        extract_payload(rgb_image)
    invalid = b"FAKE" + build_packet(sample_payload())[4:]
    with pytest.raises(AppError):
        extract_payload(independent_embed(rgb_image, invalid))


@pytest.mark.parametrize("mode,format", [("RGB", "PNG"), ("RGBA", "PNG"), ("L", "PNG"), ("P", "PNG"), ("RGB", "BMP"), ("L", "BMP")])
def test_lossless_input_modes_are_normalized(mode, format):
    image = Image.new(mode, (24, 24))
    stream = io.BytesIO()
    image.save(stream, format=format)
    loaded = load_image_bytes(stream.getvalue())
    assert loaded.size == (24, 24)
    assert loaded.mode in {"RGB", "RGBA"}


def test_palette_transparency_is_preserved():
    image = Image.new("P", (24, 24), color=0)
    image.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    stream = io.BytesIO()
    image.save(stream, format="PNG", transparency=0)
    loaded = load_image_bytes(stream.getvalue())
    assert loaded.mode == "RGBA"
    assert loaded.getpixel((0, 0))[3] == 0


def test_jpeg_is_rejected_even_if_named_png(rgb_image):
    stream = io.BytesIO()
    rgb_image.save(stream, format="JPEG")
    with pytest.raises(AppError) as captured:
        load_image_bytes(stream.getvalue())
    assert captured.value.status_code == 415


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40])
def test_invalid_or_truncated_files_are_rejected(data):
    with pytest.raises(AppError):
        load_image_bytes(data)


def test_truncated_png_is_rejected(png_bytes):
    with pytest.raises(AppError):
        load_image_bytes(png_bytes[: len(png_bytes) // 2])


def test_animated_png_is_rejected(rgb_image):
    stream = io.BytesIO()
    second = Image.new("RGB", rgb_image.size, "white")
    rgb_image.save(stream, format="PNG", save_all=True, append_images=[second], duration=100)
    with pytest.raises(AppError):
        load_image_bytes(stream.getvalue())


def test_pixel_limit_is_checked_before_decompression(png_bytes):
    # Change only a valid IHDR and its CRC. The tiny compressed data must never
    # be decoded using these hostile dimensions or cause a large allocation.
    header = bytearray(png_bytes[16:29])
    header[:8] = struct.pack(">II", 4001, 3000)  # 12,003,000 pixels
    crc = struct.pack(">I", zlib.crc32(b"IHDR" + header) & 0xFFFFFFFF)
    hostile = png_bytes[:16] + header + crc + png_bytes[33:]
    with pytest.raises(AppError) as captured:
        load_image_bytes(hostile)
    assert captured.value.status_code == 413


def test_input_metadata_is_stripped_from_download(rgb_image):
    metadata = PngInfo()
    metadata.add_text("Comment", "Private location and author metadata")
    stream = io.BytesIO()
    rgb_image.save(stream, format="PNG", pnginfo=metadata)
    normalized = load_image_bytes(stream.getvalue())
    assert not normalized.info
    normalized.info["Comment"] = "Metadata added by a caller"
    encoded = encode_png(normalized)
    with Image.open(io.BytesIO(encoded)) as decoded:
        assert "Comment" not in decoded.info
    assert b"Private location" not in encoded
