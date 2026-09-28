/**
 * The build step README.md referred to but that was never in the project.
 *
 * tsconfig sets "noEmit": true, so tsc type-checks only and esbuild produces the
 * three JavaScript bundles that manifest.json loads. The output paths here must
 * stay in sync with manifest.json:
 *
 *     background/background.js   <- manifest.background.service_worker
 *     content/content.js         <- manifest.content_scripts[0].js[0]
 *                                   and the scripting.executeScript fallback
 *                                   in background.ts
 *     popup/popup.js             <- loaded by popup/popup.html
 */
import { build } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outdir = resolve(root, "dist");

const entries = [
  { in: "src/background/background.ts", out: "background/background" },
  { in: "src/content/content.ts", out: "content/content" },
  { in: "src/popup/popup.ts", out: "popup/popup" },
  { in: "src/options/options.ts", out: "options/options" }
];

await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });

await build({
  entryPoints: entries.map((entry) => ({ in: resolve(root, entry.in), out: entry.out })),
  outdir,
  bundle: true,
  format: "esm",
  target: "chrome114",
  platform: "browser",
  sourcemap: false,
  legalComments: "none",
  logLevel: "info"
});

await cp(resolve(root, "manifest.json"), resolve(outdir, "manifest.json"));
await cp(resolve(root, "src/popup/popup.html"), resolve(outdir, "popup/popup.html"));
await cp(resolve(root, "src/popup/popup.css"), resolve(outdir, "popup/popup.css"));
await mkdir(resolve(outdir, "options"), { recursive: true });
await cp(resolve(root, "src/options/options.html"), resolve(outdir, "options/options.html"));
await cp(resolve(root, "src/options/options.css"), resolve(outdir, "options/options.css"));

console.log("Built to", outdir, "- load that folder as an unpacked extension.");
