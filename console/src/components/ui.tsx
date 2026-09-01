/**
 * The console's primitives.
 *
 * Hand-built rather than pulled from a component library so that every one of them
 * answers to `design-system/zamu/MASTER.md` directly: 44px minimum targets, visible
 * focus rings, 4.5:1 contrast, transitions in the 150–300ms band, and no emoji
 * standing in for an icon.
 */

import type { ReactNode } from "react";
import type { CoverageState } from "@/lib/api";

/* ------------------------------------------------------------------ icons */

/* SVG only. The design system names emoji-as-icons as an anti-pattern, and it is
   right to: emoji render differently on every platform and are read aloud by screen
   readers as whatever the vendor called them. */

type IconProps = { className?: string };

export function CheckIcon({ className = "size-4" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden className={className}>
      <path
        d="M4 10.5 8 14.5 16 5.5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function AlertIcon({ className = "size-4" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden className={className}>
      <path
        d="M10 3.2 18 16.8H2L10 3.2Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M10 8v3.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="10" cy="14" r="1" fill="currentColor" />
    </svg>
  );
}

export function EmptyIcon({ className = "size-4" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden className={className}>
      <circle cx="10" cy="10" r="7.2" stroke="currentColor" strokeWidth="1.8" />
      <path d="M5.2 14.8 14.8 5.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export function QuestionIcon({ className = "size-4" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden className={className}>
      <circle cx="10" cy="10" r="7.2" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M7.9 7.8a2.1 2.1 0 1 1 2.6 2.1c-.5.2-.6.6-.6 1.1"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="10" cy="14" r="1" fill="currentColor" />
    </svg>
  );
}

export function LockIcon({ className = "size-4" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden className={className}>
      <rect x="4" y="8.6" width="12" height="8" rx="2" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M7 8.6V6.4a3 3 0 0 1 6 0v2.2"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function ArrowIcon({ className = "size-4" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden className={className}>
      <path
        d="M4 10h12m0 0-4.4-4.4M16 10l-4.4 4.4"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function SpinnerIcon({ className = "size-4" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden className={`${className} animate-spin`}>
      <circle cx="10" cy="10" r="7.5" stroke="currentColor" strokeWidth="2" opacity="0.25" />
      <path
        d="M17.5 10A7.5 7.5 0 0 0 10 2.5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* ------------------------------------------------------------------ layout */

export function Card({
  children,
  className = "",
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "section" | "li" | "article";
}) {
  return (
    <Tag
      className={`rounded-[12px] border border-border bg-card text-card-foreground ${className}`}
    >
      {children}
    </Tag>
  );
}

export function SectionHeading({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 className="text-lg font-bold tracking-tight">{title}</h2>
        {hint ? <p className="text-sm text-muted-foreground">{hint}</p> : null}
      </div>
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------ button */

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "quiet" | "danger";
  busy?: boolean;
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
  className?: string;
};

const BUTTON_STYLES: Record<NonNullable<ButtonProps["variant"]>, string> = {
  primary:
    "bg-accent text-on-accent border-accent hover:brightness-110 disabled:hover:brightness-100",
  secondary:
    "bg-transparent text-foreground border-primary hover:bg-primary hover:text-on-primary",
  quiet:
    "bg-transparent text-muted-foreground border-transparent hover:bg-muted hover:text-foreground",
  danger:
    "bg-transparent text-destructive border-destructive hover:bg-destructive hover:text-on-destructive",
};

export function Button({
  children,
  onClick,
  variant = "primary",
  busy = false,
  disabled = false,
  title,
  type = "button",
  className = "",
}: ButtonProps) {
  return (
    <button
      type={type}
      onClick={onClick}
      title={title}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-lg border-2 px-4 py-2 text-sm font-bold transition-[background-color,color,filter] duration-200 disabled:cursor-not-allowed disabled:opacity-55 ${BUTTON_STYLES[variant]} ${className}`}
    >
      {busy ? <SpinnerIcon /> : null}
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ state */

/* Colour is never the only signal. Every state carries an icon and a word, which is
   the design system's highest-severity accessibility rule. */
export const STATE_META: Record<
  CoverageState,
  { label: string; icon: (p: IconProps) => ReactNode; fg: string; bg: string; dot: string }
> = {
  covered: {
    label: "Covered",
    icon: CheckIcon,
    fg: "text-covered",
    bg: "bg-covered-soft",
    dot: "bg-covered",
  },
  at_risk: {
    label: "At risk",
    icon: AlertIcon,
    fg: "text-at-risk",
    bg: "bg-at-risk-soft",
    dot: "bg-at-risk",
  },
  uncovered: {
    label: "Uncovered",
    icon: EmptyIcon,
    fg: "text-uncovered",
    bg: "bg-uncovered-soft",
    dot: "bg-uncovered",
  },
  unknown: {
    label: "Unconfirmed",
    icon: QuestionIcon,
    fg: "text-unknown",
    bg: "bg-unknown-soft",
    dot: "bg-unknown",
  },
};

export function StateBadge({ state, className = "" }: { state: CoverageState; className?: string }) {
  const meta = STATE_META[state];
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-bold ${meta.bg} ${meta.fg} ${className}`}
    >
      <Icon className="size-3.5" />
      {meta.label}
    </span>
  );
}

/* ------------------------------------------------------------------ misc */

export function Meter({
  value,
  label,
  tone = "accent",
}: {
  value: number;
  label: string;
  tone?: "accent" | "muted";
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 shrink-0 text-xs text-muted-foreground sm:w-28">{label}</span>
      <span
        className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={`${label}: ${Math.round(pct)} out of 100`}
      >
        <span
          className={`block h-full rounded-full ${tone === "accent" ? "bg-accent" : "bg-muted-foreground"}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="w-9 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

export function Rule({ rule }: { rule: string }) {
  if (!rule) return null;
  return (
    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
      {rule}
    </code>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-[12px] border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
      {children}
    </p>
  );
}
