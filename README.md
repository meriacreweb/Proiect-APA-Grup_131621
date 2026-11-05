# Criptarea mesajelor în imagini

Aplicație full-stack funcțională pentru proiectul UTM, grupa SI-252.
Echipă: **Meriacre Victor, Parfeni Inga, Rudinciuc Victor**.

StegVault criptează text UTF-8 cu AES-256-GCM, derivă cheia prin
PBKDF2-HMAC-SHA-256 și ascunde pachetul în câte un bit LSB al canalelor RGB.
Interfața și API-ul rulează împreună, fără servicii externe, conturi sau bază de date.
Parola și mesajul sunt procesate pe server: pentru lucru la distanță este necesar HTTPS
și un server în care utilizatorul are încredere.

## Pasul 1 Structura proiectului

```text
stegvault/
├── frontend/
│   ├── index.html              # două tab-uri, formulare și rezultate
│   ├── styles.css              # interfață responsivă, fără CDN
│   └── app.js                  # drag & drop, UTF-8, API, descărcare, analiză
├── backend/
│   ├── __init__.py
│   └── app/
│       ├── __init__.py
│       ├── constants.py        # profil criptografic și limite
│       ├── config.py           # gazde, origini și resurse
│       ├── errors.py           # excepții controlate
│       ├── crypto_engine.py    # pasul 2: PBKDF2 și AES-GCM
│       ├── stego_engine.py     # pasul 3: header și LSB RGB
│       ├── image_io.py         # validare, decodare și PNG curat
│       ├── middleware.py       # limite, rată, concurență, antete
│       ├── main.py             # pasul 4: API și servirea interfeței
│       └── metrics_analyzer.py # pasul 6: PSNR, SSIM, χ², histogramă CLI
├── scripts/
│   ├── benchmark.py            # timp și memorie măsurate local
│   └── browser_smoke.py        # verificarea fluxului real în browser
├── tests/
│   ├── conftest.py
│   ├── test_crypto.py
│   ├── test_stego.py
│   ├── test_api.py
│   └── test_metrics.py
├── docs/
│   ├── ANALIZA_PROPUNERII.md   # analiză, cerințe și complexitate
│   ├── PROTOCOL.md             # format binar, AAD, interoperabilitate
│   ├── API.md                  # contracte și exemple
│   ├── SECURITY.md             # model de amenințări și operare
│   └── VERIFICARE.md           # rezultate reale și limite
├── artifacts/
│   ├── sample/                # imagini sintetice și histogramă
│   └── benchmark/             # CSV și JSON cu rezultate măsurate
├── requirements.txt           # dependențe directe fixate
├── requirements.lock          # toate dependențele de execuție fixate
├── requirements-dev.txt       # teste, verificări și browser
├── pyproject.toml
├── Dockerfile
├── .dockerignore
└── .gitignore
```

Modulele de procesare nu depind de HTTP și pot fi importate în teste sau în alte
interfețe. Detaliile binare sunt stabilite în [PROTOCOL.md](docs/PROTOCOL.md).
Analiza propunerii este în [ANALIZA_PROPUNERII.md](docs/ANALIZA_PROPUNERII.md).

## Pasul 2 Modulul criptografic

[`crypto_engine.py`](backend/app/crypto_engine.py) conține `derive_key`,
`encrypt_message` și `decrypt_message`, împreună cu tipul `EncryptedPayload`.
Se folosesc 600.000 de iterații PBKDF2, cheie de 32 B, salt de 16 B, IV de 12 B și
tag de 16 B. Saltul și IV-ul sunt regenerate criptografic la fiecare criptare.
Tag-ul autentifică și câmpurile header-ului prin AAD; parola greșită și datele
alterate produc `AuthenticationError`, fără text clar parțial.

## Pasul 3 Motorul LSB

[`stego_engine.py`](backend/app/stego_engine.py) implementează `capacity`,
`build_packet`, `embed_payload` și `extract_payload`. Header-ul este exact
`STEG | Salt16 | IV12 | Tag16 | Length4 | CiphertextN`; lungimea este big-endian.
Inserarea folosește un bit per canal R/G/B, pe rânduri, MSB-first în fiecare octet.
Alfa și canalele nefolosite rămân identice. Extragerea verifică lungimea înainte
de a citi payload-ul. [`image_io.py`](backend/app/image_io.py) validează imaginile.

## Pasul 4 Backend REST

[`main.py`](backend/app/main.py) oferă cele patru endpoint-uri multipart:

| Endpoint | Intrări | Rezultat |
|---|---|---|
| `/api/v1/capacity` | `image` | capacitate brută/utilă și estimări de caractere |
| `/api/v1/embed` | `image`, `message`, `password` | imagine PNG |
| `/api/v1/extract` | `image`, `password` | text autentificat UTF-8 |
| `/api/v1/analyze` | `original`, `stego` | PSNR, SSIM, MSE și χ² pe canale |

[`API.md`](docs/API.md) descrie formatele, erorile și un exemplu de client.
Limitele se aplică înainte de operațiile costisitoare; fișierele sunt închise la final.

## Pasul 5 Interfața

[`index.html`](frontend/index.html), [`styles.css`](frontend/styles.css) și
[`app.js`](frontend/app.js) implementează cele două tab-uri în română, drag & drop,
previzualizare, capacitate actualizată prin `TextEncoder`, parolă ascunsă/afișată,
spinner, descărcare PNG, analiză și afișarea mesajului prin `textContent`.
Mesajul se trimite ca fișier text UTF-8 în formular pentru a păstra exact liniile noi.
Parolele se golesc după succes; butonul de ștergere elimină starea sesiunii.

## Pasul 6 Analiză și testare

[`metrics_analyzer.py`](backend/app/metrics_analyzer.py) calculează PSNR matematic,
SSIM cu intervalul explicit 255 și χ² pe perechi par/impar. CLI-ul generează JSON și
histograma comparativă RGB/LSB. [`benchmark.py`](scripts/benchmark.py) măsoară timpul
și alocările de memorie urmărite pe date sintetice reproductibile. Testele verifică
interoperabilitatea, integritatea, limitele, API-ul și valorile numerice; rezultatele
efective sunt consemnate în [`VERIFICARE.md`](docs/VERIFICARE.md).

## Pasul 7 Instalare și rulare

Mediu verificat: Python **3.13.7**. Folosiți Python 3.13 și rulați comenzile din
directorul rădăcină al proiectului. Nu sunt necesare Node.js sau compilarea interfeței.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

Deschideți **http://127.0.0.1:8000**. Pentru oprire, apăsați `Ctrl+C` în terminal.
`GET /health` verifică pornirea; `GET /openapi.json` descrie cererile API.
Documentația API completă este [aici](docs/API.md).

1. Selectați o imagine PNG/BMP. Capacitatea se calculează automat.
2. Introduceți mesajul și o parolă de minimum 12 caractere. Păstrați parola separat.
3. Descărcați PNG-ul generat și transmiteți-l ca fișier original.
4. În tab-ul de extragere încărcați PNG-ul și introduceți aceeași parolă.
5. Din rezultatul criptării puteți compara imaginea originală cu rezultatul.

Evitați redimensionarea, filtrele și conversia în JPEG: modifică biții care transportă
mesajul. Salvarea PNG fără schimbarea pixelilor păstrează pachetul. Un câmp de parolă
greșită și un pachet cu integritate invalidă produc aceeași eroare de autentificare.

## Teste și analiza produsului

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check backend scripts tests
python -m pip check
python -m backend.app.metrics_analyzer artifacts/sample/synthetic_original.png artifacts/sample/synthetic_stego.png --output-dir analysis
python -m scripts.benchmark --include-metrics --output-dir artifacts/benchmark
```

Scriptul de analiză produce `metrics.json` și `histogram.png`. PSNR infinit este
serializat ca `null` împreună cu `psnr_infinite: true`, pentru JSON valid.
SSIM necesită imagini cu aceleași dimensiuni, minimum 3 × 3 pixeli.
Analiza web acceptă maximum 2 megapixeli; CLI poate analiza și imagini mai mari
în limita decodorului, dacă există suficientă memorie.

Pentru verificarea browserului, instalați Chromium pentru Playwright și porniți mai
întâi serverul de mai sus:

```bash
python -m playwright install chromium
python -m scripts.browser_smoke
```

Imaginile de demonstrație sunt **sintetice**. Măsurătorile de timp sunt execuții locale
reale pe aceste date; nu sunt măsurători pe fotografii sau o evaluare de nedetectabilitate.
`tracemalloc` raportează alocările urmărite, nu memoria RSS completă a procesului.

## Operare

Upload: maximum 10 MiB/fișier și 12 megapixeli; corp HTTP: maximum 24 MiB;
maximum două procesări concurente și 60 de cereri API/minut/IP/proces.
Solicitările care depășesc limitele primesc erori controlate.
Un PNG rezultat mai mare de 10 MiB este respins cu o eroare clară, pentru ca orice
fișier descărcat cu succes să poată fi reîncărcat la extragere.
Fișierele multipart acceptate rămân în RAM și sunt închise după procesare.
Nu există salvare automată a imaginilor, parolelor sau mesajelor pe disc.
Memoria Python, swap-ul sistemului și clipboardul nu oferă ștergere criptografică garantată.

Pentru alt domeniu setați explicit `STEG_ALLOWED_HOSTS` și `STEG_ALLOWED_ORIGINS`.
Nu activați wildcard-uri. Configurarea HTTPS, controlul accesului, protecția globală
împotriva abuzului și limitele proxy-ului sunt explicate în [SECURITY.md](docs/SECURITY.md).

Opțional, construiți containerul inclus:

```bash
docker build -t stegvault .
docker run --rm --read-only --tmpfs /tmp:size=64m --memory=1g --cpus=2 -p 127.0.0.1:8000:8000 stegvault
```

Containerul rulează cu utilizator fără privilegii. Rețeta Docker este furnizată pentru
instalare; starea verificării sale este precizată în raportul de verificare.

