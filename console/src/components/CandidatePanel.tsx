"use client";

import { useEffect, useState } from "react";
import { api, type AskResult, type CandidateOrder } from "@/lib/api";
import { ArrowIcon, Button, Card, Empty, Meter, Rule, SpinnerIcon } from "./ui";

/**
 * Who Zamu would ask, in order, and everybody it ruled out.
 *
 * The exclusions are given equal weight to the shortlist on purpose. "Why isn't X on
 * this list?" is the first question any coordinator asks about a ranking, and an
 * interface that cannot answer it will not be trusted with the next decision.
 */
export function CandidatePanel({
  dutyId,
  onClose,
  onActed,
}: {
  dutyId: string;
  onClose: () => void;
  onActed: (result: AskResult) => void;
}) {
  const [order, setOrder] = useState<CandidateOrder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    let live = true;
    setOrder(null);
    setError(null);
    api
      .candidates(dutyId)
      .then((data) => live && setOrder(data))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [dutyId]);

  async function ask() {
    setAsking(true);
    try {
      onActed(await api.ask(dutyId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAsking(false);
    }
  }

  const first = order?.candidates[0];

  return (
    <Card as="section" className="zamu-enter p-5" aria-labelledby="candidates-heading">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="candidates-heading" className="text-lg font-bold tracking-tight">
            Who to ask
          </h2>
          {order ? (
            <p className="text-sm text-muted-foreground">
              {order.duty_title} · {order.when}
            </p>
          ) : null}
        </div>
        <Button variant="quiet" onClick={onClose}>
          Close
        </Button>
      </div>

      {error ? (
        <p className="rounded-lg bg-uncovered-soft px-3 py-2 text-sm text-uncovered">{error}</p>
      ) : null}

      {!order && !error ? (
        <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <SpinnerIcon /> Working out the fairest order…
        </p>
      ) : null}

      {order ? (
        <>
          {order.candidates.length === 0 ? (
            <Empty>
              Nobody Zamu is allowed to ask can cover this. Reducing scope or widening
              permission is a decision for you.
            </Empty>
          ) : (
            <ol className="space-y-3">
              {order.candidates.map((c) => (
                <li
                  key={c.person_id}
                  className={`rounded-[12px] border p-4 ${
                    c.rank === 1 ? "border-accent bg-muted/40" : "border-border"
                  }`}
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="font-bold">
                      <span className="mr-2 font-mono text-xs text-muted-foreground">
                        {c.rank}
                      </span>
                      {c.name}
                      {c.rank === 1 ? (
                        <span className="ml-2 rounded-full bg-accent px-2 py-0.5 text-[11px] text-on-accent">
                          asked first
                        </span>
                      ) : null}
                    </p>
                    <span className="font-mono text-xs tabular-nums text-muted-foreground">
                      {c.score.toFixed(3)}
                    </span>
                  </div>
                  <p className="mt-1 text-sm">{c.rationale}</p>
                  <div className="mt-3 space-y-1">
                    <Meter label="fairness" value={c.components.fairness} />
                    <Meter label="fit" value={c.components.fit} tone="muted" />
                    <Meter
                      label="responsiveness"
                      value={c.components.responsiveness}
                      tone="muted"
                    />
                    <Meter label="notice" value={c.components.notice} tone="muted" />
                    <Meter label="rest" value={c.components.rest} tone="muted" />
                  </div>
                </li>
              ))}
            </ol>
          )}

          {order.excluded.length ? (
            <div className="mt-5 border-t border-border pt-4">
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Not asked, and why
              </h3>
              <ul className="space-y-1.5">
                {order.excluded.map((e) => (
                  <li key={e.person_id} className="text-sm text-muted-foreground">
                    {e.reason}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-4">
            <Button onClick={ask} busy={asking} disabled={!first}>
              {first ? `Ask ${first.name}` : "Nobody to ask"}
              <ArrowIcon />
            </Button>
            <p className="text-xs text-muted-foreground">
              One person, one shift. Ranked from a team average of{" "}
              {order.team_average_load_hours.toFixed(1)}h.
            </p>
          </div>
        </>
      ) : null}
    </Card>
  );
}

export function OutcomeBanner({ result, onDismiss }: { result: AskResult; onDismiss: () => void }) {
  const tone = TONES[result.outcome] ?? "neutral";
  const styles = {
    good: "bg-covered-soft text-covered",
    warn: "bg-at-risk-soft text-at-risk",
    bad: "bg-uncovered-soft text-uncovered",
    neutral: "bg-muted text-muted-foreground",
  }[tone];

  return (
    <div className={`zamu-enter rounded-[12px] px-4 py-3 ${styles}`} role="status">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-bold">
            {LABELS[result.outcome] ?? result.outcome}
            {result.person_name ? ` · ${result.person_name}` : ""}
          </p>
          <p className="mt-0.5 text-sm">{result.detail}</p>
          {result.rationale ? <p className="mt-1 text-sm opacity-80">{result.rationale}</p> : null}
          {result.policy_rule ? (
            <p className="mt-2">
              <Rule rule={result.policy_rule} />
            </p>
          ) : null}
          {result.draft_text ? (
            <details className="mt-3">
              <summary className="cursor-pointer text-sm font-bold">
                Read the draft Zamu prepared
              </summary>
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-card p-3 text-xs text-card-foreground">
                {result.draft_text}
              </pre>
            </details>
          ) : null}
        </div>
        <Button variant="quiet" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}

const TONES: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  asked: "good",
  withdrawn: "good",
  drafted: "warn",
  deferred: "warn",
  blocked: "bad",
  no_candidates: "bad",
  failed: "bad",
  waiting: "neutral",
  already_covered: "neutral",
  replayed: "neutral",
};

const LABELS: Record<string, string> = {
  asked: "Asked",
  drafted: "Draft ready for you to send",
  deferred: "Waiting for a reasonable hour",
  blocked: "Zamu was not allowed to do this",
  no_candidates: "Nobody left to ask",
  waiting: "Already waiting on an answer",
  already_covered: "Already covered",
  replayed: "Already done — nothing repeated",
  failed: "That did not land",
  withdrawn: "Taken off the roster",
};
