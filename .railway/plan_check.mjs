// The wizard's plan check (#2060; scoped by structure, #2025):
//   node .railway/plan_check.mjs <plan.json> <adds> <destroys> [<service>]
// It reads a pinned `railway config plan --out` file, prints its change list, and exits 1 (REFUSED lines) unless:
// - the add/destroy counts are the expected ones, counted as runner.rs plan_summary_line counts them;
// - nothing is destructive when 0 destroys are expected, and no change touches a domain;
// - no change touches an existing service's source or networking; a create sets both, so only a create of <service>
//   is allowed, and an unscoped check refuses every create;
// - with <service>, every change TARGETS exactly that service, whatever its text names;
// - every change is one of the CLI's kinds, and an update's field can be read.
// Imports only node:fs, so CI runs it without `npm ci` (tests/test_railway_plan_check.py).
import { readFileSync } from "node:fs";

const [file, adds, destroys, service = ""] = process.argv.slice(2);
if (!file || !/^\d+$/.test(adds ?? "") || !/^\d+$/.test(destroys ?? "") || !/^[A-Za-z0-9_-]*$/.test(service)) {
  console.error("usage: node .railway/plan_check.mjs <plan.json> <adds> <destroys> [<service>]");
  process.exit(2);
}
let changes;
try {
  changes = JSON.parse(readFileSync(file, "utf8")).changeSet?.changes ?? [];
  if (!Array.isArray(changes)) throw new Error("changeSet.changes is not a list");
} catch (error) {
  console.log(`  REFUSED: unreadable plan ${file}: ${error.message}`);
  process.exit(1);
}

// The kinds the CLI writes (change_set.rs:182, :330, :807, :833, :1871); domain.* is refused below, any other kind
// as unknown.
const KINDS = ["resource.create", "resource.update", "resource.delete", "variable.set", "variable.delete"];

// The pinned file's changes are the backend preview's echo (engine.rs), not proven to keep `field` or `path`, so a
// change's fields are every one it can be read from: `field`, the path's segment after the address
// (`resources.service.sreality.source.repo`), and for an update each detail line's first segment ("source.repo (a →
// b)") and the summary's first leaf path ("Update sreality source.repo, … and 2 more", change_set.rs:1755-1767).
const fieldsOf = (c) => {
  const found = new Set();
  if (c.field) found.add(String(c.field));
  const segment = typeof c.path === "string" ? c.path.replace(/^resources\./, "").split(".")[2] : "";
  if (segment) found.add(segment);
  if (c.kind === "resource.update") {
    for (const detail of c.details || []) found.add(/^[^.\s(]*/.exec(String(detail))[0]);
    const summary = /^Update \S+ ([^.\s,]+)/.exec(String(c.summary || ""));
    if (summary) found.add(summary[1]);
  }
  found.delete("");
  return found;
};

// The CLI writes `address` and a `path` under `resources.<address>` on every change, and a create embeds the new
// resource, whose own address is structural (change_set.rs:181-189, :806-840, :1861-1877). Values never decide: the
// runner's reference variable names service.sreality inside its own create. With none of the three, the summary's
// service token decides (the CLI's summary shapes, change_set.rs:334, :812, :838, :1767). Fields that disagree, or no
// target at all, refuse the change: the check fails closed.
const SUMMARY_TARGETS = [
  /^(?:Create|Delete) service (\S+)/, /^(?:Set|Update|Delete) variable ([^.\s]+)\./, /^Update (\S+) /,
];
const targetOf = (c) => {
  const found = new Set();
  if (typeof c.address === "string") found.add(c.address);
  if (typeof c.resource === "string") found.add(c.resource);
  else if (typeof c.resource?.address === "string") found.add(c.resource.address);
  const path = typeof c.path === "string" ? /^(?:resources\.)?([^.]+\.[^.]+)/.exec(c.path) : null;
  if (path) found.add(path[1]);
  if (found.size > 1) return { problem: `conflicting targets ${[...found].join(", ")}` };
  if (found.size === 1) return { address: [...found][0] };
  for (const pattern of SUMMARY_TARGETS) {
    const match = pattern.exec(String(c.summary || ""));
    if (match) return { address: `service.${match[1]}` };
  }
  return { problem: "no readable target" };
};

const scope = service ? `service.${service}` : "";
const problems = [];
let add = 0;
let destroy = 0;
for (const c of changes) {
  const kind = String(c.kind || "");
  const fields = fieldsOf(c);
  const what = c.summary || c.path || kind;
  const target = targetOf(c);
  if (kind === "resource.create" || kind === "domain.create") add += 1;
  if (kind === "resource.delete" || kind === "variable.delete") destroy += 1;
  if (scope && target.problem) problems.push(`${target.problem}: ${what}`);
  else if (scope && target.address !== scope) problems.push(`outside ${service}: ${what}`);
  if (kind.startsWith("domain.")) problems.push(`touches ${kind}: ${what}`);
  else if (!KINDS.includes(kind)) problems.push(`unknown change kind ${kind || "(none)"}: ${what}`);
  else if (kind === "resource.create" && !scope) problems.push(`a create needs its service named: ${what}`);
  else if (kind === "resource.update" && fields.size === 0) problems.push(`no readable field: ${what}`);
  else if (kind !== "resource.create") {
    for (const field of ["source", "networking"]) if (fields.has(field)) problems.push(`touches ${field}: ${what}`);
  }
  if (c.severity === "destructive" && Number(destroys) === 0) problems.push(`destructive: ${what}`);
}
console.log(`  Plan: ${add} to add, ${changes.length - add - destroy} to change, ${destroy} to destroy`);
if (scope) console.log(`  Scope: every change must target ${scope}`);
for (const c of changes) {
  console.log(`    ${c.severity === "destructive" ? "-" : "~"} ${c.summary || c.path}`);
  for (const d of c.details || []) console.log(`        ${d}`);
}
if (add !== Number(adds)) problems.push(`${add} to add, expected ${adds}`);
if (destroy !== Number(destroys)) problems.push(`${destroy} to destroy, expected ${destroys}`);
for (const p of problems) console.log(`  REFUSED: ${p}`);
process.exit(problems.length ? 1 : 0);
