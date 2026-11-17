import { decodeImage, encodePng } from "./browser-images.js";
import { analyze, MAX_ANALYSIS_PIXELS } from "./browser-metrics.js";
import { capacity, embed, extract } from "./browser-stego.js";

const textDecoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

async function execute(path, data) {
  switch (path) {
    case "/api/v1/capacity":
      return capacity(await decodeImage(data.image));
    case "/api/v1/embed": {
      const image = await decodeImage(data.image);
      const message = textDecoder.decode(await data.message.arrayBuffer());
      return encodePng(await embed(image, message, data.password));
    }
    case "/api/v1/extract":
      return { message: await extract(await decodeImage(data.image), data.password) };
    case "/api/v1/analyze": {
      const original = await decodeImage(data.original, MAX_ANALYSIS_PIXELS);
      const stego = await decodeImage(data.stego, MAX_ANALYSIS_PIXELS);
      return analyze(original, stego);
    }
    default:
      throw new Error("Unsupported operation");
  }
}

self.addEventListener("message", async ({ data: { path, fields } }) => {
  try {
    self.postMessage({ result: await execute(path, fields) });
  } catch (error) {
    self.postMessage({ error: typeof error.code === "string" && !error.code.startsWith("ERR_")
      ? error.message
      : "Imaginea nu a putut fi procesată în browser. Încearcă din nou sau alege o imagine mai mică." });
  }
}, { once: true });
