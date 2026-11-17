import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const frontend = join(root, "frontend");
const dist = join(root, "dist");
const assets = ["_headers", "app.js", "styles.css", "mark.svg", "demo-cover.png"];
const index = await readFile(join(frontend, "index.html"), "utf-8");
if (!index.includes("<body>")) throw new Error("Expected a plain body tag in frontend/index.html.");

await rm(dist, { recursive: true, force: true });
await mkdir(dist);
await Promise.all([
  ...assets.map((name) => copyFile(join(frontend, name), join(dist, name))),
  writeFile(join(dist, "index.html"), index.replace("<body>", '<body data-processing="browser">')),
  build({
    entryPoints: [join(frontend, "image-worker.js")],
    outfile: join(dist, "image-worker.js"),
    platform: "browser",
    target: "es2022",
    bundle: true,
    minify: true,
    legalComments: "linked",
  }),
]);
