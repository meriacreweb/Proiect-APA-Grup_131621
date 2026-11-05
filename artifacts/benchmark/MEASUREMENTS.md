# Măsurători locale pe date sintetice

Au fost executate efectiv **27 de probe**: imagini RGB sintetice de 256×256, 512×512 și 1024×1024 pixeli; mesaje ASCII sintetice de 128, 1024 și 16.384 octeți; câte trei repetări. Fiecare probă a verificat egalitatea exactă a mesajului după criptare, inserare, salvare/reîncărcare PNG, extragere și decriptare. Seed-ul datelor sintetice este 252; saltul și IV-ul rămân criptografic aleatoare.

**Toate datele sunt sintetice; acestea nu sunt măsurători fizice.** Rezultatele sunt specifice acestei execuții locale și nu reprezintă garanții de performanță. Nu s-au eliminat probe de încălzire; primele apeluri pot include inițializări ale bibliotecilor.

Durata observată pentru apelul complet al benchmark-ului a fost **4,999 s**; importul inițial al modulelor a fost realizat înainte de pornirea acestui cronometru. Suma intervalelor instrumentate pentru etapele de mai jos este **3.888777 s**. Diferența include salvarea/reîncărcarea PNG, generarea datelor și generarea rapoartelor.

| Etapă | Medie (ms) | Minim (ms) | Maxim (ms) | Maxim memorie urmărită (octeți) |
|---|---:|---:|---:|---:|
| PBKDF2 + AES-GCM: criptare | 42.612 | 41.268 | 44.902 | 49,539 |
| Inserare LSB | 0.835 | 0.125 | 9.084 | 6,703,073 |
| Extragere LSB | 0.210 | 0.060 | 0.619 | 6,297,702 |
| PBKDF2 + AES-GCM: decriptare | 42.635 | 40.328 | 50.040 | 33,056 |
| PSNR + SSIM + chi-square | 57.737 | 7.362 | 162.734 | 165,745,208 |

**Limitele memoriei și timpului:** fiecare etapă este măsurată cu `perf_counter` în timp ce `tracemalloc` este activ; instrumentarea adaugă cost. `tracemalloc` raportează vârful alocărilor urmărite din acea etapă, nu întregul consum al procesului. Unele alocări native din NumPy, Pillow și bibliotecile criptografice pot să nu fie urmărite. RSS-ul procesului nu a fost măsurat.

În cele 27 de probe, PSNR a fost între **52.874404 și 84.675688 dB**, iar SSIM între **0.999968480139 și 0.999999997021**. Aceste metrici descriu fidelitatea pixelilor RGB. Nu demonstrează nedetectabilitatea steganografică și nu evaluează rezistența parolei.

## Mediul observat

- Python: `3.13.7 (v3.13.7:bcee1c32211, Aug 14 2025, 19:10:51) [Clang 16.0.0 (clang-1600.0.26.6)]`
- Platformă: `macOS-27.0.1-arm64-arm-64bit-Mach-O`
- Arhitectură: `arm64`
- Procesoare logice raportate: 10
- PBKDF2-HMAC-SHA-256: 600,000 iterații

- cryptography: `50.0.2`
- Pillow: `13.0.0`
- numpy: `2.5.4`
- scipy: `1.18.1`
- scikit-image: `0.26.0`
- matplotlib: `3.11.2`
- fastapi: `0.156.8`

## Exemplu vizual sintetic

Directorul `../sample/` conține o imagine generată din gradienți RGB și zgomot discret, imaginea stego corespunzătoare, histograma și rezultatele analizei. `SYNTHETIC_SAMPLE.json` declară generatorul, mesajul și **parola publică exclusiv de test**. Mesajul are 128 de octeți UTF-8. Extragerea mesajului din fișierul PNG salvat a fost verificată.

- PSNR: **72.535920 dB**.
- SSIM: **0.999992092318**.
- MSE: **0.003626505534**.
- Valori RGB modificate: **713** din 196.608 (**0.362651%**).

Testul chi-square utilizează perechi de valori 0/1, 2/3, …, 254/255 și numărul perechilor nevidate drept grade de libertate. Valorile p sunt doar indicatori exploratorii ai egalității numărărilor par/impar; nu sunt probabilități că imaginea ascunde un mesaj. Testul pe histograma întregii imagini poate dilua o inserare localizată, iar distribuțiile naturale pot produce rezultate similare. Numărările așteptate sub cinci reduc validitatea aproximației chi-square; sunt raportate separat în JSON.

Reproducere din directorul proiectului:

```sh
python -m scripts.benchmark --include-metrics --output-dir artifacts/benchmark
python -m backend.app.metrics_analyzer artifacts/sample/synthetic_original.png artifacts/sample/synthetic_stego.png --output-dir artifacts/sample
```
