"use client";

import { useState } from "react";
import { api, type AgentRun } from "@/lib/api";
import { Button, Card, LockIcon, Rule } from "./ui";

/**
 * Hand the roster to the agent.
 *
 * Shows the tools it called and, crucially, anything the policy gate refused. A run
 * that was blocked looks different from a run that had nothing to do, and conflating
 * the two is how an agent quietly stops working without anybody noticing.
 */
export function AgentPanel({ onFinished }: { onFinished: () => void }) {
  const [message, setMessage] = useState(
    "Check the roster and handle whatever needs doing.",
  );
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      setRun(await api.runAgent(message));
      onFinished();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card as="section" className="p-5" aria-labelledby="agent-heading">
      <h2 id="agent-heading" className="text-lg font-bold tracking-tight">
        Run Zamu
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Zamu normally runs on a schedule. This is the same loop, on demand.
      </p>

      <label htmlFor="agent-message" className="mt-4 block text-sm font-bold">
        What should it look at?
      </label>
      <textarea
        id="agent-message"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={2}
        className="mt-1.5 w-full rounded-lg border border-border bg-card px-3 py-2 text-base"
      />

      <div className="mt-3">
        <Button onClick={go} busy={busy}>
          {busy ? "Working" : "Run"}
        </Button>
      </div>

      {error ? (
        <p className="mt-3 rounded-lg bg-uncovered-soft px-3 py-2 text-sm text-uncovered">
          {error}
        </p>
      ) : null}

      {run ? (
        <div className="zamu-enter mt-5 border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">model: {run.model}</p>

          {run.tools_called.length ? (
            <ol className="mt-3 flex flex-wrap gap-1.5">
              {run.tools_called.map((tool, i) => (
                <li
                  key={`${tool}-${i}`}
                  className="rounded-md bg-muted px-2 py-1 font-mono text-[11px] text-muted-foreground"
                >
                  {i + 1}. {tool}
                </li>
              ))}
            </ol>
          ) : null}

          {run.refusals.length ? (
            <div className="mt-4 rounded-lg bg-at-risk-soft p-3">
              <h3 className="flex items-center gap-1.5 text-sm font-bold text-at-risk">
                <LockIcon className="size-4" />
                Refused before the tool ran
              </h3>
              <ul className="mt-2 space-y-1.5">
                {run.refusals.map((r, i) => (
                  <li key={i} className="text-sm text-at-risk">
                    <span className="font-mono text-xs">{r.tool}</span> — {r.reason}{" "}
                    <Rule rule={r.rule} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <pre className="mt-4 whitespace-pre-wrap rounded-lg bg-muted p-3 text-sm">
            {run.reply}
          </pre>
        </div>
      ) : null}
    </Card>
  );
}
