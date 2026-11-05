# Protocolul binar STEG profilul 1

Formatul respectă exact structura solicitată. Numerele sunt unsigned big-endian;
nu există padding, terminator NUL, compresie sau codare Base64 în imagine.

| Offset în octeți | Lungime | Conținut |
|---|---:|---|
| 0 | 4 | ASCII `STEG`, hex `53 54 45 47` |
| 4 | 16 | salt aleator PBKDF2 |
| 20 | 12 | IV/nonce aleator AES-GCM |
| 32 | 16 | tag de autentificare GCM |
| 48 | 4 | lungimea N a cifratului, uint32 big-endian |
| 52 | N | cifratul |

Header-ul ocupă **52 de octeți**, adică 416 canale RGB/pixel values.
AES-GCM nu folosește padding; N este exact lungimea textului clar codificat UTF-8.
Un caracter vizual nu coincide neapărat cu un code point sau cu un octet.

## Derivarea și autentificarea

```text
password_bytes = UTF8(password), fără trim sau normalizare Unicode
K = PBKDF2-HMAC-SHA256(password_bytes, salt16, 600000, dkLen=32)
AAD = b"STEG" || salt16 || iv12 || uint32_be(N)
ciphertext || tag16 = AES-256-GCM.encrypt(K, iv12, UTF8(message), AAD)
packet = b"STEG" || salt16 || iv12 || tag16 || uint32_be(N) || ciphertext
```

Saltul și IV-ul sunt generate separat prin `secrets.token_bytes` la fiecare criptare.
Sunt publice și se păstrează în header. Parola și cheia nu se includ în imagine.
Tag-ul nu se introduce în propriul AAD. Biblioteca verifică tag-ul înainte de a
returna orice text clar; `InvalidTag` devine `AuthenticationError`.

Acest AAD este o decizie de interoperabilitate explicită: o implementare care
folosește `AAD=None`, alt număr de iterații sau altă ordine a câmpurilor nu poate
decripta acest profil, chiar dacă header-ul are aceeași formă.
Testele includ un pachet creat independent cu `hashlib.pbkdf2_hmac` și `AESGCM`.

Formatul cerut nu rezervă un octet de versiune sau numărul de iterații.
Prin urmare, profilul este fix la 600.000 de iterații. **Nu modificați această constantă
pentru imagini existente.** O evoluție trebuie să definească alt magic sau un protocol
nou versionat; nu se ghicesc parametrii și nu se încearcă un fallback nesigur.

## Inserare și extragere

Pixelii sunt parcurși pe rânduri, de sus în jos și de la stânga la dreapta.
În fiecare pixel se folosesc R, apoi G, apoi B. Alfa nu este folosit.
Fiecare octet al pachetului este parcurs de la bitul 7 la bitul 0:

```text
pixel_channel = (pixel_channel & 0xFE) | next_bit
```

Se modifică numai prefixul necesar; restul canalelor rămân identice.
Extragerea citește mai întâi 416 biți, validează magic-ul și verifică
`0 <= N <= min(4500000, floor(3WH/8) - 52)`, apoi citește exact N octeți.
Un header care depășește capacitatea este respins înainte de citirea payload-ului.

Capacitatea brută este `floor(3WH/8)` octeți.
Capacitatea utilă este `max(0, min(floor(3WH/8)-52, 4500000))`.
O imagine prea mică pentru header nu poate conține nici un mesaj, chiar dacă API-ul
raportează capacitatea utilă zero. API-ul de inserare cere un mesaj nevid.

Pentru 1024 × 768 pixeli: 294.912 octeți brut, 294.860 octeți utili, maximum
294.860 caractere ASCII sau 73.715 code points dacă fiecare necesită 4 octeți.
Acestea sunt limite de codare, nu număr garantat de grafeme afișate.

## Ce este acoperit de integritate

Sunt autentificate textul și câmpurile salt, IV, lungime și magic din profil.
Magic-ul modificat este respins la parsare; celelalte modificări ajung fie la
validarea limitelor, fie la verificarea tag-ului. Nu se autentifică imaginea integrală,
canalele nefolosite, biții superiori, alfa sau metadatele. O modificare în afara
pachetului poate păstra decriptarea validă. AES-GCM nu constituie semnătură a
expeditorului: orice persoană care știe parola poate crea un mesaj valid.

Documentație primară: [AESGCM](https://cryptography.io/en/latest/hazmat/primitives/aead/),
[PBKDF2HMAC](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/).

