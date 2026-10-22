// Adapter: exposes Because's Python H-MEM (because/meterless/hmem.py) as the
// `HMEM` class the official Meterless conformance runner expects
// (engines/hmem/conformance/runner.ts). Each call shells out synchronously to
// the stateless Python bridge; state lives in a temp JSON file per instance.
//
//   HMEM_IMPL=$PWD/conformance/meterless/hmem-adapter.ts npx tsx <meterless>/engines/hmem/conformance/runner.ts
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PY = process.env.PYTHON ?? "python3";

function call(state: string, method: string, args: unknown[], now: number): any {
  const out = execFileSync(PY, ["-m", "because.meterless.bridge", "hmem", state, method, JSON.stringify(args), String(now)], {
    cwd: repoRoot,
    env: { ...process.env, PYTHONPATH: repoRoot },
    encoding: "utf-8",
    maxBuffer: 64 * 1024 * 1024,
  });
  const parsed = JSON.parse(out);
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed.result;
}

export class HMEM {
  private state: string;
  private clock: () => number;
  store: any;
  ledger: any;
  sleep: any;
  dreaming: any;
  conflicts: any;

  constructor(opts: { clock: () => number }) {
    this.clock = opts.clock;
    this.state = path.join(mkdtempSync(path.join(tmpdir(), "hmem-")), "state.json");
    const c = (m: string, ...a: unknown[]) => call(this.state, m, a, this.clock());
    // Records come back as live proxies: assigning a field writes through to Python.
    const live = (rec: any) =>
      rec && typeof rec === "object"
        ? new Proxy(rec, {
            set: (t, k, v) => {
              t[k as string] = v;
              c("store.set", rec.id, k, v);
              return true;
            },
          })
        : rec;
    this.store = { get: (id: string) => live(c("store.get", id)) ?? undefined, all: () => c("store.all") };
    this.ledger = { history: (id: string) => c("ledger.history", id) };
    this.sleep = { preview: () => c("sleep.preview"), execute: (p: unknown) => c("sleep.execute", p) };
    this.dreaming = { dream: () => c("dreaming.dream"), approve: (id: string) => c("dreaming.approve", id) };
    this.conflicts = { scan: () => c("conflicts.scan"), autoResolve: (x: unknown) => c("conflicts.autoResolve", x) };
    (this as any)._c = c;
    (this as any)._live = live;
  }
  add(item: unknown) {
    return (this as any)._live((this as any)._c("add", item));
  }
  query(q: string, opts?: unknown) {
    return (this as any)._c("query", q, opts ?? {});
  }
  feedback(id: string, kind: string) {
    return (this as any)._c("feedback", id, kind);
  }
}
