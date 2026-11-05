"""REST integration, resource limits, and security response behavior."""

from __future__ import annotations

import io
import logging
import random
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.config import Settings
from backend.app.main import create_app


def file_part(data: bytes, name="cover.png", mime="image/png"):
    return (name, data, mime)


def multipart_bytes(parts, boundary="stegvault-wire-test", *, complete=True):
    body = bytearray()
    for name, filename, value in parts:
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body.extend(f"--{boundary}\r\n{disposition}\r\n\r\n".encode())
        body.extend(value)
        body.extend(b"\r\n")
    # A nonterminal delimiter completes the previous part but leaves the parser
    # waiting for another part. This catches finalize() implementations that do
    # not validate the final parser state.
    body.extend(f"--{boundary}{'--' if complete else ''}\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def assert_private_response(response):
    assert "no-store" in response.headers.get("cache-control", "")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options", "").upper() == "DENY"
    assert response.headers.get("x-request-id")


def assert_error(response, expected_status):
    assert response.status_code == expected_status, response.text
    assert_private_response(response)
    payload = response.json()
    assert isinstance(payload["error"]["code"], str)
    assert isinstance(payload["error"]["message"], str)
    assert payload["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text
    return payload


def test_capacity_exposes_bytes_and_unambiguous_character_estimates(client, png_bytes):
    response = client.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
    assert response.status_code == 200, response.text
    assert_private_response(response)
    data = response.json()
    assert data["total_capacity_bytes"] == 1536
    assert data["header_bytes"] == 52
    assert data["max_payload_bytes"] == 1484
    assert data["max_characters_ascii"] == 1484
    assert data["max_characters_utf8_4byte"] == 371


def test_embed_download_and_extract_roundtrip(client, png_bytes, password):
    message = "Meriacre Victor • Parfeni Inga • Rudinciuc Victor\nSI-252 — UTM 🔐"
    embedded = client.post(
        "/api/v1/embed", files={"image": file_part(png_bytes)},
        data={"message": message, "password": password},
    )
    assert embedded.status_code == 200, embedded.text
    assert_private_response(embedded)
    assert embedded.headers["content-type"].startswith("image/png")
    assert "attachment" in embedded.headers.get("content-disposition", "")
    assert embedded.content.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(embedded.content)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == (64, 64)
    extracted = client.post(
        "/api/v1/extract", files={"image": file_part(embedded.content, "stego.png")},
        data={"password": password},
    )
    assert extracted.status_code == 200, extracted.text
    assert_private_response(extracted)
    assert extracted.json()["message"] == message


def test_utf8_message_file_preserves_exact_newline_bytes(client, png_bytes, password):
    message = "Prima linie țară 👋\nA doua\r\n\n<script>text</script>\rSfârșit"
    embedded = client.post(
        "/api/v1/embed",
        files={"image": file_part(png_bytes),
               "message": ("message.txt", message.encode("utf-8"), "text/plain;charset=utf-8")},
        data={"password": password},
    )
    assert embedded.status_code == 200, embedded.text
    recovered = client.post(
        "/api/v1/extract", files={"image": file_part(embedded.content)},
        data={"password": password},
    )
    assert recovered.status_code == 200
    assert recovered.json() == {"message": message, "message_bytes": len(message.encode("utf-8"))}


def test_utf8_message_file_rejects_invalid_bytes(client, png_bytes, password):
    response = client.post(
        "/api/v1/embed",
        files={"image": file_part(png_bytes), "message": ("message.txt", b"invalid\xff", "text/plain")},
        data={"password": password},
    )
    assert_error(response, 422)


def test_utf8_message_file_rejects_unexpected_metadata(client, png_bytes, password):
    response = client.post(
        "/api/v1/embed",
        files={"image": file_part(png_bytes), "message": ("other.bin", b"text", "application/octet-stream")},
        data={"password": password},
    )
    assert_error(response, 422)


def test_wrong_password_and_modified_tag_report_same_authentication_category(client, png_bytes, password):
    embedded = client.post(
        "/api/v1/embed", files={"image": file_part(png_bytes)},
        data={"message": "Un secret foarte privat", "password": password},
    )
    assert embedded.status_code == 200
    wrong = client.post(
        "/api/v1/extract", files={"image": file_part(embedded.content)},
        data={"password": "Parola greșită de test 2026"},
    )
    wrong_data = assert_error(wrong, 400)
    with Image.open(io.BytesIO(embedded.content)) as image:
        altered = image.convert("RGB")
    raw = bytearray(altered.tobytes())
    raw[(4 + 16 + 12) * 8] ^= 1  # first authentication-tag bit, RGB only
    altered = Image.frombytes("RGB", altered.size, bytes(raw))
    stream = io.BytesIO()
    altered.save(stream, format="PNG")
    tampered = client.post(
        "/api/v1/extract", files={"image": file_part(stream.getvalue())},
        data={"password": password},
    )
    tampered_data = assert_error(tampered, 400)
    assert wrong_data["error"]["code"] == tampered_data["error"]["code"]
    assert password not in wrong.text + tampered.text
    assert "Un secret foarte privat" not in wrong.text + tampered.text


def test_capacity_counts_utf8_bytes_not_python_character_count(client, png_bytes, password):
    response = client.post(
        "/api/v1/embed", files={"image": file_part(png_bytes)},
        data={"message": "€" * 495, "password": password},  # 1485 bytes > 1484
    )
    assert_error(response, 422)


@pytest.mark.parametrize("password", ["short", "🔐" * 257])
def test_embed_rejects_weak_or_oversized_password(client, png_bytes, password):
    response = client.post(
        "/api/v1/embed", files={"image": file_part(png_bytes)},
        data={"message": "secret", "password": password},
    )
    assert_error(response, 422)


def test_unsupported_real_file_format_overrides_filename_and_mime(client):
    stream = io.BytesIO()
    Image.new("RGB", (64, 64)).save(stream, format="JPEG")
    response = client.post(
        "/api/v1/capacity", files={"image": file_part(stream.getvalue(), "pretend.png", "image/png")}
    )
    assert_error(response, 415)


def test_missing_multipart_field_has_safe_structured_error(client):
    response = client.post(
        "/api/v1/extract", files={"password": (None, "private-password-2026")}
    )
    assert_error(response, 422)
    assert "private-password-2026" not in response.text


def test_nonmultipart_request_is_rejected_without_echoing_fields(client):
    response = client.post("/api/v1/extract", data={"password": "private-password-2026"})
    assert_error(response, 415)
    assert "private-password-2026" not in response.text


def test_invalid_image_has_safe_structured_error(client):
    response = client.post(
        "/api/v1/capacity", files={"image": file_part(b"\x89PNG\r\n\x1a\ninvalid chunks")}
    )
    assert_error(response, 400)


def test_malformed_multipart_does_not_expose_internals(client):
    response = client.post(
        "/api/v1/embed", content=b"not-a-valid-multipart-body",
        headers={"content-type": "multipart/form-data; boundary=missing"},
    )
    assert response.status_code in {400, 422}
    assert_error(response, response.status_code)


def test_extracted_markup_remains_literal_json_data(client, png_bytes, password):
    message = '<img src=x onerror="alert(document.cookie)"><script>alert(1)</script>& text'
    embedded = client.post(
        "/api/v1/embed", files={"image": file_part(png_bytes)},
        data={"message": message, "password": password},
    )
    assert embedded.status_code == 200
    extracted = client.post(
        "/api/v1/extract", files={"image": file_part(embedded.content)},
        data={"password": password},
    )
    assert extracted.status_code == 200
    assert extracted.headers["content-type"].startswith("application/json")
    assert extracted.json()["message"] == message
    assert_private_response(extracted)


def test_identical_images_have_exact_ssim_and_json_safe_infinite_psnr(client, png_bytes):
    response = client.post(
        "/api/v1/analyze", files={"original": file_part(png_bytes), "stego": file_part(png_bytes)}
    )
    assert response.status_code == 200, response.text
    assert_private_response(response)
    data = response.json()
    assert data["psnr_db"] is None
    assert data["ssim"] == pytest.approx(1.0)
    assert "Infinity" not in response.text


def test_analysis_rejects_mismatched_dimensions(client, png_bytes):
    stream = io.BytesIO()
    Image.new("RGB", (32, 32)).save(stream, format="PNG")
    response = client.post(
        "/api/v1/analyze", files={"original": file_part(png_bytes), "stego": file_part(stream.getvalue())}
    )
    assert response.status_code in {400, 422}
    assert_error(response, response.status_code)


@pytest.mark.parametrize("length_header", [None, "10"])
def test_body_limit_is_enforced_without_relying_on_content_length(length_header):
    boundary = "stegvault-test-boundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"cover.png\"\r\n"
        "Content-Type: image/png\r\n\r\n"
    ).encode() + b"x" * 2048 + f"\r\n--{boundary}--\r\n".encode()
    headers = {"content-type": f"multipart/form-data; boundary={boundary}"}
    if length_header is not None:
        headers["content-length"] = length_header
    application = create_app(Settings(max_body_bytes=1024, max_upload_bytes=512, rate_limit_requests=100))
    with TestClient(application) as limited:
        # An iterator produces a chunked request with no synthesized Content-Length.
        response = limited.post("/api/v1/capacity", content=iter([body]), headers=headers)
    assert_error(response, 413)


def test_per_file_limit_is_enforced_inside_valid_multipart(png_bytes):
    application = create_app(Settings(max_upload_bytes=32, max_body_bytes=8192, rate_limit_requests=100))
    with TestClient(application) as limited:
        response = limited.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
    assert_error(response, 413)


def test_rate_limit_is_enforced_for_expensive_api_requests(png_bytes):
    application = create_app(Settings(rate_limit_requests=2, rate_window_seconds=60))
    with TestClient(application) as limited:
        first = limited.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
        second = limited.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
        third = limited.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
    assert first.status_code == second.status_code == 200
    assert_error(third, 429)
    assert int(third.headers["retry-after"]) >= 1


def test_untrusted_host_is_rejected(png_bytes):
    application = create_app(Settings(rate_limit_requests=100))
    with TestClient(application, base_url="http://attacker.invalid") as isolated:
        response = isolated.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
    assert response.status_code == 400


def test_truncated_multipart_after_complete_file_is_rejected(client, png_bytes):
    body, content_type = multipart_bytes([("image", "cover.png", png_bytes)], complete=False)
    response = client.post("/api/v1/capacity", content=body, headers={"content-type": content_type})
    assert_error(response, 400)


@pytest.mark.parametrize("field", ["message", "password"])
def test_invalid_utf8_form_fields_are_rejected_without_latin1_fallback(client, png_bytes, field):
    values = {"message": b"A secret message", "password": b"Long test password 2026"}
    values[field] += b"\xff"
    body, content_type = multipart_bytes([
        ("image", "cover.png", png_bytes),
        ("message", None, values["message"]),
        ("password", None, values["password"]),
    ])
    response = client.post("/api/v1/embed", content=body, headers={"content-type": content_type})
    assert_error(response, 400)


def test_non_utf8_multipart_charset_is_rejected(client, png_bytes):
    body, content_type = multipart_bytes([("image", "cover.png", png_bytes)])
    response = client.post(
        "/api/v1/capacity", content=body,
        headers={"content-type": content_type + "; charset=iso-8859-1"},
    )
    assert response.status_code in {400, 415}
    assert_error(response, response.status_code)


@pytest.mark.parametrize("ambiguous_headers", [
    [("host", "testserver"), ("host", "attacker.invalid")],
    [("content-length", "10"), ("content-length", "11")],
    [("content-length", "10"), ("transfer-encoding", "chunked")],
])
def test_ambiguous_http_framing_or_host_headers_are_rejected(client, png_bytes, ambiguous_headers):
    body, content_type = multipart_bytes([("image", "cover.png", png_bytes)])
    response = client.post(
        "/api/v1/capacity", content=body,
        headers=[("content-type", content_type), *ambiguous_headers],
    )
    assert_error(response, 400)


def test_image_upload_larger_than_default_spool_threshold_never_reaches_disk(client, monkeypatch):
    def fail_rollover(self):
        pytest.fail("Secret image upload attempted to spill onto disk")

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", fail_rollover)
    random_bytes = random.Random(252).randbytes(640 * 640 * 3)
    image = Image.frombytes("RGB", (640, 640), random_bytes)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    assert len(stream.getvalue()) > 1024 * 1024
    response = client.post("/api/v1/capacity", files={"image": file_part(stream.getvalue())})
    assert response.status_code == 200, response.text


def test_concurrent_job_limit_rejects_work_and_releases_slot_after_completion(png_bytes, monkeypatch):
    from backend.app import main

    entered = threading.Event()
    release = threading.Event()
    original_decoder = main.load_image_bytes

    def blocking_decoder(data):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Test did not release the blocked image decoder")
        return original_decoder(data)

    monkeypatch.setattr(main, "load_image_bytes", blocking_decoder)
    application = create_app(Settings(max_concurrent_jobs=1, rate_limit_requests=100))
    with TestClient(application) as limited, ThreadPoolExecutor(max_workers=1) as pool:
        first_job = pool.submit(limited.post, "/api/v1/capacity", files={"image": file_part(png_bytes)})
        try:
            assert entered.wait(5), "First image job did not start"
            rejected = limited.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
            assert_error(rejected, 503)
            assert rejected.headers["retry-after"]
        finally:
            release.set()
        assert first_job.result(timeout=5).status_code == 200
        follow_up = limited.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
        assert follow_up.status_code == 200


def test_frontend_has_content_security_policy_and_no_client_cache(client):
    response = client.get("/")
    assert response.status_code == 200
    assert_private_response(response)
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "'unsafe-eval'" not in policy


@pytest.mark.parametrize("headers", [
    {"origin": "https://attacker.invalid"},
    {"origin": "null"},
    {"sec-fetch-site": "cross-site"},
])
def test_cross_site_requests_are_rejected_before_processing(client, png_bytes, headers):
    response = client.post(
        "/api/v1/capacity", files={"image": file_part(png_bytes)}, headers=headers
    )
    assert_error(response, 403)


def test_internal_failure_redacts_exception_text_from_response_and_logs(client, png_bytes, monkeypatch, caplog):
    from backend.app import main

    private_text = "Password=VerySensitivePrivatePassword; message=very private message"

    def fail_decode(data):
        raise RuntimeError(private_text)

    monkeypatch.setattr(main, "load_image_bytes", fail_decode)
    caplog.set_level(logging.ERROR, logger="stegvault")
    response = client.post("/api/v1/capacity", files={"image": file_part(png_bytes)})
    assert_error(response, 500)
    assert private_text not in response.text
    assert private_text not in caplog.text
    assert "RuntimeError" in caplog.text
    assert response.headers["x-request-id"] in caplog.text


def test_embed_rejects_png_output_that_cannot_be_uploaded_for_extraction(png_bytes, password, monkeypatch):
    from backend.app import main

    upload_limit = len(png_bytes)
    oversized_output = b"\x89PNG\r\n\x1a\n" + b"x" * (upload_limit - 7)
    assert len(oversized_output) == upload_limit + 1
    monkeypatch.setattr(main, "png_bytes", lambda image: oversized_output)
    application = create_app(Settings(max_upload_bytes=upload_limit, rate_limit_requests=100))
    with TestClient(application) as limited:
        response = limited.post(
            "/api/v1/embed", files={"image": file_part(png_bytes)},
            data={"message": "Secret care încape în imagine", "password": password},
        )
    error = assert_error(response, 422)
    assert error["error"]["code"] == "OUTPUT_TOO_LARGE"
    assert response.headers["content-type"].startswith("application/json")
