const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Health = {
  status: string;
  app_env: string;
  demo_mode: boolean;
  database: string;
  payments: string;
  llm: string;
};

async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export default async function Home() {
  const health = await getHealth();

  const rows = health
    ? [
        ["API", health.status],
        ["Environment", health.app_env],
        ["Database", health.database],
        ["Payments", health.payments],
        ["LLM", health.llm],
      ]
    : [];

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <div className="mx-auto max-w-3xl px-6 py-24">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-neutral-500">
          RecoveryOS
        </p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight">
          Autonomous revenue recovery
        </h1>
        <p className="mt-3 max-w-xl leading-relaxed text-neutral-400">
          Detects revenue at risk, diagnoses why it happened, chooses the optimal
          recovery action within merchant guardrails, and measures what it recovered.
        </p>

        <div className="mt-12 overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900/40">
          <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
            <h2 className="text-sm font-medium text-neutral-300">System status</h2>
            {health?.demo_mode && (
              <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-400">
                Demo mode
              </span>
            )}
          </div>

          {health ? (
            <dl className="divide-y divide-neutral-800">
              {rows.map(([label, value]) => (
                <div
                  key={String(label)}
                  className="flex justify-between gap-6 px-5 py-3 text-sm"
                >
                  <dt className="text-neutral-500">{label}</dt>
                  <dd className="text-right font-mono text-neutral-200">
                    {String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="px-5 py-6 text-sm text-amber-400">
              Backend unreachable. Start it with{" "}
              <code className="font-mono text-amber-300">
                python -m uvicorn app.main:app --reload --port 8000
              </code>
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
