#!/usr/bin/env node
/**
 * Export docs/diagrams/*.excalidraw to SVG for embedding in markdown.
 *
 * Zero dependencies. Supports the constrained element subset used by the
 * diagrams in this repo: rectangle, ellipse, diamond, line, arrow, text
 * (standalone or container-bound). Files remain fully editable at
 * https://excalidraw.com — re-run this script after editing:
 *
 *   node scripts/export_diagrams.mjs [file.excalidraw ...]
 *
 * With no arguments, exports every .excalidraw file in docs/diagrams/.
 */

import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIAGRAM_DIR = join(ROOT, "docs", "diagrams");

const FONT = "Virgil, 'Segoe Print', 'Comic Sans MS', 'Marker Felt', cursive";
const PAD = 24;

function esc(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function dashFor(el) {
  if (el.strokeStyle === "dashed") return ' stroke-dasharray="8 6"';
  if (el.strokeStyle === "dotted") return ' stroke-dasharray="2 4"';
  return "";
}

function strokeAttrs(el) {
  const w = el.strokeWidth ?? 1.5;
  return `stroke="${el.strokeColor ?? "#1e1e1e"}" stroke-width="${w}"${dashFor(el)}`;
}

function fillAttr(el) {
  const bg = el.backgroundColor;
  if (!bg || bg === "transparent") return 'fill="none"';
  return `fill="${bg}"`;
}

function textLines(el) {
  return String(el.text ?? "").split("\n");
}

function renderText(el, byId) {
  const fontSize = el.fontSize ?? 16;
  const lineHeight = fontSize * (el.lineHeight ?? 1.25);
  const lines = textLines(el);
  const color = el.strokeColor ?? "#1e1e1e";
  const weight = el.fontWeight ?? (el.bold ? "bold" : "normal");

  let anchor = "start";
  let x = el.x;
  let yTop = el.y;

  const container = el.containerId ? byId.get(el.containerId) : null;
  if (container) {
    // Center the label inside its container.
    anchor = "middle";
    x = container.x + container.width / 2;
    yTop = container.y + (container.height - lines.length * lineHeight) / 2;
  } else if (el.textAlign === "center") {
    anchor = "middle";
    x = el.x + (el.width ?? 0) / 2;
  } else if (el.textAlign === "right") {
    anchor = "end";
    x = el.x + (el.width ?? 0);
  }

  const spans = lines
    .map((line, i) => {
      const y = yTop + lineHeight * i + fontSize * 0.8;
      return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" font-family="${FONT}" font-size="${fontSize}" font-weight="${weight}" fill="${color}" text-anchor="${anchor}">${esc(line)}</text>`;
    })
    .join("\n  ");
  return spans;
}

function renderElement(el, byId) {
  switch (el.type) {
    case "rectangle": {
      const rx = el.roundness ? 10 : 0;
      return `<rect x="${el.x}" y="${el.y}" width="${el.width}" height="${el.height}" rx="${rx}" ${fillAttr(el)} ${strokeAttrs(el)}/>`;
    }
    case "ellipse": {
      const cx = el.x + el.width / 2;
      const cy = el.y + el.height / 2;
      return `<ellipse cx="${cx}" cy="${cy}" rx="${el.width / 2}" ry="${el.height / 2}" ${fillAttr(el)} ${strokeAttrs(el)}/>`;
    }
    case "diamond": {
      const { x, y, width: w, height: h } = el;
      const pts = `${x + w / 2},${y} ${x + w},${y + h / 2} ${x + w / 2},${y + h} ${x},${y + h / 2}`;
      return `<polygon points="${pts}" ${fillAttr(el)} ${strokeAttrs(el)}/>`;
    }
    case "line":
    case "arrow": {
      const raw = el.points ?? [[0, 0], [el.width ?? 0, el.height ?? 0]];
      const abs = raw.map(([dx, dy]) => [el.x + dx, el.y + dy]);
      const pts = abs.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`);
      let head = "";
      if (el.type === "arrow" && abs.length >= 2) {
        const [x1, y1] = abs[abs.length - 2];
        const [x2, y2] = abs[abs.length - 1];
        const ang = Math.atan2(y2 - y1, x2 - x1);
        const L = 11, W = 4.5;
        const p = (a, d) =>
          `${(x2 - L * Math.cos(ang) - d * W * Math.sin(ang) * a).toFixed(1)},${(y2 - L * Math.sin(ang) + d * W * Math.cos(ang) * a).toFixed(1)}`;
        head = `\n  <polygon points="${x2.toFixed(1)},${y2.toFixed(1)} ${p(1, 1)} ${p(1, -1)}" fill="${el.strokeColor ?? "#1e1e1e"}"/>`;
      }
      return `<polyline points="${pts.join(" ")}" fill="none" ${strokeAttrs(el)} stroke-linejoin="round" stroke-linecap="round"/>${head}`;
    }
    case "text":
      return renderText(el, byId);
    default:
      return `<!-- unsupported element type: ${el.type} -->`;
  }
}

function bounds(elements) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const el of elements) {
    if (el.isDeleted) continue;
    let x0 = el.x, y0 = el.y;
    let x1 = el.x + (el.width ?? 0), y1 = el.y + (el.height ?? 0);
    if ((el.type === "line" || el.type === "arrow") && el.points) {
      for (const [dx, dy] of el.points) {
        x0 = Math.min(x0, el.x + dx); y0 = Math.min(y0, el.y + dy);
        x1 = Math.max(x1, el.x + dx); y1 = Math.max(y1, el.y + dy);
      }
    }
    if (el.type === "text") {
      const fs = el.fontSize ?? 16;
      const lines = textLines(el);
      y1 = Math.max(y1, el.y + lines.length * fs * (el.lineHeight ?? 1.25));
      x1 = Math.max(x1, el.x + (el.width ?? Math.max(...lines.map((l) => l.length)) * fs * 0.55));
    }
    minX = Math.min(minX, x0); minY = Math.min(minY, y0);
    maxX = Math.max(maxX, x1); maxY = Math.max(maxY, y1);
  }
  return { minX, minY, maxX, maxY };
}

function exportFile(path) {
  const scene = JSON.parse(readFileSync(path, "utf8"));
  const elements = (scene.elements ?? []).filter((el) => !el.isDeleted);
  const byId = new Map(elements.map((el) => [el.id, el]));
  const { minX, minY, maxX, maxY } = bounds(elements);
  const w = Math.ceil(maxX - minX + PAD * 2);
  const h = Math.ceil(maxY - minY + PAD * 2);

  // Containers first, then lines/arrows, then text on top.
  const order = { rectangle: 0, ellipse: 0, diamond: 0, line: 1, arrow: 1, text: 2 };
  const sorted = [...elements].sort((a, b) => (order[a.type] ?? 0) - (order[b.type] ?? 0));

  const body = sorted.map((el) => renderElement(el, byId)).join("\n  ");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${(minX - PAD).toFixed(0)} ${(minY - PAD).toFixed(0)} ${w} ${h}" width="${w}" height="${h}" font-family="${FONT}">
  <rect x="${(minX - PAD).toFixed(0)}" y="${(minY - PAD).toFixed(0)}" width="${w}" height="${h}" fill="#ffffff"/>
  ${body}
</svg>
`;
  const out = path.replace(/\.excalidraw$/, ".svg");
  writeFileSync(out, svg);
  console.log(`exported ${basename(path)} -> ${basename(out)} (${w}x${h})`);
}

const args = process.argv.slice(2);
const files = args.length
  ? args
  : readdirSync(DIAGRAM_DIR).filter((f) => f.endsWith(".excalidraw")).map((f) => join(DIAGRAM_DIR, f));

if (!files.length) {
  console.error("no .excalidraw files found");
  process.exit(1);
}
files.forEach(exportFile);
