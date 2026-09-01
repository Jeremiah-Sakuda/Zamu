"use client";

import type { Brief } from "@/lib/api";
import { AlertIcon, Card, CheckIcon, LockIcon, Rule } from "./ui";

/**
 * The handover brief.
 *
 * Ordered the way a coordinator's attention actually runs: what needs me, what were
 * you not allowed to do, what did you do, what are you still waiting on. When nothing
 * needs them it says so in one line and stops — no filler, no score, no streak.
 */
export function BriefCard({ brief }: { brief: Brief }) {
  const quiet =
    !brief.needs_decision.length &&
    !brief.not_allowed.length &&
    !brief.filled.length &&
    !brief.waiting.length;

  return (
    <Card as="section" className="p-5" aria-labelledby="brief-heading">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 id="brief-heading" className="text-lg font-bold tracking-tight">
          Handover
        </h2>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-bold ${
            brief.needs_human
              ? "bg-at-risk-soft text-at-risk"
              : "bg-covered-soft text-covered"
          }`}
        >
          {brief.needs_human ? "Needs you" : "Nothing needed"}
        </span>
      </div>

      {quiet ? (
        <p className="text-sm text-muted-foreground">
          Nothing has needed you since the last brief. Coverage is holding.
        </p>
      ) : null}

      <Group
        title="Needs you"
        items={brief.needs_decision}
        icon={<AlertIcon className="size-4 text-at-risk" />}
      />
      <Group
        title="Zamu was not allowed to"
        items={brief.not_allowed}
        icon={<LockIcon className="size-4 text-muted-foreground" />}
        showRule
      />
      <Group
        title="Filled"
        items={brief.filled}
        icon={<CheckIcon className="size-4 text-covered" />}
      />
      <Group title="Waiting on an answer" items={brief.waiting} />

      {brief.fairness_note ? (
        <p className="mt-4 border-t border-border pt-4 text-sm text-muted-foreground">
          {brief.fairness_note}
        </p>
      ) : null}
    </Card>
  );
}

function Group({
  title,
  items,
  icon,
  showRule = false,
}: {
  title: string;
  items: Brief["filled"];
  icon?: React.ReactNode;
  showRule?: boolean;
}) {
  if (!items.length) return null;
  return (
    <div className="mb-4 last:mb-0">
      <h3 className="mb-2 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-muted-foreground">
        {icon}
        {title}
      </h3>
      <ul className="space-y-2.5">
        {items.map((item, i) => (
          <li key={`${item.headline}-${i}`} className="text-sm">
            <p className="font-bold">{item.headline}</p>
            {item.detail ? (
              <p className="text-muted-foreground">{item.detail}</p>
            ) : null}
            {showRule ? (
              <p className="mt-1">
                <Rule rule={item.policy_rule} />
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
