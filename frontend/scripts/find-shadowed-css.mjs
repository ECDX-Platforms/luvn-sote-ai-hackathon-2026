// Report every declaration in src/styles.css that a later declaration of the same property,
// same selector, and overlapping media context makes unreachable.
//
// The rule: within one "context" (the set of media conditions a rule sits inside), a declaration is
// shadowed when a later rule in the SAME context has the SAME selector list and sets the same property.
// Media conditions are compared as text. This is deliberately conservative — it only reports exact
// selector-list matches, so a rule that is merely out-specified is never called dead here.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import postcss from "postcss";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "src", "styles.css"), "utf8");
const root = postcss.parse(css);

/** The media condition text a node sits inside, or "" at the top level. */
function contextOf(node) {
  const chain = [];
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (parent.type === "atrule" && parent.name === "media") chain.unshift(parent.params.trim());
    else if (parent.type === "atrule") chain.unshift(`@${parent.name} ${parent.params}`.trim());
  }
  return chain.join(" && ");
}

const byKey = new Map(); // context|selector|prop -> [line, ...]
const reports = [];

root.walkDecls((decl) => {
  if (decl.parent.type !== "rule") return;
  const context = contextOf(decl);
  const selector = decl.parent.selector.replace(/\s+/g, " ").trim();
  const key = `${context}\u0000${selector}\u0000${decl.prop}`;
  const previous = byKey.get(key);
  if (previous) {
    reports.push({
      context,
      selector,
      prop: decl.prop,
      deadLine: previous.line,
      deadValue: previous.value,
      liveLine: decl.source.start.line,
      liveValue: decl.value,
    });
  }
  // Only the last declaration wins, so keep the latest line.
  byKey.set(key, { line: decl.source.start.line, value: decl.value });
});

// A rule whose every declaration is shadowed is wholly dead; report it once.
const byRule = new Map();
for (const report of reports) {
  const key = `${report.context}\u0000${report.selector}\u0000${report.deadLine}`;
  if (!byRule.has(key)) byRule.set(key, []);
  byRule.get(key).push(report);
}

console.log(`fully/partly shadowed declarations: ${reports.length}`);
for (const items of byRule.values()) {
  const { context, selector } = items[0];
  const where = context ? `[${context}] ` : "";
  console.log(`\n${where}${selector}`);
  for (const item of items) {
    console.log(`  L${item.deadLine} ${item.prop}: ${item.deadValue}   <- shadowed by L${item.liveLine} ${item.prop}: ${item.liveValue}`);
  }
}
