# API REST

Toate cererile de procesare sunt `POST` cu `multipart/form-data` și folosesc aceeași
origine ca interfața. Nu adăugați manual antetul multipart în `fetch`: browserul
trebuie să genereze boundary-ul. Câmpurile text sunt UTF-8 strict.
Se resping câmpurile suplimentare, duplicate, lipsă și formularele trunchiate.

## Capacitate

`POST /api/v1/capacity`, câmp fișier `image`.

```json
{
  "width": 1024, "height": 768,
  "total_capacity_bytes": 294912, "header_bytes": 52,
  "max_payload_bytes": 294860,
  "max_characters_ascii": 294860,
  "max_characters_utf8_4byte": 73715,
  "format": "PNG", "mode": "RGB"
}
```

Exemplul este calculat pentru dimensiunile de mai sus.
`format` și `mode` descriu fișierul decodat/normalizat. Numele sau MIME-ul declarat
de client nu sunt folosite ca dovadă a tipului imaginii.

## Inserare

`POST /api/v1/embed`, câmpuri `image`, `message`, `password`.
`message` poate fi câmp text obișnuit sau un fișier multipart `message.txt` cu
`Content-Type: text/plain` și octeți UTF-8 strict. Interfața trimite a doua formă,
pentru ca browserul să nu transforme LF în CRLF la serializarea valorilor FormData.
Ambele forme păstrează aceleași valori în modulul criptografic. Pentru clienții API
care cer identitate exactă la nivel de octeți se recomandă varianta fișier UTF-8.
Parola de inserare trebuie să aibă minimum 12 code points și maximum 1024 octeți
UTF-8. Mesajul trebuie să fie nevid și să încapă integral în capacitate.
Nu se taie textul și nu se elimină spațiile din mesaj sau parolă.

Succes: `200 image/png`, cu `Content-Disposition: attachment;
filename="stegvault-secret.png"`. Rezultatul se citește ca Blob sau octeți, nu JSON.
Un PNG rezultat peste limita de 10 MiB este respins cu `422 OUTPUT_TOO_LARGE`;
utilizatorul trebuie să aleagă o imagine sau un mesaj mai mic. Capacitatea pixelilor
și dimensiunea fișierului PNG comprimat sunt limite diferite.

## Extragere

`POST /api/v1/extract`, câmpuri `image`, `password`.
La extragere se acceptă orice parolă nevidă de maximum 1024 octeți, pentru
interoperabilitate cu modulul criptografic.

```json
{"message": "Salut, lume!", "message_bytes": 12}
```

Valoarea `message` este text, nu HTML. Interfața o afișează prin `textContent`.
Parola greșită sau un tag invalid nu returnează text parțial.

## Analiză

`POST /api/v1/analyze`, câmpuri fișier `original` și `stego`.
Aceleași dimensiuni, minimum 3 × 3 pixeli, maximum 2 megapixeli/fișier.
Rezultatul include `psnr_db`, `psnr_infinite`, `ssim`, `mse`, `width`, `height`,
`channels`, `changed_channels`, `changed_channel_ratio`, `ssim_window_size`,
`chi_square`. Pentru imagini identice: `psnr_db=null`, `psnr_infinite=true`, `ssim=1`.
`chi_square.original` și `.stego` conțin statisticile pe canale `red`, `green`, `blue`,
inclusiv χ², grade de libertate, p-value și perechi cu efective așteptate mici.

Endpoint-ul compară pixelii furnizați; nu dovedește că al doilea fișier provine din
primul și nu validează pachetul criptografic. Histograma se generează cu CLI-ul.

## Erori

```json
{
  "error": {"code": "AUTHENTICATION_FAILED", "message": "Autentificare eșuată: parola este greșită sau datele au fost modificate."},
  "request_id": "identificator aleator pentru diagnostic"
}
```

| HTTP | Situație |
|---:|---|
| 400 | imagine coruptă, protocol/length invalid, autentificare invalidă, multipart trunchiat |
| 403 | origine web neautorizată |
| 408 | upload prea lent |
| 413 | fișier, corp HTTP sau dimensiuni peste limite |
| 415 | tip imagine/mod/charset/formular neacceptat |
| 422 | câmpuri greșite, mesaj gol/prea mare, parolă prea scurtă, comparație invalidă |
| 429 | prea multe cereri; respectați `Retry-After` |
| 503 | toate sloturile de procesare ocupate; respectați `Retry-After` |
| 500 | eroare internă redactată, fără detalii sensibile |

Răspunsurile au `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` și un
`X-Request-ID` generat pe server. Antetele CSP limitează resursele interfeței la
aceeași origine; nu există CDN sau analytics.

Exemplu sigur de apel fără parolă în argumentele procesului, dintr-un script Python:

```python
from getpass import getpass
from pathlib import Path
import httpx

with Path("original.png").open("rb") as image:
    result = httpx.post(
        "http://127.0.0.1:8000/api/v1/embed",
        files={"image": ("original.png", image, "image/png")},
        data={"message": "Mesaj de demonstrație", "password": getpass("Parola: ")},
        timeout=60,
    )
result.raise_for_status()
Path("stego.png").write_bytes(result.content)
```

