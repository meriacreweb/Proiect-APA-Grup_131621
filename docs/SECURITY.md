# Model de securitate și operare

Aplicația protejează confidențialitatea și integritatea mesajului într-o imagine
transportată fără modificarea pixelilor folosiți. Nu garantează ascunderea existenței
mesajului și nu constituie un produs certificat sau un audit independent de securitate.

## Criptografie

Profilul folosește PBKDF2-HMAC-SHA-256 la 600.000 de iterații, cheie AES de 256 biți,
salt aleator de 16 octeți, nonce aleator de 12 octeți și tag GCM complet de 16 octeți.
Recomandarea OWASP pentru PBKDF2-HMAC-SHA-256 în contextul stocării parolelor
este folosită drept reper pentru cost; adecvarea operațională se măsoară pe hardware-ul
real, iar această alegere nu implică o certificare FIPS a aplicației.
[Referință OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

Nonce-ul nu se reutilizează intenționat și nu se acceptă IV/salt de la client la
criptare. AES-GCM din `cryptography` verifică tag-ul înainte de a returna textul clar.
Nu se implementează manual AES sau o comparație de tag. AAD-ul leagă magic-ul,
saltul, nonce-ul și lungimea cifratului. Detaliile profilului sunt în PROTOCOL.md.
[Referință AESGCM](https://cryptography.io/en/latest/hazmat/primitives/aead/).

Parolele nu sunt normalizate și nu sunt trunchiate. Interfața și API-ul cer minimum
12 code points la inserare și maximum 1024 octeți UTF-8; lungimea nu garantează
entropia. Se recomandă o frază unică, aleatorie, păstrată într-un manager de parole.
Un atacator cu imaginea poate ghici parola offline; limitele de rată ale API-ului
nu împiedică acest atac. Nu există recuperare sau resetare a parolei.

## Suprafețe și controale

| Amenințare | Control implementat | Limită rămasă |
|---|---|---|
| Cifrat modificat sau parolă greșită | verificare tag și eroare comună | nu se identifică cine a modificat datele |
| Length fals în imagine | validare înainte de citire, limită pe imagine/mesaj | imaginea în sine nu este semnată |
| Upload deghizat sau corupt | semnătură PNG/BMP, decodare reală, verificare integritate fișier | vulnerabilitățile bibliotecilor cer actualizări |
| Bombe de decomprimare | limită 12 MP înainte de decodarea pixelilor | memoria are un factor multiplicativ față de pixelii RGB |
| Abuz de procesor/memorie | 24 MiB/corp, 10 MiB/fișier, 2 joburi, timeout și rată | controalele sunt locale procesului |
| Formular ambiguu | câmpuri exacte, UTF-8 strict, boundary terminal obligatoriu | limite HTTP trebuie aplicate și la proxy |
| Text decriptat interpretat ca HTML | `textContent`, fără `innerHTML` pentru date | clipboardul poate fi citit de alte aplicații autorizate |
| Origini web străine | host/origin allowlist, fără CORS, Fetch Metadata | un client non-browser poate omite/falsifica Origin |
| Persistență accidentală | parsare multipart în RAM, închidere fișiere, fără DB | swap, dump-uri și memoria runtime nu sunt șterse garantat |
| Loguri sau cache cu secrete | erori redactate, fără loguri de body, no-store | proxy-ul și infrastructura pot avea propriile loguri |

## Încredere și date sensibile

Browserul trimite serverului imaginea, parola și textul clar. Aceasta este arhitectura
cerută; criptarea are loc pe backend. Un administrator rău intenționat, malware pe
server, extensii de browser sau un terminal compromis pot observa datele.
Pentru eliminarea încrederii în server ar fi necesară o arhitectură nouă cu criptare
în browser și o analiză separată a implementării sale.

Interfața nu folosește localStorage, sessionStorage, cookies de aplicație sau analytics.
Object URL-urile sunt revocate când rezultatele sunt înlocuite ori sesiunea este
ștearsă. Butonul de ștergere elimină datele din interfață și anulează cererile în curs;
anularea HTTP nu garantează oprirea instantanee a unei operații deja pornite în thread.
Descărcările și copierea în clipboard sunt acțiuni explicite ale utilizatorului.
Resursele statică, inclusiv scripturile și stilurile, sunt locale și funcționează fără CDN.

## Rulare la distanță

Rularea implicită este pe `127.0.0.1`. Înainte de accesul din afara calculatorului:

1. Configurați HTTPS la un reverse proxy și redirecționați HTTP spre HTTPS.
2. Configurați gazdele și originile exacte, de exemplu:

   ```bash
   export STEG_ALLOWED_HOSTS="127.0.0.1,steg.example.org"
   export STEG_ALLOWED_ORIGINS="https://steg.example.org"
   ```

3. Aplicați autentificare/control de acces la proxy dacă serviciul nu este destinat
   publicului. Originea permisă nu este autentificare.
4. Limitați corpul HTTP la 24 MiB, timpul de upload și conexiunile/IP și în proxy.
5. Folosiți un singur worker pentru limitele incluse sau un limitator distribuit.
   În mod implicit IP-ul este peer-ul conexiunii; `X-Forwarded-For` nu este crezut.
   În spatele proxy-ului, toate cererile pot împărți aceeași cotă. Dacă activați
   proxy headers în Uvicorn, acceptați-le numai de la IP-uri proxy controlate.
6. Dezactivați logarea corpurilor, capturile de request în APM și dump-urile cu secrete;
   configurați retenția și accesul logurilor infrastructurii.
7. Rulați fără privilegii, cu limite de RAM/CPU. Validați pe hardware real dimensiunile
   maxime și concurența. Analiza SSIM web are o limită mai mică, de 2 MP.

HSTS se configurează la proxy numai după ce HTTPS funcționează corect.
Containerul inclus folosește utilizator fără privilegii și suportă filesystem read-only
cu `/tmp` în memorie. Nu include certificate sau secrete.

## Limitele steganografiei

Prefixul `STEG` și integrarea secvențială sunt publice. Un observator poate verifica
magic-ul fără parolă. AES-GCM rămâne responsabil de protecția conținutului.
PSNR/SSIM descriu fidelitatea imaginii; χ² oferă un indicator exploratoriu, cu
p-value dependent de ipotezele distribuției. Niciuna dintre aceste metrici nu
certifică nedetectabilitatea, anonimitatea sau rezistența la toate atacurile.

Fișierele PNG/BMP se transmit ca documente originale. Nu se aplică filtre,
redimensionare, conversie de paletă sau JPEG. Chiar și un PNG fără pierderi poate
fi rescris cu valori RGB diferite de o aplicație care aplică transformări de culoare.
Metadatele nu sunt copiate în rezultatul PNG. Nu există semnătură digitală a
expeditorului, autentificare a întregii imagini sau corectare a erorilor.

## Întreținere

Dependențele sunt fixate pentru reproducibilitate, nu pentru a elimina nevoia de
actualizări. Actualizările se fac controlat, cu verificarea vulnerabilităților curente
și rerularea testelor, inclusiv fixture-ul independent al protocolului și testele
parserului. Nu schimbați parametrii criptografici ai profilului în timpul unei
actualizări de dependențe. O revizie externă și un test de încărcare pe infrastructura
țintă rămân necesare pentru un serviciu public cu date sensibile.
