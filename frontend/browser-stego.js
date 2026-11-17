// Browser implementation of the same STEG wire profile as backend/app.
// Image data is an interleaved RGBA byte array, even for sources without alpha.
export const MAX_PIXELS = 12_000_000;
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MAX_MESSAGE_BYTES = 4_500_000;
export const MAX_PASSWORD_BYTES = 1024;
export const HEADER_SIZE = 52;

const MAGIC = new Uint8Array([0x53, 0x54, 0x45, 0x47]); // STEG
const SALT_OFFSET = 4;
const SALT_SIZE = 16;
const IV_OFFSET = 20;
const IV_SIZE = 12;
const TAG_OFFSET = 32;
const TAG_SIZE = 16;
const LENGTH_OFFSET = 48;
const PBKDF2_ITERATIONS = 600_000;
const encoder = new TextEncoder();
// Preserve a leading U+FEFF as message text, like Python's strict UTF-8 decoder.
const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const unpairedSurrogate = /[\uD800-\uDFFF]/u;

function fail(code, message) {
  throw Object.assign(new Error(message), { code });
}

function webCrypto() {
  const provider = globalThis.crypto;
  if (!provider?.subtle || !provider.getRandomValues) {
    fail("WEB_CRYPTO_UNAVAILABLE", "Criptarea locală necesită un browser cu Web Crypto, pe HTTPS sau localhost.");
  }
  return provider;
}

function pixelCount(image) {
  const { width, height, data } = image ?? {};
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height)
      || width < 1 || height < 1 || width * height > MAX_PIXELS) {
    fail("INVALID_DIMENSIONS", "Dimensiunile imaginii sunt invalide sau depășesc limita de 12 megapixeli.");
  }
  if (!(data instanceof Uint8Array || data instanceof Uint8ClampedArray)
      || data.length !== width * height * 4) {
    fail("INVALID_MODE", "Motorul LSB necesită un tablou valid de pixeli RGBA.");
  }
  return width * height;
}

export function capacity(image) {
  const total = Math.floor(pixelCount(image) * 3 / 8);
  const usable = Math.max(0, Math.min(total - HEADER_SIZE, MAX_MESSAGE_BYTES));
  return {
    width: image.width,
    height: image.height,
    total_capacity_bytes: total,
    header_bytes: HEADER_SIZE,
    max_payload_bytes: usable,
    max_characters_ascii: usable,
    max_characters_utf8_4byte: Math.floor(usable / 4),
    format: image.format || "PNG",
    mode: image.mode || "RGBA",
  };
}

function encodePassword(password, embedding) {
  if (typeof password !== "string" || !password || password.length > MAX_PASSWORD_BYTES) {
    fail("INVALID_PASSWORD", "Parola trebuie să aibă între 1 și 1.024 de octeți UTF-8.");
  }
  // TextEncoder replaces lone surrogates. Reject them to match Python strict UTF-8.
  if (unpairedSurrogate.test(password)) {
    fail("INVALID_PASSWORD", "Parola nu este UTF-8 valid.");
  }
  const bytes = encoder.encode(password);
  if (bytes.length > MAX_PASSWORD_BYTES) {
    bytes.fill(0);
    fail("INVALID_PASSWORD", "Parola trebuie să aibă între 1 și 1.024 de octeți UTF-8.");
  }
  if (embedding && Array.from(password).length < 12) {
    bytes.fill(0);
    fail("WEAK_PASSWORD", "Folosiți o parolă de cel puțin 12 caractere.");
  }
  return bytes;
}

async function deriveKey(provider, passwordBytes, salt, usage) {
  const material = await provider.subtle.importKey("raw", passwordBytes, "PBKDF2", false, ["deriveKey"]);
  return provider.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: PBKDF2_ITERATIONS },
    material, { name: "AES-GCM", length: 256 }, false, [usage],
  );
}

function aesParameters(header) {
  // AAD is magic || salt || IV || N: the header with its tag field omitted.
  const aad = new Uint8Array(TAG_OFFSET + 4);
  aad.set(header.subarray(0, TAG_OFFSET));
  aad.set(header.subarray(LENGTH_OFFSET, HEADER_SIZE), TAG_OFFSET);
  return {
    name: "AES-GCM",
    iv: header.subarray(IV_OFFSET, IV_OFFSET + IV_SIZE),
    additionalData: aad,
    tagLength: TAG_SIZE * 8,
  };
}

function writeLsb(pixels, bytes, firstByte) {
  // A packet byte occupies eight consecutive RGB channels. Skip alpha each time.
  const firstChannel = firstByte * 8;
  let channel = firstChannel % 3;
  let index = Math.floor(firstChannel / 3) * 4 + channel;
  for (const byte of bytes) {
    for (let shift = 7; shift >= 0; shift -= 1) {
      pixels[index] = (pixels[index] & 0xfe) | ((byte >>> shift) & 1);
      index += 1;
      if (++channel === 3) { index += 1; channel = 0; }
    }
  }
}

function readLsb(pixels, output, firstByte) {
  const firstChannel = firstByte * 8;
  let channel = firstChannel % 3;
  let index = Math.floor(firstChannel / 3) * 4 + channel;
  for (let i = 0; i < output.length; i += 1) {
    let byte = 0;
    for (let bit = 0; bit < 8; bit += 1) {
      byte = (byte << 1) | (pixels[index] & 1);
      index += 1;
      if (++channel === 3) { index += 1; channel = 0; }
    }
    output[i] = byte;
  }
}

/** Add a STEG packet to image.data in place. Return the same image. */
export async function embed(image, message, password) {
  const { max_payload_bytes: available } = capacity(image);
  if (typeof message !== "string") fail("INVALID_MESSAGE", "Mesajul trebuie să fie text.");
  if (!message) fail("EMPTY_MESSAGE", "Introduceți un mesaj de ascuns.");
  // UTF-8 is never shorter than the corresponding number of UTF-16 code units.
  if (message.length > available) {
    fail("CAPACITY_EXCEEDED", "Mesajul depășește capacitatea disponibilă a imaginii.");
  }
  if (unpairedSurrogate.test(message)) fail("INVALID_MESSAGE", "Mesajul nu este UTF-8 valid.");
  const provider = webCrypto();
  const passwordBytes = encodePassword(password, true);
  let clear;
  try {
    clear = encoder.encode(message);
    if (clear.length > available) {
      fail("CAPACITY_EXCEEDED", "Mesajul depășește capacitatea disponibilă a imaginii.");
    }
    const header = new Uint8Array(HEADER_SIZE);
    header.set(MAGIC);
    const salt = provider.getRandomValues(header.subarray(SALT_OFFSET, SALT_OFFSET + SALT_SIZE));
    provider.getRandomValues(header.subarray(IV_OFFSET, IV_OFFSET + IV_SIZE));
    new DataView(header.buffer).setUint32(LENGTH_OFFSET, clear.length, false);
    const key = await deriveKey(provider, passwordBytes, salt, "encrypt");
    const sealed = new Uint8Array(await provider.subtle.encrypt(aesParameters(header), key, clear));
    header.set(sealed.subarray(clear.length), TAG_OFFSET);
    writeLsb(image.data, header, 0);
    writeLsb(image.data, sealed.subarray(0, clear.length), HEADER_SIZE);
    return image;
  } finally {
    passwordBytes.fill(0);
    clear?.fill(0);
  }
}

export async function extract(image, password) {
  const available = Math.floor(pixelCount(image) * 3 / 8);
  if (available < HEADER_SIZE) {
    fail("IMAGE_TOO_SMALL", "Imaginea nu poate conține header-ul STEG.");
  }
  const header = new Uint8Array(HEADER_SIZE);
  readLsb(image.data, header, 0);
  if (!MAGIC.every((byte, index) => header[index] === byte)) {
    fail("NO_PAYLOAD", "Imaginea nu conține un mesaj STEG recunoscut.");
  }
  const length = new DataView(header.buffer).getUint32(LENGTH_OFFSET, false);
  if (length > MAX_MESSAGE_BYTES || length > available - HEADER_SIZE) {
    fail("INVALID_LENGTH", "Header-ul este corupt: lungimea declarată nu este validă.");
  }
  const provider = webCrypto();
  const passwordBytes = encodePassword(password, false);
  let clear;
  try {
    const sealed = new Uint8Array(length + TAG_SIZE);
    readLsb(image.data, sealed.subarray(0, length), HEADER_SIZE);
    sealed.set(header.subarray(TAG_OFFSET, LENGTH_OFFSET), length);
    const salt = header.subarray(SALT_OFFSET, SALT_OFFSET + SALT_SIZE);
    const key = await deriveKey(provider, passwordBytes, salt, "decrypt");
    try {
      clear = new Uint8Array(await provider.subtle.decrypt(aesParameters(header), key, sealed));
    } catch (error) {
      if (error?.name !== "OperationError") throw error;
      fail("AUTHENTICATION_FAILED", "Autentificare eșuată: parola este greșită sau datele au fost modificate.");
    }
    try {
      return decoder.decode(clear);
    } catch {
      fail("INVALID_TEXT", "Mesajul autentificat nu este text UTF-8 valid.");
    }
  } finally {
    passwordBytes.fill(0);
    clear?.fill(0);
  }
}
