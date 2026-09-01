"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  type AskResult,
  type Console,
  type Duty,
  type OutboxMessage,
  type Receipt,
} from "@/lib/api";
import { AgentPanel } from "@/components/AgentPanel";
import { AuthorityLadder } from "@/components/AuthorityLadder";
import { BriefCard } from "@/components/BriefCard";
import { CandidatePanel, OutcomeBanner } from "@/components/CandidatePanel";
import { CoverageBoard } from "@/components/CoverageBoard";
import { OutboxPanel } from "@/components/OutboxPanel";
import { PeopleLedger } from "@/components/PeopleLedger";
import { ReceiptsList } from "@/components/ReceiptsList";
import { WithdrawDialog } from "@/components/WithdrawDialog";
import { Button, Card, SpinnerIcon, STATE_META } from "@/components/ui";

/**
 * The coordinator's console.
 *
 * One screen answers the only question they open this for: is anything uncovered, and
 * does anything need me? Everything else — the ledger, the fairness ledger, the trust
 * ladder — is a tab away, present for when trust needs rebuilding rather than
 * competing for attention every morning.
 */

const TABS = [
  { key: "coverage", label: "Coverage" },
  { key: "people", label: "Fairness" },
  { key: "authority", label: "Authority" },
  { key: "receipts", label: "Receipts" },
  { key: "outbox", label: "Sent" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function ConsolePage() {
  const [data, setData] = useState<Console | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [outbox, setOutbox] = useState<OutboxMessage[]>([]);
  const [tab, setTab] = useState<TabKey>("coverage");
  const [selected, setSelected] = useState<string | null>(null);
  const [withdrawing, setWithdrawing] = useState<Duty | null>(null);
  const [outcome, setOutcome] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [consoleData, receiptData, outboxData] = await Promise.all([
        api.console(),
        api.receipts(),
        api.outbox(),
      ]);
      setData(consoleData);
      setReceipts(receiptData);
      setOutbox(outboxData);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    try {
      await work();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !data) {
    return <Unreachable message={error} onRetry={refresh} />;
  }

  if (!data) {
    return (
      <main id="main" className="grid min-h-dvh place-items-center p-8">
        <p className="flex items-center gap-2 text-muted-foreground">
          <SpinnerIcon /> Reading the roster…
        </p>
      </main>
    );
  }

  const { org, summary, duties, people, grants, brief } = data;

  return (
    <>
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-end justify-between gap-4 px-5 pb-4 pt-6">
          <div className="min-w-0">
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-muted-foreground">
              Zamu
            </p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight">{org.name}</h1>
            <p className="text-sm text-muted-foreground">
              {org.local_time} · {org.timezone.replace("_", " ")}
              {org.demo ? " · demonstration roster" : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={() => act(() => api.sweep())} busy={busy}>
              Run a sweep
            </Button>
            {org.demo ? (
              <Button
                variant="quiet"
                onClick={() =>
                  act(async () => {
                    await api.resetDemo();
                    setSelected(null);
                    setOutcome(null);
                  })
                }
              >
                Reset sandbox
              </Button>
            ) : null}
          </div>
        </div>

        <div className="mx-auto max-w-[1200px] px-5">
          <ul className="scroll-x flex gap-2 pb-3">
            {(["uncovered", "at_risk", "unknown", "covered"] as const).map((state) => {
              const meta = STATE_META[state];
              const Icon = meta.icon;
              const count = summary[state];
              return (
                <li
                  key={state}
                  className={`inline-flex shrink-0 items-center gap-2 rounded-full px-3 py-1.5 text-sm font-bold ${
                    count ? `${meta.bg} ${meta.fg}` : "bg-muted text-muted-foreground"
                  }`}
                >
                  <Icon className="size-4" />
                  {count} {meta.label.toLowerCase()}
                </li>
              );
            })}
            {summary.open_asks ? (
              <li className="inline-flex shrink-0 items-center gap-2 rounded-full bg-muted px-3 py-1.5 text-sm font-bold text-muted-foreground">
                {summary.open_asks} awaiting an answer
              </li>
            ) : null}
          </ul>
        </div>

        <nav aria-label="Console sections" className="mx-auto max-w-[1200px] px-5">
          <ul className="scroll-x -mb-px flex gap-1">
            {TABS.map((t) => (
              <li key={t.key}>
                <button
                  type="button"
                  onClick={() => setTab(t.key)}
                  aria-current={tab === t.key ? "page" : undefined}
                  className={`inline-flex min-h-11 cursor-pointer items-center border-b-[3px] px-3 text-sm font-bold transition-colors duration-200 ${
                    tab === t.key
                      ? "border-accent text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main id="main" className="mx-auto max-w-[1200px] px-5 py-6">
        {error ? (
          <p className="mb-4 rounded-lg bg-uncovered-soft px-3 py-2 text-sm text-uncovered">
            {error}
          </p>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="min-w-0 space-y-5">
            {outcome ? (
              <OutcomeBanner result={outcome} onDismiss={() => setOutcome(null)} />
            ) : null}

            {selected ? (
              <CandidatePanel
                dutyId={selected}
                onClose={() => setSelected(null)}
                onActed={(result) => {
                  setOutcome(result);
                  setSelected(null);
                  void refresh();
                }}
              />
            ) : null}

            {tab === "coverage" ? (
              <CoverageBoard
                duties={duties}
                selectedDutyId={selected}
                busyDutyId={busy ? selected : null}
                onSelect={(id) => setSelected(id === selected ? null : id)}
                onWithdraw={setWithdrawing}
              />
            ) : null}
            {tab === "people" ? <PeopleLedger people={people} org={org} /> : null}
            {tab === "authority" ? (
              <AuthorityLadder
                grants={grants}
                onChanged={(next) => {
                  setData({ ...data, grants: next });
                  void refresh();
                }}
              />
            ) : null}
            {tab === "receipts" ? <ReceiptsList receipts={receipts} /> : null}
            {tab === "outbox" ? <OutboxPanel messages={outbox} /> : null}
          </div>

          <aside className="space-y-5 lg:sticky lg:top-6 lg:self-start">
            <BriefCard brief={brief} />
            <AgentPanel onFinished={refresh} />
          </aside>
        </div>
      </main>

      {withdrawing ? (
        <WithdrawDialog
          duty={withdrawing}
          onClose={() => setWithdrawing(null)}
          onDone={(result) => {
            setWithdrawing(null);
            setOutcome(result);
            setSelected(result.duty_id);
            void refresh();
          }}
        />
      ) : null}
    </>
  );
}

function Unreachable({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <main id="main" className="mx-auto grid min-h-dvh max-w-lg place-items-center p-6">
      <Card className="p-6">
        <h1 className="text-lg font-bold tracking-tight">Zamu&rsquo;s API is not answering</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The console reads everything from the Zamu service. Start it with{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            uvicorn zamu.api.app:app
          </code>{" "}
          , or point <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            NEXT_PUBLIC_ZAMU_API
          </code>{" "}
          at a running one.
        </p>
        <p className="mt-3 rounded-lg bg-muted px-3 py-2 font-mono text-xs">{message}</p>
        <div className="mt-4">
          <Button onClick={onRetry}>Try again</Button>
        </div>
      </Card>
    </main>
  );
}
