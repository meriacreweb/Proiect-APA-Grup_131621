"""Real Chromium UI → API → downloaded PNG → authenticated UI round trip.

Only synthetic public test data is used. Start the server on port 8000 first.
Install the matching browser with `python -m playwright install chromium`.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "browser")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    message = 'Salut, țară 👋\n<scr' + 'ipt>alert("test")</scr' + 'ipt> & text simplu.'
    password = "Parola publica pentru test 2026!"
    page_errors = []
    external_requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            **({"executable_path": str(args.browser_executable)} if args.browser_executable else {}),
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("request", lambda request: external_requests.append(request.url)
                if urlsplit(request.url).scheme in ("http", "https")
                and urlsplit(request.url).netloc != urlsplit(args.url).netloc else None)
        response = page.goto(args.url, wait_until="networkidle")
        assert response.status == 200
        expect(page.get_by_role("heading", name="Criptarea mesajelor în imagini", level=1)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#embed-image").set_input_files(ROOT / "artifacts/sample/synthetic_original.png")
        expect(page.locator("#capacity-summary")).to_contain_text("24.524", timeout=15_000)
        page.locator("#message").fill(message)
        expect(page.locator("#message-counter")).to_contain_text(f"{len(message.encode('utf-8'))} octeți")
        page.locator("#embed-password").fill(password)
        page.locator("#embed-submit").click()
        expect(page.locator("#embed-result")).to_be_visible(timeout=30_000)
        expect(page.locator("#embed-error")).to_be_hidden()
        with page.expect_download() as download:
            page.locator("#download-image").click()
        stego = output / "ui-stego.png"
        download.value.save_as(stego)
        assert stego.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        page.locator("#analyze-button").click()
        expect(page.locator("#analysis-results")).to_be_visible(timeout=30_000)
        expect(page.locator("#metric-psnr")).to_contain_text("dB")
        expect(page.locator("#analyze-error")).to_be_hidden()
        page.screenshot(path=str(output / "desktop-embed.png"), full_page=True)

        page.locator("#tab-extract").click()
        page.locator("#extract-image").set_input_files(stego)
        page.locator("#extract-password").fill("Parola gresita 2026!")
        page.locator("#extract-submit").click()
        expect(page.locator("#extract-error")).to_contain_text("Autentificare eșuată", timeout=30_000)
        expect(page.locator("#extract-result")).to_be_hidden()
        page.locator("#extract-password").fill(password)
        page.locator("#extract-submit").click()
        expect(page.locator("#extract-result")).to_be_visible(timeout=30_000)
        assert page.locator("#decrypted-message").text_content() == message
        assert page.locator("#decrypted-message script").count() == 0
        assert page.locator("#decrypted-message img").count() == 0
        page.screenshot(path=str(output / "desktop-extract.png"), full_page=True)
        page.locator("#clear-message").click()
        expect(page.locator("#decrypted-message")).to_be_empty()
        expect(page.locator("#extract-password")).to_have_value("")

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#tab-embed").click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "mobile.png"), full_page=True)
        page.locator("#clear-session").click()
        expect(page.locator("#message")).to_have_value("")
        expect(page.locator("#embed-password")).to_have_value("")
        expect(page.locator("#embed-result")).to_be_hidden()
        assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
        assert not page_errors, page_errors
        assert not external_requests, external_requests
        report = {
            "status": "passed", "browser": browser.version, "url": args.url,
            "checks": ["desktop layout", "upload", "UTF-8 capacity", "AES-GCM embedding",
                       "PNG download", "PSNR/SSIM analysis", "wrong password rejection",
                       "authenticated extraction", "literal HTML rendering", "clear secret",
                       "390px mobile without overflow", "clear session", "no web storage",
                       "no external requests", "no JavaScript errors"],
            "input_type": "public synthetic test image and public test message",
            "message_bytes": len(message.encode("utf-8")),
            "page_errors": page_errors, "external_requests": external_requests,
        }
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
