"""Sequential, row-major RGB LSB substitution, most significant bit first."""

import struct

import numpy as np
from PIL import Image

from .constants import HEADER_SIZE, MAGIC, MAX_MESSAGE_BYTES, MAX_PIXELS
from .crypto_engine import EncryptedPayload, validate_payload
from .errors import AppError

HEADER = struct.Struct(">4s16s12s16sI")
assert HEADER.size == HEADER_SIZE


def _validate_image(image: Image.Image) -> None:
    if image.mode not in ("RGB", "RGBA"):
        raise AppError("INVALID_MODE", "Motorul LSB acceptă imagini normalizate RGB sau RGBA.", 422)
    if image.width < 1 or image.height < 1 or image.width * image.height > MAX_PIXELS:
        raise AppError("INVALID_DIMENSIONS", "Dimensiunile imaginii depășesc limita de 12 megapixeli.", 422)


def capacity(image: Image.Image) -> dict:
    _validate_image(image)
    total = image.width * image.height * 3 // 8
    usable = max(0, min(total - HEADER_SIZE, MAX_MESSAGE_BYTES))
    return {
        "width": image.width, "height": image.height,
        "total_capacity_bytes": total, "header_bytes": HEADER_SIZE,
        "max_payload_bytes": usable,
        "max_characters_ascii": usable, "max_characters_utf8_4byte": usable // 4,
        "format": image.format or "PNG", "mode": image.mode,
    }


def build_packet(payload: EncryptedPayload) -> bytes:
    validate_payload(payload)
    return HEADER.pack(MAGIC, payload.salt, payload.iv, payload.tag, len(payload.ciphertext)) + payload.ciphertext


def embed_payload(image: Image.Image, payload: EncryptedPayload) -> Image.Image:
    _validate_image(image)
    packet = build_packet(payload)
    # Check before unpackbits allocates eight times the packet size.
    if len(packet) > image.width * image.height * 3 // 8:
        raise AppError("CAPACITY_EXCEEDED", "Mesajul și header-ul de 52 de octeți nu încap în imagine.", 422)
    pixels = np.array(image, dtype=np.uint8, copy=True)
    rgb = pixels[:, :, :3].copy().reshape(-1)
    bits = np.unpackbits(np.frombuffer(packet, dtype=np.uint8), bitorder="big")
    rgb[:bits.size] = (rgb[:bits.size] & 0xFE) | bits
    pixels[:, :, :3] = rgb.reshape(image.height, image.width, 3)
    result = Image.fromarray(pixels)
    # Deliberately do not copy EXIF, text chunks, ICC profiles or other metadata.
    return result


def extract_payload(image: Image.Image) -> EncryptedPayload:
    _validate_image(image)
    available = image.width * image.height * 3 // 8
    if available < HEADER_SIZE:
        raise AppError("IMAGE_TOO_SMALL", "Imaginea nu poate conține header-ul STEG.")
    # Allocate only the fixed image-sized RGB array. Untrusted length never drives allocation.
    rgb = np.asarray(image, dtype=np.uint8)[:, :, :3].reshape(-1)
    header = np.packbits(rgb[:HEADER_SIZE * 8] & 1, bitorder="big").tobytes()
    magic, salt, iv, tag, length = HEADER.unpack(header)
    if magic != MAGIC:
        raise AppError("NO_PAYLOAD", "Imaginea nu conține un mesaj STEG recunoscut.")
    if length > MAX_MESSAGE_BYTES or length > available - HEADER_SIZE:
        raise AppError("INVALID_LENGTH", "Header-ul este corupt: lungimea declarată nu este validă.")
    end = (HEADER_SIZE + length) * 8
    ciphertext = np.packbits(rgb[HEADER_SIZE * 8:end] & 1, bitorder="big").tobytes()
    payload = EncryptedPayload(salt, iv, tag, ciphertext)
    validate_payload(payload)
    return payload

