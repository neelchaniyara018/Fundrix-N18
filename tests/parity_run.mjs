// node tests/parity_run.mjs <static_dir> <cases.json> <out.json>  -> runs every case through frontend/engine.js
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const Engine = require("../frontend/engine.js");
const [dir, casesPath, outPath] = process.argv.slice(2);
const rd = f => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
const mx = rd("metrics.json"), meta = rd("meta.json"), dates = rd("dates.json");
const E = Engine.create({ metrics: mx.metrics, groups: mx.groups, functions: mx.functions, funds: rd("funds.json"), dates, meta });
const shard = id => rd(`nav/${id % meta.shards}.json`);
const nav = id => shard(id).nav[String(id)];
const out = [];
for (const c of JSON.parse(fs.readFileSync(casesPath, "utf8"))) {
  try {
    const ctx = { watch: new Set(c.watch || []) }; let r;
    if (c.type === "screen") r = E.screen(c.body, ctx);
    else if (c.type === "export") r = E.exportCsv(c.body, ctx).text;
    else if (c.type === "validate") r = E.validate(c.expr);
    else if (c.type === "detail") r = E.fundDetail(c.id, shard(c.id).detail[String(c.id)]);
    else if (c.type === "calc") r = E.calculator(c.id, nav(c.id), c.params);
    else if (c.type === "compare") r = E.compare(c.ids, c.ids.map(nav));
    else if (c.type === "meta") r = E.metaFor();
    out.push({ ok: true, data: r });
  } catch (e) { out.push({ ok: false, status: e.status || 500, message: String(e.message) }); }
}
fs.writeFileSync(outPath, JSON.stringify(out));
