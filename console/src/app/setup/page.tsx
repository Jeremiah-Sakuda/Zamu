"use client";

import { useState } from "react";
import Link from "next/link";
import { api, type ImportReport, type Org } from "@/lib/api";
import { AlertIcon, ArrowIcon, Button, Card, CheckIcon } from "@/components/ui";

/**
 * Setting up a real roster.
 *
 * Three steps, and the whole screen is designed around one fact: the person doing this
 * did not ask for software, is busy, and will abandon anything they cannot finish in
 * one sitting. So it takes the spreadsheet they already have, imports around the bad
 * rows, and names each one by line number rather than rejecting the file.
 *
 * The two notes about consent and confirmation are on the screen rather than in the
 * documentation because they are the two things a coordinator would otherwise assume
 * wrongly, and both matter the moment Zamu starts acting.
 */

const TIMEZONES = [
  "America/Chicago",
  "America/New_York",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Africa/Nairobi",
  "Asia/Kolkata",
  "Australia/Sydney",
  "UTC",
];

const PEOPLE_EXAMPLE = `Name,Email,Skills,Consent
Amara Okonkwo,amara@example.org,"food-safety, forklift",yes
Marcus Tran,marcus@example.org,food-safety,yes`;

const DUTIES_EXAMPLE = `Shift,Start,End,Role,Requires,Assigned To
Evening distribution,2026-10-01 18:00,2026-10-01 20:00,Distribution,food-safety,Amara Okonkwo
Saturday intake,2026-10-03 08:00,2026-10-03 13:00,Intake,food-safety,`;

export default function SetupPage() {
  const [org, setOrg] = useState<Org | null>(null);
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("America/Chicago");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setOrg(await api.createOrg(name.trim(), timezone));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="mx-auto max-w-[46rem] px-5 py-10">
      <p className="text-xs font-bold uppercase tracking-[0.18em] text-muted-foreground">
        Zamu
      </p>
      <h1 className="mt-1 text-2xl font-bold tracking-tight">Set up a roster</h1>
      <p className="mt-1 text-muted-foreground">
        Three steps, using the spreadsheet you already have. You can stop after any of
        them and come back.
      </p>

      {error ? (
        <p className="mt-4 rounded-lg bg-uncovered-soft px-3 py-2 text-sm text-uncovered">
          {error}
        </p>
      ) : null}

      <Step number={1} title="Name the organization" done={Boolean(org)}>
        {org ? (
          <p className="text-sm text-muted-foreground">
            {org.name} · {org.timezone.replace("_", " ")}
          </p>
        ) : (
          <>
            <label htmlFor="org-name" className="block text-sm font-bold">
              What is it called?
            </label>
            <input
              id="org-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Riverside Community Food Bank"
              className="mt-1.5 w-full rounded-lg border border-border bg-card px-3 py-2 text-base"
            />

            <label htmlFor="org-tz" className="mt-4 block text-sm font-bold">
              Which timezone do your shifts run in?
            </label>
            <p className="text-sm text-muted-foreground">
              So that &ldquo;6pm&rdquo; means six in the evening where you are, and quiet
              hours land at night.
            </p>
            <select
              id="org-tz"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-border bg-card px-3 py-2 text-base"
            >
              {TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz.replace("_", " ")}
                </option>
              ))}
            </select>

            <div className="mt-4">
              <Button onClick={create} busy={busy} disabled={!name.trim()}>
                Create it
              </Button>
            </div>
          </>
        )}
      </Step>

      <Step number={2} title="Add your volunteers" disabled={!org}>
        <ImportBox
          orgId={org?.id ?? ""}
          what="people"
          example={PEOPLE_EXAMPLE}
          hint="Paste the CSV export of whatever list you keep. Zamu matches column names loosely — Name, Full Name, Volunteer, and E-mail all work."
          footnote="Nobody is opted in to being messaged by Zamu unless your file says so. That consent belongs to them, not to the spreadsheet — you can grant it per person later."
        />
      </Step>

      <Step number={3} title="Add your shifts" disabled={!org}>
        <ImportBox
          orgId={org?.id ?? ""}
          what="duties"
          example={DUTIES_EXAMPLE}
          hint="One row per shift. If there is no end time, Zamu assumes two hours."
          footnote="Anybody already listed against a shift is imported as assigned but unconfirmed. Nobody has confirmed anything to Zamu yet, and treating a spreadsheet row as a kept promise is how coverage software starts being wrong."
        />
      </Step>

      {org ? (
        <Card className="mt-8 p-5">
          <h2 className="font-bold">That is the setup done.</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Zamu can now read this roster and draft asks for you. It cannot message
            anybody or change the roster until you grant those separately, on the
            Authority screen.
          </p>
          <p className="mt-4">
            <Link
              className="inline-flex min-h-11 items-center gap-2 rounded-lg border-2 border-accent bg-accent px-4 py-2 text-sm font-bold text-on-accent"
              href={`/?org=${org.id}`}
            >
              Open the console
              <ArrowIcon />
            </Link>
          </p>
        </Card>
      ) : null}
    </main>
  );
}

function Step({
  number,
  title,
  children,
  done = false,
  disabled = false,
}: {
  number: number;
  title: string;
  children: React.ReactNode;
  done?: boolean;
  disabled?: boolean;
}) {
  return (
    <Card as="section" className={`mt-6 p-5 ${disabled ? "opacity-50" : ""}`}>
      <div className="mb-3 flex items-center gap-2">
        <span
          className={`grid size-7 shrink-0 place-items-center rounded-full text-sm font-bold ${
            done ? "bg-covered-soft text-covered" : "bg-muted text-muted-foreground"
          }`}
        >
          {done ? <CheckIcon className="size-4" /> : number}
        </span>
        <h2 className="text-lg font-bold tracking-tight">{title}</h2>
      </div>
      <fieldset disabled={disabled} className="min-w-0">
        {children}
      </fieldset>
    </Card>
  );
}

function ImportBox({
  orgId,
  what,
  example,
  hint,
  footnote,
}: {
  orgId: string;
  what: "people" | "duties";
  example: string;
  hint: string;
  footnote: string;
}) {
  const [csv, setCsv] = useState("");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go(dryRun: boolean) {
    if (!csv.trim() || !orgId) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await api.importCsv(orgId, what, csv, dryRun));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="text-sm text-muted-foreground">{hint}</p>
      <label htmlFor={`csv-${what}`} className="sr-only">
        Paste your {what} CSV
      </label>
      <textarea
        id={`csv-${what}`}
        value={csv}
        onChange={(e) => setCsv(e.target.value)}
        rows={6}
        spellCheck={false}
        placeholder={example}
        className="mt-2 w-full rounded-lg border border-border bg-card px-3 py-2 font-mono text-xs"
      />

      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={() => go(true)} variant="secondary" busy={busy} disabled={!csv.trim()}>
          Check it first
        </Button>
        <Button onClick={() => go(false)} busy={busy} disabled={!csv.trim()}>
          Import
        </Button>
      </div>

      {error ? (
        <p className="mt-3 rounded-lg bg-uncovered-soft px-3 py-2 text-sm text-uncovered">
          {error}
        </p>
      ) : null}

      {report ? (
        <div className="zamu-enter mt-4 rounded-lg bg-muted p-4" role="status">
          <p className="text-sm font-bold">
            {report.summary}
            {report.dry_run ? " Nothing was written." : ""}
          </p>

          {report.problems.length ? (
            <>
              <h3 className="mt-3 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-at-risk">
                <AlertIcon className="size-3.5" />
                Could not read
              </h3>
              <ul className="mt-1.5 space-y-1">
                {report.problems.map((problem, i) => (
                  <li key={i} className="text-sm text-muted-foreground">
                    {problem}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-sm text-muted-foreground">
                Everything else was imported. Fix these rows and paste them again.
              </p>
            </>
          ) : null}
        </div>
      ) : null}

      <p className="mt-3 border-t border-border pt-3 text-sm text-muted-foreground">
        {footnote}
      </p>
    </>
  );
}
