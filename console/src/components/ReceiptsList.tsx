"use client";

import type { Receipt } from "@/lib/api";
import { Card, Empty, Rule } from "./ui";

/**
 * The ledger.
 *
 * Intended and observed sit next to each other because that adjacency *is* the
 * receipt. An entry that only records what was attempted is a log; one that records
 * what was found on re-reading the target is evidence.
 */
const RESULT_STYLES: Record<Receipt["result"], { label: string; className: string }> = {
  verified: { label: "Verified", className: "bg-covered-soft text-covered" },
  blocked: { label: "Blocked", className: "bg-at-risk-soft text-at-risk" },
  failed: { label: "Failed", className: "bg-uncovered-soft text-uncovered" },
  conflicted: { label: "Conflicted", className: "bg-uncovered-soft text-uncovered" },
  reversed: { label: "Reversed", className: "bg-muted text-muted-foreground" },
  in_progress: { label: "In progress", className: "bg-muted text-muted-foreground" },
};

export function ReceiptsList({ receipts }: { receipts: Receipt[] }) {
  return (
    <section aria-labelledby="receipts-heading">
      <div className="mb-3">
        <h2 id="receipts-heading" className="text-lg font-bold tracking-tight">
          Receipts
        </h2>
        <p className="text-sm text-muted-foreground">
          Everything Zamu did, tried, or was refused. Each entry records what it meant to
          happen, what was actually there when the target was re-read, and the rule that
          permitted or refused it.
        </p>
      </div>

      {receipts.length === 0 ? (
        <Empty>Nothing yet. Zamu has not acted on this roster.</Empty>
      ) : (
        <ol className="space-y-2">
          {receipts.map((receipt) => {
            const style = RESULT_STYLES[receipt.result];
            return (
              <Card as="li" key={receipt.id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${style.className}`}
                      >
                        {style.label}
                      </span>
                      <h3 className="font-bold">{receipt.summary}</h3>
                    </div>
                    {receipt.detail ? (
                      <p className="mt-1 text-sm text-muted-foreground">{receipt.detail}</p>
                    ) : null}
                  </div>
                  <time
                    className="shrink-0 font-mono text-xs text-muted-foreground"
                    dateTime={receipt.at}
                  >
                    {new Date(receipt.at).toLocaleString("en-GB", {
                      day: "2-digit",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Rule rule={receipt.policy_rule} />
                  <span className="text-xs text-muted-foreground">{receipt.action}</span>
                </div>

                {receipt.observed || Object.keys(receipt.intended).length ? (
                  <div className="scroll-x mt-3 rounded-lg bg-muted p-3">
                    <dl className="grid min-w-[22rem] grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px]">
                      <dt className="font-bold text-muted-foreground">intended</dt>
                      <dt className="font-bold text-muted-foreground">observed on re-read</dt>
                      <dd className="whitespace-pre-wrap break-words">
                        {format(receipt.intended)}
                      </dd>
                      <dd className="whitespace-pre-wrap break-words">
                        {receipt.observed ? format(receipt.observed) : "— nothing read back —"}
                      </dd>
                    </dl>
                  </div>
                ) : null}
              </Card>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function format(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload);
  if (!entries.length) return "—";
  return entries.map(([k, v]) => `${k}: ${v === null ? "none" : String(v)}`).join("\n");
}
