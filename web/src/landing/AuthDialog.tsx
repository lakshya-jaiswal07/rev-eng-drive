import { forwardRef, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Eye, EyeOff, Loader2, X } from "lucide-react";
import { Logo } from "~/components/Logo";
import { AuthFailure, useAuth } from "~/lib/auth";
import { cn } from "~/lib/utils";

export type AuthMode = "login" | "signup";

const Field = forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }
>(function Field({ label, hint, ...props }, ref) {
  const id = `field-${props.name}`;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        aria-describedby={hint ? `${id}-hint` : undefined}
        {...props}
        className={cn(
          "h-11 rounded-[var(--radius-item)] border border-line bg-surface/70 px-3.5 text-sm text-foreground",
          "outline-none transition-colors placeholder:text-muted/70 focus:border-accent/70 focus:bg-surface",
          props.className,
        )}
      />
      {hint ? (
        <span id={`${id}-hint`} className="text-[11px] leading-snug text-muted">
          {hint}
        </span>
      ) : null}
    </div>
  );
});

export function AuthDialog({
  mode,
  onMode,
  onClose,
  onDone,
}: {
  mode: AuthMode;
  onMode: (mode: AuthMode) => void;
  onClose: () => void;
  onDone: () => void;
}) {
  const { login, signup } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);
  // An automatic switch (sign-up -> log-in for an existing email) keeps its explanation.
  const keepError = useRef(false);

  useEffect(() => {
    if (!keepError.current) setError(null);
    keepError.current = false;
    first.current?.focus();
  }, [mode]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") await signup(name, email, password);
      else await login(email, password);
      onDone();
    } catch (err) {
      setError(err instanceof AuthFailure ? err.message : "Something went wrong. Try again.");
      if (err instanceof AuthFailure && err.code === "email_taken") {
        keepError.current = true;
        onMode("login");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <button aria-label="Close" className="absolute inset-0 bg-black/65 backdrop-blur-sm" onClick={onClose} />
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-title"
        className="relative w-full max-w-[420px] rounded-[var(--radius-panel)] border border-line/80 bg-panel p-7 shadow-[var(--shadow-lift)]"
        initial={{ y: 16, scale: 0.98 }}
        animate={{ y: 0, scale: 1 }}
        exit={{ y: 10, scale: 0.98 }}
        transition={{ type: "spring", stiffness: 380, damping: 32 }}
      >
        <button
          aria-label="Close"
          onClick={onClose}
          className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full text-muted hover:bg-subtle hover:text-foreground"
        >
          <X size={16} />
        </button>

        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <Logo size={40} />
          <h2 id="auth-title" className="display text-[1.9rem] leading-none">
            {mode === "signup" ? "Create your account" : "Welcome back"}
          </h2>
          <p className="text-sm text-muted">
            {mode === "signup" ? "Free, and it takes ten seconds." : "Log in to start driving."}
          </p>
        </div>

        <div className="mb-5 grid grid-cols-2 rounded-[var(--radius-pill)] border border-line/80 p-1 text-sm">
          {(["login", "signup"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onMode(m)}
              aria-pressed={mode === m}
              className={cn(
                "h-9 rounded-[var(--radius-pill)] font-medium transition-colors",
                mode === m ? "bg-elevated text-foreground" : "text-muted hover:text-foreground",
              )}
            >
              {m === "login" ? "Log in" : "Sign up"}
            </button>
          ))}
        </div>

        <form className="flex flex-col gap-4" onSubmit={submit}>
          <AnimatePresence initial={false}>
            {mode === "signup" ? (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
                <Field
                  ref={mode === "signup" ? first : undefined}
                  label="Name"
                  name="name"
                  autoComplete="name"
                  placeholder="Your name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </motion.div>
            ) : null}
          </AnimatePresence>
          <Field
            ref={mode === "login" ? first : undefined}
            label="Email"
            name="email"
            type="email"
            autoComplete="email"
            placeholder="you@gmail.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <div className="relative">
            <Field
              label="Password"
              name="password"
              type={show ? "text" : "password"}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              placeholder={mode === "signup" ? "At least 8 characters" : "Your password"}
              minLength={mode === "signup" ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="pr-11"
              hint={
                mode === "signup"
                  ? "Any email works, Gmail included. Choose a new password for RouteVoice - never your email account's password."
                  : undefined
              }
              required
            />
            <button
              type="button"
              aria-label={show ? "Hide password" : "Show password"}
              onClick={() => setShow((s) => !s)}
              className="absolute right-2 top-[30px] flex h-8 w-8 items-center justify-center rounded-full text-muted hover:text-foreground"
            >
              {show ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>

          {error ? (
            <p role="alert" className="rounded-[var(--radius-item)] border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={busy}
            className="mt-1 inline-flex h-11 items-center justify-center gap-2 rounded-[var(--radius-pill)] bg-accent text-sm font-medium text-accent-foreground shadow-[var(--shadow-soft)] transition hover:brightness-110 disabled:opacity-60"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : null}
            {mode === "signup" ? "Create account" : "Log in"}
            {!busy ? <ArrowRight size={15} /> : null}
          </button>
        </form>

        <p className="mt-5 text-center text-xs text-muted">
          {mode === "signup" ? "Already have an account? " : "New to RouteVoice? "}
          <button
            type="button"
            className="font-medium text-accent hover:underline"
            onClick={() => onMode(mode === "signup" ? "login" : "signup")}
          >
            {mode === "signup" ? "Log in" : "Create one"}
          </button>
        </p>
      </motion.div>
    </motion.div>
  );
}
