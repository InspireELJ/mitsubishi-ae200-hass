/* Original geometric artwork and renderer. MIT licensed; see LICENSE. */
"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const sharp = require(process.env.AE200_SHARP_MODULE || "sharp");

const root = __dirname;
const pngSignature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function criticalChunksOnly(png) {
  if (!png.subarray(0, 8).equals(pngSignature)) throw new Error("Invalid PNG signature");
  const chunks = [pngSignature];
  let offset = 8;
  while (offset < png.length) {
    const length = png.readUInt32BE(offset);
    const end = offset + length + 12;
    if (end > png.length) throw new Error("Truncated PNG chunk");
    const type = png.toString("ascii", offset + 4, offset + 8);
    if (["IHDR", "IDAT", "IEND"].includes(type)) chunks.push(png.subarray(offset, end));
    else if (type[0] === type[0].toUpperCase()) throw new Error(`Unexpected critical chunk ${type}`);
    offset = end;
    if (type === "IEND") break;
  }
  if (offset !== png.length) throw new Error("Trailing PNG data");
  return Buffer.concat(chunks);
}

async function main() {
  const svg = await fs.readFile(path.join(root, "icon.svg"));
  const files = [];
  for (const [name, size] of [["icon.png", 256], ["icon@2x.png", 512]]) {
    const rendered = await sharp(svg, { density: 144 }).resize(size, size)
      .ensureAlpha().png({ compressionLevel: 9, palette: false, progressive: false }).toBuffer();
    const png = criticalChunksOnly(rendered);
    await fs.writeFile(path.join(root, name), png);
    files.push({ file: name, width: size, height: size, bytes: png.length,
      sha256: crypto.createHash("sha256").update(png).digest("hex") });
  }
  console.log(JSON.stringify({ renderer: "sharp", rendererVersion: sharp.versions.sharp,
    svgSha256: crypto.createHash("sha256").update(svg).digest("hex"), files }, null, 2));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
