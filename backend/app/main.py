"""FastAPI REST boundary. Frontend and API share one origin."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

from .config import Settings
from .constants import MAX_ANALYSIS_PIXELS, MAX_MESSAGE_BYTES, MAX_UPLOAD_BYTES
from .crypto_engine import decrypt_message, encrypt_message, password_bytes
from .errors import AppError
from .image_io import load_image_bytes, png_bytes
from .metrics_analyzer import analyze_images
from .middleware import SecurityMiddleware
from .stego_engine import capacity, embed_payload, extract_payload

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class MemoryMultipartParser(MultiPartParser):
    """Keep allowed uploads in RAM; the ASGI body bound applies before this."""
    spool_max_size = 24 * 1024 * 1024 + 1

    def on_part_end(self):
        if self._current_part.file is None:
            # Starlette normally falls back to Latin-1. Passwords/messages in
            # this profile must round-trip as exact UTF-8 instead.
            try:
                value = self._current_part.data.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise MultiPartException("Text fields must be UTF-8.") from exc
            self.items.append((self._current_part.field_name, value))
        else:
            super().on_part_end()

    def on_end(self):
        self.completed = True

    async def parse(self):
        self.completed = False
        form = await super().parse()
        if not self.completed:
            await form.close()
            # Also close any unfinished part, absent from FormData.
            for handle in self._files_to_close_on_error:
                handle.close()
            raise MultiPartException("Truncated multipart body.")
        return form


@asynccontextmanager
async def multipart(request: Request, files: set[str], fields: set[str]):
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise AppError("MULTIPART_REQUIRED", "Folosiți multipart/form-data pentru încărcare.", 415)
    _, options = parse_options_header(content_type)
    if options.get(b"charset", b"utf-8").lower() not in (b"utf-8", b"utf8"):
        raise AppError("INVALID_CHARSET", "Formularul trebuie să folosească UTF-8.", 415)
    parser = MemoryMultipartParser(
        request.headers, request.stream(), max_files=len(files) + ("message" in fields), max_fields=len(fields),
        max_part_size=MAX_MESSAGE_BYTES + 4096,
    )
    # Remain in memory even when tests select a different body bound.
    parser.spool_max_size = request.app.state.settings.max_body_bytes + 1
    try:
        form = await parser.parse()
    except (MultiPartException, ValueError, UnicodeError) as exc:
        raise AppError("INVALID_MULTIPART", "Datele formularului sunt invalide sau depășesc limitele.") from exc
    try:
        values = list(form.multi_items())
        if len(values) != len(files) + len(fields) or set(form) != files | fields:
            raise AppError("INVALID_FIELDS", "Formularul conține câmpuri lipsă, duplicate sau necunoscute.", 422)
        for name in files:
            if not isinstance(form[name], UploadFile):
                raise AppError("INVALID_FILE_FIELD", "Imaginea trebuie încărcată ca fișier.", 422)
        normalized = dict(form)
        for name in fields:
            value = form[name]
            # Browsers normalize line breaks in string FormData values. A UTF-8
            # Blob preserves the textarea's exact bytes and its capacity count.
            if name == "message" and isinstance(value, UploadFile):
                if value.filename != "message.txt" or (value.content_type or "").split(";", 1)[0].lower() != "text/plain":
                    raise AppError("INVALID_TEXT_FIELD", "Mesajul binar trebuie să fie message.txt de tip text/plain UTF-8.", 422)
                raw = await value.read(MAX_MESSAGE_BYTES + 1)
                if len(raw) > MAX_MESSAGE_BYTES:
                    raise AppError("MESSAGE_TOO_LARGE", "Mesajul depășește limita aplicației.", 422)
                try:
                    normalized[name] = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise AppError("INVALID_TEXT_FIELD", "Mesajul trebuie să fie text UTF-8 valid.", 422) from exc
            elif not isinstance(value, str):
                raise AppError("INVALID_TEXT_FIELD", "Mesajul și parola trebuie să fie text.", 422)
        yield normalized
    finally:
        await form.close()


async def read_image(upload: UploadFile, settings: Settings):
    data = await upload.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise AppError("FILE_TOO_LARGE", "Imaginea depășește limita de încărcare.", 413)
    return await run_in_threadpool(load_image_bytes, data)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="StegVault — Criptarea mesajelor în imagini", version="1.0.0",
        docs_url=None, redoc_url=None,
    )
    app.state.settings = settings
    app.add_middleware(SecurityMiddleware, settings=settings)

    @app.exception_handler(AppError)
    async def domain_error(request: Request, exc: AppError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message},
                             "request_id": request.state.request_id}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default details can echo sensitive input, so do not return them.
        return await domain_error(request, AppError("INVALID_REQUEST", "Cererea este invalidă.", 422))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return await domain_error(request, AppError("HTTP_ERROR", "Resursa sau metoda solicitată nu este disponibilă.", exc.status_code))

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.post("/api/v1/capacity", summary="Capacitatea unei imagini PNG/BMP")
    async def get_capacity(request: Request):
        async with multipart(request, {"image"}, set()) as form:
            image = await read_image(form["image"], settings)
            return capacity(image)

    @app.post(
        "/api/v1/embed", summary="Criptează și ascunde un mesaj", response_class=Response,
        responses={200: {"description": "Imagine PNG cu mesaj criptat", "content": {
            "image/png": {"schema": {"type": "string", "format": "binary"}},
        }}},
    )
    async def embed(request: Request):
        async with multipart(request, {"image"}, {"message", "password"}) as form:
            message, password = form["message"], form["password"]
            password_bytes(password)
            if len(password) < 12:
                raise AppError("WEAK_PASSWORD", "Folosiți o parolă de cel puțin 12 caractere.", 422)
            if not message:
                raise AppError("EMPTY_MESSAGE", "Introduceți un mesaj de ascuns.", 422)
            image = await read_image(form["image"], settings)

            def process():
                try:
                    length = len(message.encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise AppError("INVALID_MESSAGE", "Mesajul nu este UTF-8 valid.", 422) from exc
                if length > capacity(image)["max_payload_bytes"]:
                    raise AppError("CAPACITY_EXCEEDED", "Mesajul depășește capacitatea disponibilă a imaginii.", 422)
                return png_bytes(embed_payload(image, encrypt_message(message, password)))

            data = await run_in_threadpool(process)
            if len(data) > min(settings.max_upload_bytes, MAX_UPLOAD_BYTES):
                raise AppError(
                    "OUTPUT_TOO_LARGE",
                    "PNG-ul rezultat depășește limita de 10 MiB pentru reîncărcare. Folosiți o imagine sau un mesaj mai mic.",
                    422,
                )
            return Response(data, media_type="image/png", headers={
                "Content-Disposition": 'attachment; filename="stegvault-secret.png"',
            })

    @app.post("/api/v1/extract", summary="Extrage și autentifică un mesaj")
    async def extract(request: Request):
        async with multipart(request, {"image"}, {"password"}) as form:
            password = form["password"]
            password_bytes(password)
            image = await read_image(form["image"], settings)

            def process():
                message = decrypt_message(extract_payload(image), password)
                return {"message": message, "message_bytes": len(message.encode("utf-8"))}

            return await run_in_threadpool(process)

    @app.post("/api/v1/analyze", summary="Calculează PSNR SSIM și statisticile LSB")
    async def analyze(request: Request):
        async with multipart(request, {"original", "stego"}, set()) as form:
            original = await read_image(form["original"], settings)
            stego = await read_image(form["stego"], settings)
            if max(original.width * original.height, stego.width * stego.height) > MAX_ANALYSIS_PIXELS:
                raise AppError("ANALYSIS_TOO_LARGE", "Analiza SSIM acceptă cel mult 2 megapixeli per imagine.", 413)
            try:
                return await run_in_threadpool(analyze_images, original, stego)
            except ValueError as exc:
                raise AppError("INVALID_COMPARISON", str(exc), 422) from exc

    # Document multipart contracts despite bounded, manually managed parsing.
    schemas = {
        "/api/v1/capacity": ({"image"}, set()),
        "/api/v1/embed": ({"image"}, {"message", "password"}),
        "/api/v1/extract": ({"image"}, {"password"}),
        "/api/v1/analyze": ({"original", "stego"}, set()),
    }
    for route in app.routes:
        if getattr(route, "path", None) in schemas:
            files, fields = schemas[route.path]
            route.openapi_extra = {"requestBody": {"required": True, "content": {"multipart/form-data": {
                "schema": {"type": "object", "required": sorted(files | fields), "properties": {
                    **{x: {"type": "string", "format": "binary"} for x in sorted(files)},
                    **{x: {"type": "string", **({"writeOnly": True, "format": "password"} if x == "password" else {})}
                       for x in sorted(fields)},
                }},
            }}}}
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
