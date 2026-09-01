"use client";

import type { Org, Person } from "@/lib/api";
import { Card, LockIcon } from "./ui";

/**
 * The fairness ledger, which is also the people list, because they are the same thing.
 *
 * Load is shown as a bar against the heaviest carrier rather than as a raw number:
 * the point a coordinator needs to see in one glance is the *shape* of the
 * distribution, not anybody's total.
 */
export function PeopleLedger({ people, org }: { people: Person[]; org: Org }) {
  const heaviest = Math.max(1, ...people.map((p) => p.weighted_load));

  return (
    <section aria-labelledby="people-heading">
      <div className="mb-3">
        <h2 id="people-heading" className="text-lg font-bold tracking-tight">
          Who is carrying what
        </h2>
        <p className="text-sm text-muted-foreground">
          Hours over the last {org.fairness_window_weeks} weeks, with unsociable hours
          counted at a premium. Nobody is asked more than {org.max_asks_per_person_per_week}{" "}
          times a week.
        </p>
      </div>

      <ul className="space-y-2">
        {people.map((person) => (
          <Card as="li" key={person.id} className="p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="font-bold">
                {person.name}
                {!person.active ? (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">inactive</span>
                ) : null}
              </h3>
              <span className="font-mono text-sm tabular-nums text-muted-foreground">
                {person.hours_carried.toFixed(1)}h · {person.shifts_carried} shifts
              </span>
            </div>

            <div
              className="mt-2 h-2 overflow-hidden rounded-full bg-muted"
              role="img"
              aria-label={`${person.name}: ${person.summary}`}
            >
              <span
                className="block h-full rounded-full bg-accent"
                style={{ width: `${(person.weighted_load / heaviest) * 100}%` }}
              />
            </div>

            <p className="mt-2 text-sm text-muted-foreground">{person.summary}</p>

            <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <li>
                {person.asks_received} asked · {person.accepts} yes · {person.declines} no
              </li>
              {person.qualifications.length ? (
                <li>trained: {person.qualifications.join(", ")}</li>
              ) : (
                <li>no qualifications recorded</li>
              )}
              {person.quiet_hours ? <li>quiet {person.quiet_hours}</li> : null}
              {!person.opted_in ? (
                <li className="inline-flex items-center gap-1 font-bold text-at-risk">
                  <LockIcon className="size-3.5" />
                  Zamu may not contact directly
                </li>
              ) : null}
            </ul>
          </Card>
        ))}
      </ul>
    </section>
  );
}
