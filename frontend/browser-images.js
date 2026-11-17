import { Buffer } from "buffer";
import pngjs from "pngjs/browser.js";
import { MAX_PIXELS, MAX_UPLOAD_BYTES } from "./browser-stego.js";

const { PNG } = pngjs;
const pngSignature = [137, 80, 78, 71, 13, 10, 26, 10];

function fail(code, message) {
  throw Object.assign(new Error(message), { code });
}

function checkDimensions(width, height, pixelLimit) {
  if (width < 1 || height < 1) fail("INVALID_IMAGE", "Dimensiunile imaginii sunt invalide.");
  if (width * height <= pixelLimit) return;
  if (pixelLimit < MAX_PIXELS) {
    fail("ANALYSIS_TOO_LARGE", "Analiza SSIM acceptă cel mult 2 megapixeli per imagine.");
  }
  fail("IMAGE_TOO_LARGE", "Imaginea depășește limita de 12 megapixeli.");
}

async function checkInterlacedData(chunks, limit) {
  // The PNG decoder's interlaced path inflates without a byte limit.
  const reader = new Blob(chunks).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
  let length = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      length += value.length;
      if (length > limit) throw new Error("Excess PNG scanline data");
    }
  } finally {
    await reader.cancel();
  }
}

async function decodePng(bytes, pixelLimit) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < 33 || view.getUint32(8) !== 13 || view.getUint32(12) !== 0x49484452) {
    throw new Error("Invalid PNG header");
  }
  const width = view.getUint32(16);
  const height = view.getUint32(20);
  checkDimensions(width, height, pixelLimit);
  const depth = bytes[24];
  const colorType = bytes[25];
  const interlaced = bytes[28] === 1;
  const compressedChunks = [];
  let transparentColor;
  let transparencyStart;
  let transparencyEnd;
  for (let pos = 8; pos + 12 <= bytes.length;) {
    const length = view.getUint32(pos);
    const kind = view.getUint32(pos + 4);
    const next = pos + 12 + length;
    if (next > bytes.length) throw new Error("Truncated PNG chunk");
    if (kind === 0x6163544c) {
      fail("ANIMATED_IMAGE", "Imaginile animate nu sunt acceptate.");
    }
    if (kind === 0x49444154 && interlaced && length > 0) {
      compressedChunks.push(bytes.subarray(pos + 8, next - 4));
    }
    if (kind === 0x74524e53 && (colorType === 0 || colorType === 2)) {
      // pngjs zeros transparent RGB samples. Preserve them for LSB and metrics.
      if (transparentColor || length !== (colorType === 2 ? 6 : 2)) {
        throw new Error("Invalid PNG transparency");
      }
      transparentColor = Array.from({ length: length / 2 }, (_, i) => view.getUint16(pos + 8 + 2 * i));
      transparencyStart = pos;
      transparencyEnd = next;
    }
    if (kind === 0x49454e44) break;
    pos = next;
  }

  if (interlaced) await checkInterlacedData(compressedChunks, width * height * (depth === 16 ? 8 : 4) + 7 * height);
  // First pass checks all CRCs, including the transparency chunk if present.
  let decoded = PNG.sync.read(Buffer.from(bytes), { checkCRC: true, skipRescale: depth === 16 });
  if (transparentColor) {
    const source = Buffer.concat([
      Buffer.from(bytes.subarray(0, transparencyStart)),
      Buffer.from(bytes.subarray(transparencyEnd)),
    ]);
    decoded = PNG.sync.read(source, { checkCRC: true, skipRescale: depth === 16 });
    const [red, green = red, blue = red] = depth < 8
      ? transparentColor.map((sample) => Math.round(sample * 255 / (2 ** depth - 1)))
      : transparentColor;
    for (let p = 0; p < decoded.data.length; p += 4) {
      if (decoded.data[p] === red && decoded.data[p + 1] === green && decoded.data[p + 2] === blue) {
        decoded.data[p + 3] = 0;
      }
    }
  }
  const data = depth === 16 ? Uint8Array.from(decoded.data, (sample) => sample >>> 8) : decoded.data;
  return { width, height, data, mode: decoded.alpha || transparentColor ? "RGBA" : "RGB", format: "PNG" };
}

async function decodeBmp(bytes, pixelLimit) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const headerSize = view.getUint32(14, true);
  if (headerSize !== 12 && headerSize < 40) throw new Error("Unsupported BMP header");
  const width = headerSize === 12 ? view.getUint16(18, true) : view.getInt32(18, true);
  const height = headerSize === 12 ? view.getUint16(20, true) : Math.abs(view.getInt32(22, true));
  checkDimensions(width, height, pixelLimit);
  const image = await createImageBitmap(new Blob([bytes], { type: "image/bmp" }), { colorSpaceConversion: "none" });
  try {
    if (image.width !== width || image.height !== height) throw new Error("Invalid BMP dimensions");
    const canvas = new OffscreenCanvas(width, height);
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(image, 0, 0);
    const { data } = context.getImageData(0, 0, width, height);
    const alpha = data.some((sample, index) => (index & 3) === 3 && sample !== 255);
    return { width, height, data, mode: alpha ? "RGBA" : "RGB", format: "BMP" };
  } finally {
    image.close();
  }
}

export async function decodeImage(file, pixelLimit = MAX_PIXELS) {
  if (!(file instanceof Blob) || !file.size) fail("EMPTY_FILE", "Fișierul încărcat este gol.");
  if (file.size > MAX_UPLOAD_BYTES) fail("FILE_TOO_LARGE", "Imaginea depășește limita de 10 MiB.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  try {
    if (pngSignature.every((byte, index) => bytes[index] === byte)) return await decodePng(bytes, pixelLimit);
    if (bytes[0] === 66 && bytes[1] === 77) return await decodeBmp(bytes, pixelLimit);
  } catch (error) {
    if (typeof error.code === "string") throw error;
    fail("INVALID_IMAGE", "Imaginea este invalidă, trunchiată sau nu poate fi decodată în acest browser.");
  }
  fail("UNSUPPORTED_IMAGE", "Încarcă o imagine PNG sau BMP fără pierderi.");
}

export function encodePng({ width, height, data, mode }) {
  const bytes = PNG.sync.write({ width, height, data }, {
    inputColorType: 6,
    colorType: mode === "RGBA" ? 6 : 2,
    deflateLevel: 6,
    deflateStrategy: 0,
  });
  if (bytes.length > MAX_UPLOAD_BYTES) {
    fail("OUTPUT_TOO_LARGE", "PNG-ul rezultat depășește 10 MiB. Folosește o imagine sau un mesaj mai mic.");
  }
  return new Blob([bytes], { type: "image/png" });
}
