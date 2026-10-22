// Adapter: exposes Because's Python Scout (because/meterless/scout.py) as the
// `Scout` class the official Meterless eval harness expects
// (engines/scout-intent/evals/runner/index.ts).
//
//   SCOUT_IMPL=$PWD/conformance/meterless/scout-adapter.ts npx tsx <meterless>/engines/scout-intent/evals/runner/index.ts all
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PY = process.env.PYTHON ?? "python3";

export class Scout {
  private registry: string;
  constructor(opts: { intentRegistry: string; policyPack?: string | string[] }) {
    const packs = Array.isArray(opts.policyPack) ? opts.policyPack.join(",") : opts.policyPack ?? "default";
    this.registry = `${opts.intentRegistry}::${packs}`;
  }
  async decide(input: unknown): Promise<any> {
    const out = execFileSync(PY, ["-m", "because.meterless.bridge", "scout", this.registry, "decide", JSON.stringify(input)], {
      cwd: repoRoot,
      env: { ...process.env, PYTHONPATH: repoRoot },
      encoding: "utf-8",
    });
    const parsed = JSON.parse(out);
    if (!parsed.ok) throw new Error(parsed.error);
    return parsed.result;
  }
  async classify(input: unknown): Promise<any> {
    const r = await this.decide(input);
    return { candidates: r.candidates };
  }
}
