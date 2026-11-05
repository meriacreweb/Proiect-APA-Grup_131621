"""ASGI protections applied before multipart parsing or image decoding."""

import asyncio
import logging
import time
import uuid
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .config import Settings
from .errors import AppError

logger = logging.getLogger("stegvault")

SECURITY_HEADERS = {
    "cache-control": "no-store",
    "pragma": "no-cache",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


class SecurityMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings
        self.clients: OrderedDict[str, deque[float]] = OrderedDict()
        self.active_jobs = 0

    def _rate_allowed(self, client: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.settings.rate_window_seconds
        # Bound the cardinality too. New identities cannot evict live rate limits.
        while self.clients:
            first = next(iter(self.clients))
            queue = self.clients[first]
            if queue and queue[-1] > cutoff:
                break
            self.clients.pop(first)
        if client not in self.clients:
            if len(self.clients) >= 4096:
                return False
            self.clients[client] = deque()
        entries = self.clients[client]
        self.clients.move_to_end(client)
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if len(entries) >= self.settings.rate_limit_requests:
            return False
        entries.append(now)
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                headers.update({key.encode(): value.encode() for key, value in SECURITY_HEADERS.items()})
                headers[b"x-request-id"] = request_id.encode()
                message = {**message, "headers": list(headers.items())}
            await send(message)

        async def reject(error: AppError, extra_headers=None):
            response = JSONResponse(
                {"error": {"code": error.code, "message": error.message}, "request_id": request_id},
                status_code=error.status_code, headers=extra_headers,
            )
            await response(scope, receive, secured_send)

        headers = Headers(scope=scope)
        if len(headers.getlist("host")) != 1 or len(headers.getlist("content-length")) > 1 or (
            "content-length" in headers and "transfer-encoding" in headers
        ):
            await reject(AppError("AMBIGUOUS_HEADERS", "Antetele cererii sunt ambigue.", 400))
            return
        try:
            host = urlsplit("//" + headers.get("host", "")).hostname
        except ValueError:
            host = None
        if host not in self.settings.allowed_hosts:
            await reject(AppError("INVALID_HOST", "Gazda cererii nu este autorizată.", 400))
            return
        is_api = scope.get("path", "").startswith("/api/")
        if is_api:
            origin = headers.get("origin")
            if (origin and origin not in self.settings.allowed_origins) or headers.get("sec-fetch-site") == "cross-site":
                await reject(AppError("ORIGIN_DENIED", "Originea cererii nu este autorizată.", 403))
                return
            client = (scope.get("client") or ("unknown", 0))[0]
            if not self._rate_allowed(client):
                await reject(AppError("RATE_LIMITED", "Prea multe cereri. Încercați din nou mai târziu.", 429),
                             {"Retry-After": str(int(self.settings.rate_window_seconds) + 1)})
                return
        needs_job = is_api and scope["method"] == "POST"
        if needs_job and self.active_jobs >= self.settings.max_concurrent_jobs:
            await reject(AppError("SERVER_BUSY", "Serverul procesează alte imagini. Încercați din nou.", 503), {"Retry-After": "2"})
            return
        if needs_job:
            self.active_jobs += 1
        try:
            if scope["method"] in ("POST", "PUT", "PATCH"):
                declared = headers.get("content-length")
                if declared is not None:
                    try:
                        length = int(declared)
                        if length < 0:
                            raise ValueError
                    except ValueError as exc:
                        raise AppError("INVALID_CONTENT_LENGTH", "Lungimea cererii este invalidă.") from exc
                    if length > self.settings.max_body_bytes:
                        raise AppError("BODY_TOO_LARGE", "Cererea depășește limita de încărcare.", 413)
                body = bytearray()
                try:
                    async with asyncio.timeout(self.settings.body_timeout_seconds):
                        while True:
                            part = await receive()
                            if part["type"] == "http.disconnect":
                                return
                            chunk = part.get("body", b"")
                            if len(body) + len(chunk) > self.settings.max_body_bytes:
                                raise AppError("BODY_TOO_LARGE", "Cererea depășește limita de încărcare.", 413)
                            body.extend(chunk)
                            if not part.get("more_body", False):
                                break
                except TimeoutError as exc:
                    raise AppError("UPLOAD_TIMEOUT", "Încărcarea a depășit timpul permis.", 408) from exc
                sent = False

                async def replay():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                await self.app(scope, replay, secured_send)
            else:
                await self.app(scope, receive, secured_send)
        except AppError as exc:
            if not started:
                await reject(exc)
        except Exception as exc:  # noqa: BLE001 -- final HTTP boundary; redact internal failures
            # Never log request bodies, images, messages, passwords or exception text.
            logger.error("request_id=%s error_type=%s", request_id, type(exc).__name__)
            if not started:
                await reject(AppError("INTERNAL_ERROR", "Procesarea a eșuat. Încercați din nou.", 500))
        finally:
            if needs_job:
                self.active_jobs -= 1

