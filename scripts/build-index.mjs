/**
 * aRCADA RAG index builder.
 * Reads catalog/instruments.json → chunks text → embeds via Gemini →
 * writes public/embeddings.bin, public/chunks.json, public/search-index.json
 *
 * Adapted from vaultnotes/scripts/index-notes.mjs
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dir = path.dirname(fileURLToPath(import.meta.url));
const ROOT  = path.resolve(__dir, "..");

const CONFIG       = JSON.parse(fs.readFileSync(path.join(ROOT, "rag-config.json"), "utf8"));
const CATALOG      = JSON.parse(fs.readFileSync(path.join(ROOT, "catalog", "instruments.json"), "utf8"));
const PI_PAGES     = JSON.parse(fs.readFileSync(path.join(ROOT, "catalog", "pi-pages.json"), "utf8"));
const PAPERS       = loadOptional(path.join(ROOT, "catalog", "papers.json"));
const M2M_META     = loadOptional(path.join(ROOT, "catalog", "m2m-metadata.json"));
const RCA_CONTEXT  = loadOptional(path.join(ROOT, "catalog", "rca-context.json"));
const GEMINI_KEY   = process.env.GEMINI_API_KEY;

function loadOptional(p) {
  try { return JSON.parse(fs.readFileSync(p, "utf8")); }
  catch { console.warn(`  (optional) ${path.basename(p)} not found — skipping`); return null; }
}
const EMBED_URL    = `https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents?key=${GEMINI_KEY}`;
const OUT_DIR      = path.join(ROOT, "public");

if (!GEMINI_KEY) { console.error("GEMINI_API_KEY not set"); process.exit(1); }
fs.mkdirSync(OUT_DIR, { recursive: true });

// ── Chunk instruments ─────────────────────────────────────────────────────────

function instrumentToText(inst) {
  const lines = [
    `Name: ${inst.name}`,
    `Type: ${inst.type}`,
    `Location: ${inst.location}`,
    `Source: ${inst.source}`,
    `Description: ${inst.description}`,
    `Keywords: ${(inst.keywords || []).join(", ")}`,
    inst.latitude  != null ? `Latitude: ${inst.latitude}` : null,
    inst.longitude != null ? `Longitude: ${inst.longitude}` : null,
    inst.depth_m   != null ? `Depth: ${inst.depth_m} m` : null,
    inst.start_date ? `Data available from: ${inst.start_date}` : null,
    inst.units      ? `Units: ${JSON.stringify(inst.units)}` : null,
    inst.sample_rate_hz ? `Sample rate: ${inst.sample_rate_hz} Hz` : null,
    inst.channels   ? `Channels: ${inst.channels.join(", ")}` : null,
    inst.site       ? `OOI site: ${inst.site} / node: ${inst.node}` : null,
    inst.network    ? `EarthScope network: ${inst.network} / station: ${inst.station}` : null,
    inst.pi_base_url ? `PI data URL: ${inst.pi_base_url}` : null,
  ].filter(Boolean);
  return lines.join("\n");
}

function makeChunks() {
  const chunks = [];

  // ── Instrument chunks ──────────────────────────────────────────────────────
  for (const inst of [...CATALOG.instruments, ...PI_PAGES.instruments]) {
    const m2m    = M2M_META?.instruments?.[inst.id];
    const params = m2m?.parameters ?? [];

    // Augment instrument text with M2M parameter names
    const paramLine = params.length
      ? `Parameters measured: ${params.map(p => p.display_name || p.name).join(", ")}`
      : null;

    const text = [instrumentToText(inst), paramLine].filter(Boolean).join("\n");
    const embedText = `Instrument: ${inst.name}\n\n${text}`;

    chunks.push({
      id:       inst.id,
      title:    inst.name,
      type:     inst.type,
      source:   inst.source,
      location: inst.location || null,
      keywords: inst.keywords || [],
      text,
      embedText,
    });

    // Description chunk
    if (inst.description && inst.description.length > 100) {
      chunks.push({
        id:       `${inst.id}::desc`,
        title:    `${inst.name} (description)`,
        type:     inst.type,
        source:   inst.source,
        location: inst.location || null,
        keywords: inst.keywords || [],
        text:     inst.description,
        embedText: `${inst.name}: ${inst.description}`,
      });
    }

    // M2M parameters chunk — what the instrument actually measures
    if (params.length > 0) {
      const paramText = params
        .filter(p => p.name)
        .map(p => `${p.display_name || p.name}${p.units ? ` (${p.units})` : ""}${p.description ? ": " + p.description : ""}`)
        .join("\n");
      chunks.push({
        id:       `${inst.id}::params`,
        title:    `${inst.name} — measured parameters`,
        type:     inst.type,
        source:   inst.source,
        location: inst.location || null,
        keywords: inst.keywords || [],
        text:     `Parameters for ${inst.name}:\n${paramText}`,
        embedText: `What does ${inst.name} measure?\n${paramText}`,
      });
    }
  }

  // ── RCA site context chunks ────────────────────────────────────────────────
  if (RCA_CONTEXT) {
    for (const page of RCA_CONTEXT.pages) {
      if (!page.description) continue;
      chunks.push({
        id:       `rca::${page.id}`,
        title:    page.title,
        type:     "site-context",
        source:   "rca-website",
        location: page.location || null,
        keywords: ["regional cabled array", "OOI", "RCA", page.location || ""].filter(Boolean),
        text:     page.description,
        embedText: `About the ${page.title}:\n${page.description}`,
        linked_instruments: page.instruments || [],
      });
    }
  }

  // ── Zotero paper chunks ────────────────────────────────────────────────────
  if (PAPERS) {
    for (const paper of PAPERS.papers) {
      if (!paper.abstract || paper.abstract.length < 80) continue;
      const yearStr = paper.year ? ` (${paper.year})` : "";
      const journalStr = paper.journal ? ` — ${paper.journal}` : "";
      const linkStr = paper.linked_instruments?.length
        ? `\nRelevant instruments: ${paper.linked_instruments.join(", ")}`
        : "";
      const text = `${paper.title}${yearStr}${journalStr}\n\n${paper.abstract}${linkStr}`;
      chunks.push({
        id:       `paper::${paper.doi || paper.title.slice(0, 40).replace(/\s+/g, "-")}`,
        title:    paper.title,
        type:     "paper",
        source:   "zotero",
        location: null,
        keywords: paper.tags || [],
        text,
        embedText: `Research paper: ${paper.title}\n\n${paper.abstract}`,
        linked_instruments: paper.linked_instruments || [],
      });
    }
  }

  return chunks;
}

// ── Embed ─────────────────────────────────────────────────────────────────────

async function embedBatch(texts, retries = 4) {
  const requests = texts.map(t => ({
    model: "models/gemini-embedding-001",
    content: { parts: [{ text: t }] },
    taskType: "RETRIEVAL_DOCUMENT",
  }));

  for (let attempt = 0; attempt < retries; attempt++) {
    const res = await fetch(EMBED_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requests }),
    });
    if (res.ok) {
      const data = await res.json();
      return data.embeddings.map(e => e.values);
    }
    const errText = await res.text();
    console.warn(`Embed attempt ${attempt + 1} failed (${res.status}): ${errText.slice(0, 120)}`);
    await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
  }
  throw new Error("Embedding failed after retries");
}

function l2Normalize(vec) {
  const norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0));
  return norm > 0 ? vec.map(v => v / norm) : vec;
}

// ── MiniSearch index ──────────────────────────────────────────────────────────

function buildSearchIndex(chunks) {
  // Minimal BM25-ready index structure for MiniSearch
  return {
    version:   "1.0",
    fields:    ["title", "text", "keywords", "type", "location"],
    documents: chunks.map(c => ({
      id:       c.id,
      title:    c.title,
      text:     c.text,
      keywords: (c.keywords || []).join(" "),
      type:     c.type,
      location: c.location || "",
    })),
  };
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log("Building aRCADA RAG index...");
  const chunks = makeChunks();
  const instrCount = CATALOG.instruments.length + PI_PAGES.instruments.length;
  const paperCount = PAPERS?.papers?.length ?? 0;
  const pageCount  = RCA_CONTEXT?.pages?.length ?? 0;
  console.log(`  ${chunks.length} chunks from ${instrCount} instruments, ${paperCount} papers, ${pageCount} RCA pages`);

  // Embed in batches
  const batchSize = CONFIG.batchSize || 25;
  const allVecs   = [];

  for (let i = 0; i < chunks.length; i += batchSize) {
    const batch = chunks.slice(i, i + batchSize);
    console.log(`  Embedding batch ${Math.floor(i / batchSize) + 1}/${Math.ceil(chunks.length / batchSize)}...`);
    const vecs = await embedBatch(batch.map(c => c.embedText));
    for (const v of vecs) allVecs.push(l2Normalize(v));
    await new Promise(r => setTimeout(r, 200)); // stay under rate limit
  }

  // Write embeddings.bin (Float32, row-major)
  const dim  = allVecs[0].length;
  const bin  = new Float32Array(allVecs.length * dim);
  allVecs.forEach((v, i) => bin.set(v, i * dim));
  fs.writeFileSync(path.join(OUT_DIR, "embeddings.bin"), Buffer.from(bin.buffer));
  console.log(`  embeddings.bin: ${allVecs.length} × ${dim}`);

  // Write chunks.json (strip embedText to save bandwidth)
  const chunksOut = chunks.map(({ embedText, ...rest }) => rest);
  fs.writeFileSync(path.join(OUT_DIR, "chunks.json"), JSON.stringify(chunksOut));
  console.log(`  chunks.json: ${chunksOut.length} entries`);

  // Write search-index.json
  const searchIdx = buildSearchIndex(chunks);
  fs.writeFileSync(path.join(OUT_DIR, "search-index.json"), JSON.stringify(searchIdx));
  console.log(`  search-index.json: ${searchIdx.documents.length} documents`);

  console.log("Done.");
}

main().catch(e => { console.error(e); process.exit(1); });
