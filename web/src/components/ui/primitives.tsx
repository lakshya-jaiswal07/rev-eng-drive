import * as React from "react";
import { cn } from "~/lib/utils";

/* ---------------------------------------------------------------- Button */

type ButtonVariant = "primary" | "ghost" | "outline" | "danger" | "soft";
type ButtonSize = "sm" | "md" | "lg" | "icon";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-foreground hover:brightness-110 active:brightness-95 shadow-[var(--shadow-soft)]",
  soft: "bg-elevated text-foreground hover:bg-line",
  ghost: "text-muted hover:text-foreground hover:bg-subtle",
  outline: "border border-line/80 bg-transparent text-foreground hover:border-line hover:bg-subtle",
  danger: "bg-danger text-white hover:brightness-110",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-10 px-4 text-sm gap-2",
  lg: "h-12 px-6 text-sm gap-2",
  icon: "h-9 w-9",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "soft", size = "md", ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "inline-flex select-none items-center justify-center rounded-[var(--radius-pill)] font-medium",
        "transition-[background-color,color,filter,transform] duration-150 active:scale-[0.98]",
        "disabled:pointer-events-none disabled:opacity-40",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = "Button";

/* ----------------------------------------------------------------- Panel */

export function Panel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-col rounded-[var(--radius-panel)] border border-line/70 bg-panel/85",
        "shadow-[var(--shadow-lift)] backdrop-blur-xl",
        className,
      )}
      {...props}
    />
  );
}

export function SectionLabel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("eyebrow px-1", className)}
      {...props}
    />
  );
}

/* ----------------------------------------------------------------- Badge */

type Tone = "neutral" | "accent" | "live" | "warn" | "info" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "bg-subtle text-muted",
  accent: "bg-accent-soft text-accent",
  live: "bg-live-soft text-live",
  warn: "bg-warn-soft text-warn",
  info: "bg-info-soft text-info",
  danger: "bg-danger/15 text-danger",
};

export function Badge({
  tone = "neutral",
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-[var(--radius-pill)] px-2 py-0.5",
        "text-[10.5px] font-medium leading-5 whitespace-nowrap tracking-[0.01em]",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

/* ---------------------------------------------------------------- Toggle */

export function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 rounded-[var(--radius-item)] px-2 py-1.5 hover:bg-subtle">
      <span className="min-w-0">
        <span className="block truncate text-xs font-medium text-foreground">{label}</span>
        {hint ? <span className="block truncate text-[11px] text-muted">{hint}</span> : null}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-5 w-9 shrink-0 rounded-full transition-colors duration-200",
          checked ? "bg-accent" : "bg-elevated",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-200",
            checked ? "translate-x-[18px]" : "translate-x-0.5",
          )}
        />
      </button>
    </label>
  );
}

/* -------------------------------------------------------------- StatTile */

export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: Tone;
}) {
  const accent: Record<Tone, string> = {
    neutral: "text-foreground",
    accent: "text-accent",
    live: "text-live",
    warn: "text-warn",
    info: "text-info",
    danger: "text-danger",
  };
  return (
    <div className="rounded-[var(--radius-item)] border border-line bg-surface px-3 py-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">{label}</div>
      <div className={cn("mt-1 font-mono text-lg leading-tight tabular-nums", accent[tone])}>{value}</div>
      {hint ? <div className="mt-0.5 truncate text-[11px] text-muted" title={hint}>{hint}</div> : null}
    </div>
  );
}
