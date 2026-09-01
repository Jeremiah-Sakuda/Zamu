"use client";

import { useEffect, useRef, useState } from "react";
import { api, type AskResult, type Duty } from "@/lib/api";
import { Button } from "./ui";

/**
 * Recording that somebody dropped out.
 *
 * Asks for what the person actually said, and requires it, because the ledger entry
 * is only worth anything if it carries the evidence. "Priya dropped out" with nothing
 * behind it is a rumour that has been written to a database.
 */
export function WithdrawDialog({
  duty,
  onClose,
  onDone,
}: {
  duty: Duty;
  onClose: () => void;
  onDone: (result: AskResult) => void;
}) {
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit() {
    if (!duty.assigned || !evidence.trim()) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.withdraw(duty.id, duty.assigned.id, evidence.trim()));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="withdraw-heading"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="zamu-enter w-full max-w-lg rounded-[16px] border border-border bg-card p-6 text-card-foreground">
        <h2 id="withdraw-heading" className="text-lg font-bold tracking-tight">
          {duty.assigned?.name} dropped out
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {duty.title} · {duty.when}
        </p>

        <label htmlFor="evidence" className="mt-4 block text-sm font-bold">
          What did they say?
        </label>
        <p className="text-sm text-muted-foreground">
          Recorded on the receipt. Zamu will not take somebody off a shift without it.
        </p>
        <textarea
          id="evidence"
          ref={inputRef}
          rows={3}
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          placeholder="Sorry, my shift at work got moved — I can't make Thursday."
          className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-base"
        />

        {error ? (
          <p className="mt-3 rounded-lg bg-uncovered-soft px-3 py-2 text-sm text-uncovered">
            {error}
          </p>
        ) : null}

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} busy={busy} disabled={!evidence.trim()}>
            Take them off and find cover
          </Button>
        </div>
      </div>
    </div>
  );
}
