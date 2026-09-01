"use client";

import type { Duty } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { ArrowIcon, Button, Card, Empty, StateBadge } from "./ui";

/**
 * The roster.
 *
 * Two lists rather than one table: what needs attention, then everything else. A
 * coordinator opening this at 8am wants the first list; the second is reassurance,
 * and reassurance should not be the thing you have to scroll past.
 *
 * Nothing here renders `unknown` as covered. An unconfirmed assignment gets its own
 * badge and its own sentence, because a duty somebody accepted three weeks ago and
 * has not mentioned since is genuinely not the same as one confirmed this morning.
 */
export function CoverageBoard({
  duties,
  selectedDutyId,
  onSelect,
  onWithdraw,
  busyDutyId,
}: {
  duties: Duty[];
  selectedDutyId: string | null;
  onSelect: (dutyId: string) => void;
  onWithdraw: (duty: Duty) => void;
  busyDutyId: string | null;
}) {
  const upcoming = duties.filter((d) => !d.is_past);
  const attention = upcoming.filter((d) => d.state !== "covered");
  const holding = upcoming.filter((d) => d.state === "covered");

  return (
    <div className="space-y-8">
      <section aria-labelledby="attention-heading">
        <div className="mb-3">
          <h2 id="attention-heading" className="text-lg font-bold tracking-tight">
            Needs attention
          </h2>
          <p className="text-sm text-muted-foreground">
            Uncovered, at risk, or accepted but never confirmed.
          </p>
        </div>

        {attention.length === 0 ? (
          <Empty>Every upcoming duty is covered and confirmed.</Empty>
        ) : (
          <ul className="space-y-3">
            {attention.map((duty) => (
              <DutyRow
                key={duty.id}
                duty={duty}
                selected={duty.id === selectedDutyId}
                busy={duty.id === busyDutyId}
                onSelect={onSelect}
                onWithdraw={onWithdraw}
                prominent
              />
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="holding-heading">
        <div className="mb-3">
          <h2 id="holding-heading" className="text-lg font-bold tracking-tight">
            Holding
          </h2>
          <p className="text-sm text-muted-foreground">
            {holding.length} upcoming {holding.length === 1 ? "duty is" : "duties are"} covered
            and confirmed.
          </p>
        </div>
        {holding.length === 0 ? (
          <Empty>Nothing upcoming is confirmed yet.</Empty>
        ) : (
          <ul className="space-y-2">
            {holding.map((duty) => (
              <DutyRow
                key={duty.id}
                duty={duty}
                selected={duty.id === selectedDutyId}
                busy={duty.id === busyDutyId}
                onSelect={onSelect}
                onWithdraw={onWithdraw}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DutyRow({
  duty,
  selected,
  busy,
  onSelect,
  onWithdraw,
  prominent = false,
}: {
  duty: Duty;
  selected: boolean;
  busy: boolean;
  onSelect: (id: string) => void;
  onWithdraw: (duty: Duty) => void;
  prominent?: boolean;
}) {
  return (
    <Card
      as="li"
      className={`p-4 transition-colors duration-200 ${
        selected ? "border-accent" : ""
      } ${prominent ? "" : "bg-card/60"}`}
    >
      {/* Stacked on a phone: side-by-side, the action column squeezes the shift
          details into a two-word-wide strip. */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <StateBadge state={duty.state} />
            <h3 className="font-bold">{duty.title}</h3>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {duty.when} · {duty.notice} · {duty.role}
            {duty.required_qualification ? ` · needs ${duty.required_qualification}` : ""}
          </p>
          <p className="mt-1 text-sm">{duty.reason}</p>

          {duty.pending_ask ? (
            <p className="mt-2 rounded-lg bg-muted px-3 py-2 text-sm">
              <span className="font-bold">
                {duty.pending_ask.drafted_only ? "Draft ready for" : "Waiting on"}{" "}
                {duty.pending_ask.person}
              </span>
              {duty.pending_ask.drafted_only ? null : (
                <>
                  {" · moves on "}
                  {relativeTime(duty.pending_ask.expires_at)}
                </>
              )}
              {duty.pending_ask.rationale ? (
                <span className="mt-1 block text-muted-foreground">
                  {duty.pending_ask.rationale}
                </span>
              ) : null}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-wrap items-stretch gap-2 sm:flex-col">
          {duty.needs_filling || duty.state === "at_risk" ? (
            <Button
              variant={prominent ? "primary" : "secondary"}
              onClick={() => onSelect(duty.id)}
              busy={busy}
            >
              {selected ? "Showing" : "Who to ask"}
              <ArrowIcon />
            </Button>
          ) : null}
          {duty.assigned ? (
            <Button
              variant="quiet"
              onClick={() => onWithdraw(duty)}
              title={`Record that ${duty.assigned.name} can no longer do this shift`}
            >
              Mark as dropped out
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
