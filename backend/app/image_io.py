"""Bounded decoding with signature checks and no metadata propagation."""

import warnings
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from .constants import MAX_PIXELS, MAX_UPLOAD_BYTES
from .errors import AppError


def load_image_bytes(data: bytes) -> Image.Image:
    if not data:
        raise AppError("EMPTY_FILE", "Fișierul încărcat este gol.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("FILE_TOO_LARGE", "Imaginea depășește limita de 10 MiB.", 413)
    if not data.startswith((b"\x89PNG\r\n\x1a\n", b"BM")):
        raise AppError("UNSUPPORTED_IMAGE", "Încărcați o imagine PNG sau BMP fără pierderi.", 415)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=("PNG", "BMP")) as probe:
                if probe.width * probe.height > MAX_PIXELS:
                    raise AppError("IMAGE_TOO_LARGE", "Imaginea depășește limita de 12 megapixeli.", 413)
                if getattr(probe, "n_frames", 1) != 1:
                    raise AppError("ANIMATED_IMAGE", "Imaginile animate nu sunt acceptate.", 415)
                if probe.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
                    raise AppError("UNSUPPORTED_MODE", "Folosiți o imagine pe 8 biți RGB, RGBA sau tonuri de gri.", 415)
                probe.verify()
            with Image.open(BytesIO(data), formats=("PNG", "BMP")) as source:
                source.load()  # force decoding while the input stream is alive
                source_format = source.format
                alpha = "A" in source.getbands() or "transparency" in source.info
                image = source.convert("RGBA" if alpha else "RGB")
                image.info.clear()
                image.format = source_format
                return image
    except AppError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AppError("IMAGE_TOO_LARGE", "Imaginea necesită prea multă memorie.", 413) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, OverflowError) as exc:
        raise AppError("INVALID_IMAGE", "Imaginea este invalidă, trunchiată sau coruptă.") from exc


def png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    # Build a fresh image so no input metadata or untrusted file name is serialized.
    clean = Image.frombytes(image.mode, image.size, image.tobytes())
    clean.save(output, format="PNG", compress_level=6)
    return output.getvalue()

