/**
 * Patches public/chunks.json with year, journal, and first_author from
 * catalog/papers.json without requiring a full re-embed.
 *
 * Run after fetch-zotero.py: node scripts/patch-paper-meta.mjs
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dir = path.dirname(fileURLToPath(import.meta.url));
const ROOT  = path.resolve(__dir, "..");

const papersPath = path.join(ROOT, "catalog", "papers.json");
const chunksPath = path.join(ROOT, "public", "chunks.json");

const { papers } = JSON.parse(fs.readFileSync(papersPath, "utf8"));
const chunks     = JSON.parse(fs.readFileSync(chunksPath, "utf8"));

// Build DOI → paper map
const byDoi = new Map(papers.filter(p => p.doi).map(p => [p.doi, p]));

let patched = 0;
for (const chunk of chunks) {
  if (chunk.type !== "paper") continue;
  const doi = chunk.id.replace(/^paper::/, "");
  const paper = byDoi.get(doi);
  if (!paper) continue;
  chunk.year         = paper.year ?? null;
  chunk.journal      = paper.journal ?? null;
  chunk.first_author = paper.first_author ?? null;
  patched++;
}

fs.writeFileSync(chunksPath, JSON.stringify(chunks));
console.log(`Patched ${patched} paper chunks in public/chunks.json`);
