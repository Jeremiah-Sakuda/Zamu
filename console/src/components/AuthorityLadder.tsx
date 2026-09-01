"use client";

import { useState } from "react";
import type { Grant } from "@/lib/api";
import { api } from "@/lib/api";
import { Card, CheckIcon, LockIcon, SpinnerIcon } from "./ui";

/**
 * The trust ladder.
 *
 * Shown as five rungs rather than a settings list, because the escalation is the
 * point: each level is granted separately and none implies the one above it. Level 4
 * is displayed permanently, greyed out and unreachable, so the coordinator can see
 * the boundary rather than having to trust a promise it exists.
 */
export function AuthorityLadder({
  orgId,
  grants,
  onChanged,
}: {
  orgId: string;
  grants: Grant[];
  onChanged: (grants: Grant[]) => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle(grant: Grant) {
    setPending(grant.key);
    setError(null);
    try {
      const body = await api.setGrant(grant.key, !grant.granted, orgId);
      onChanged(body.grants);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(null);
    }
  }

  return (
    <section aria-labelledby="authority-heading">
      <div className="mb-3">
        <h2 id="authority-heading" className="text-lg font-bold tracking-tight">
          What Zamu may do
        </h2>
        <p className="text-sm text-muted-foreground">
          Each level is granted separately. Granting one never grants the next. These
          checks run in code before any tool call, so turning one off does not ask Zamu
          to behave — it makes the action unreachable.
        </p>
      </div>

      {error ? (
        <p className="mb-3 rounded-lg bg-uncovered-soft px-3 py-2 text-sm text-uncovered">
          {error}
        </p>
      ) : null}

      <ol className="space-y-2">
        {grants.map((grant) => (
          <Card
            as="li"
            key={grant.key}
            className={`p-4 ${grant.forbidden ? "opacity-70" : ""}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="grid size-6 shrink-0 place-items-center rounded-full bg-muted font-mono text-xs text-muted-foreground">
                    {grant.level}
                  </span>
                  {/* Sentence case, not `capitalize`: title-casing turns "draft an ask" into
                      "Draft An Ask". */}
                  <h3 className="font-bold first-letter:uppercase">{grant.label}</h3>
                  {grant.forbidden ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-bold text-muted-foreground">
                      <LockIcon className="size-3" />
                      never
                    </span>
                  ) : null}
                </div>
                <p className="mt-1.5 text-sm text-muted-foreground">{grant.description}</p>
                {grant.granted_by ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    granted by {grant.granted_by}
                  </p>
                ) : null}
              </div>

              <div className="shrink-0">
                {grant.changeable ? (
                  <button
                    type="button"
                    role="switch"
                    aria-checked={grant.granted}
                    aria-label={`${grant.granted ? "Revoke" : "Grant"} permission to ${grant.label}`}
                    onClick={() => toggle(grant)}
                    disabled={pending === grant.key}
                    className={`inline-flex min-h-11 min-w-11 cursor-pointer items-center gap-2 rounded-lg border-2 px-3 py-2 text-sm font-bold transition-colors duration-200 disabled:cursor-not-allowed ${
                      grant.granted
                        ? "border-accent bg-accent text-on-accent"
                        : "border-border bg-transparent text-muted-foreground hover:border-primary hover:text-foreground"
                    }`}
                  >
                    {pending === grant.key ? (
                      <SpinnerIcon />
                    ) : grant.granted ? (
                      <CheckIcon />
                    ) : null}
                    {grant.granted ? "Granted" : "Off"}
                  </button>
                ) : (
                  <span className="inline-flex min-h-11 items-center rounded-lg border-2 border-transparent px-3 py-2 text-sm font-bold text-muted-foreground">
                    {grant.forbidden ? "Not implemented" : "Always on"}
                  </span>
                )}
              </div>
            </div>
          </Card>
        ))}
      </ol>
    </section>
  );
}
