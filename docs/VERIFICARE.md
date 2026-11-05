# Verificarea aplicației

Verificare efectuată la **21 octombrie 2026**. Fișierul Word al propunerii a fost
extras integral și randat: o pagină, verificată vizual. Nu a fost modificat.

## Rezultate automate

Ultima execuție completă: **96 de teste trecute în 12,25 s**.
`ruff check backend scripts tests` a trecut, iar `pip check` nu a găsit dependențe
incompatibile. Versiunile instalate au fost comparate cu `requirements.lock`.
Cele patru contracte multipart au fost verificate în schema OpenAPI.

Există un avertisment de depreciere din Starlette TestClient privind adaptorul
`httpx`; nu este eroare de execuție. Versiunile sunt fixate și testate împreună.
O migrare viitoare a instrumentului de test trebuie făcută separat, fără a reduce
acoperirea testelor sau a ascunde avertismentul.

| Domeniu | Verificări efective |
|---|---|
| PBKDF2 | rezultat comparat cu `hashlib.pbkdf2_hmac`, cheie de 32 B, parole și salturi diferite |
| AES-GCM | fixture independent, Unicode, text gol în modulul core, tag/parolă/cifrat alterate |
| Protocol | header exact de 52 B, uint32 big-endian, MSB-first, extragere independentă |
| LSB | capacitate exactă și depășită, alfa păstrat, canale nefolosite identice |
| Imagini | PNG/BMP, paletă/transparență, metadata eliminată, JPEG/APNG/corupție/limite respinse |
| Multipart | câmpuri invalide, UTF-8 strict, charset invalid, boundary terminal lipsă |
| Corp HTTP | lungime falsă/absentă și corp prea mare, antete duplicate sau ambigue |
| Resurse | limită de rată, concurență, recuperarea slotului după procesare |
| Date sensibile | mesajul/parola nu apar în erori/loguri; numai request ID și tipul erorii interne |
| RAM | upload PNG real peste 1 MiB cu trecerea pe disc blocată; procesarea reușește |
| Output | PNG peste limita de reîncărcare este respins controlat |
| Linii noi | mesajul multipart UTF-8 păstrează LF, CRLF și CR exact |
| Metrici | MSE/PSNR pe valori cunoscute, SSIM identic, dimensiuni, χ², CLI și histogramă |

## Fluxul real în browser

Scriptul `scripts/browser_smoke.py` a trecut în Chromium **153.0.8010.12**, cu
backend-ul real pe `127.0.0.1:8000`. Testul folosește imaginea sintetică publică
și un mesaj de 64 de octeți cu diacritice, emoji, linie nouă și markup literal.

1. Pagina se afișează și primește capacitatea reală de la API.
2. Criptarea returnează un PNG, iar browserul îl descarcă efectiv.
3. Analiza rezultă din comparația originalului cu acel PNG.
4. O parolă greșită produce eroare de autentificare, fără mesaj afișat.
5. Parola corectă recuperează exact textul, inclusiv linia nouă.
6. Secvența `<script>` rămâne text, fără element script în rezultat.
7. Ștergerea mesajului și a sesiunii golește câmpurile și rezultatele.
8. Layout-ul la 390 px nu depășește lățimea ecranului.
9. Nu apar erori JavaScript, cereri către origini externe sau date în web storage.

Raportul automat este în `artifacts/browser/report.json`. Capturile desktop și mobil
din același director au fost deschise și inspectate vizual. O verificare suplimentară
în browserul aplicației a confirmat extragerea și adaptarea la ecrane intermediare.

Verificarea pe mai multe rânduri a identificat normalizarea LF→CRLF făcută de
browser pentru câmpurile text FormData. Corecția trimite mesajul ca Blob UTF-8
`message.txt`; API-ul validează strict acel conținut și acceptă în continuare câmpul
text obișnuit al clienților existenți. Regresiile sunt acoperite prin teste API și browser.

## Măsurători locale

**27/27 probe** au recuperat exact mesajul după salvare și reîncărcare PNG.
Imagini RGB sintetice: 256², 512², 1024² pixeli; mesaje ASCII: 128, 1024 și
16.384 octeți; trei repetări pentru fiecare combinație.

| Etapă | Medie măsurată |
|---|---:|
| PBKDF2 + criptare AES-GCM | 42,612 ms |
| Inserare LSB | 0,835 ms |
| Extragere LSB | 0,210 ms |
| PBKDF2 + decriptare AES-GCM | 42,635 ms |
| PSNR, SSIM și χ² | 57,737 ms |

PSNR: **52,874404–84,675688 dB**; SSIM: **0,999968480139–0,999999997021**.
Rapoartele complete și mediul de execuție sunt în `artifacts/benchmark/`.
Timpul include instrumentarea fiecărei etape; serializarea PNG este verificată,
dar nu inclusă în timpii etapelor. Memoria urmărită prin `tracemalloc` exclude
unele alocări native și nu reprezintă RSS-ul complet. Nu se extrapolează rezultate
de nedetectabilitate sau performanță pe alte calculatoare.

Exemplul din `artifacts/sample/` are mesaj de 128 octeți, PSNR **72,535920 dB**,
SSIM **0,999992092318** și 713 valori RGB modificate. Histograma a fost verificată
vizual. Parola din manifest este publică, exclusiv pentru demonstrație.

## Limite ale verificării

Testele acoperă comportamentul implementat și o revizie de securitate a modulelor,
fără să constituie o certificare sau un audit extern. Docker nu a fost executat,
deoarece în mediul de verificare nu era disponibil un executabil Docker.
Nu au fost efectuate teste de încărcare distribuite, teste pe un corpus fotografic
sau validări pe Windows/Linux. Aceste platforme au instrucțiuni de instalare,
iar execuția verificată este pe macOS arm64 cu Python 3.13.7.

Pentru rulare publică sunt necesare HTTPS, controlul accesului conform utilizării,
limite la proxy și evaluarea memoriei pe infrastructura țintă. LSB secvențial și
magic-ul public rămân detectabile; PSNR/SSIM/χ² nu demonstrează contrariul.
