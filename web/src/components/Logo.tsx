/** RouteVoice mark: a route that turns into a sound wave. */
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="RouteVoice">
      <rect width="32" height="32" rx="9" fill="var(--accent)" />
      <g fill="none" stroke="var(--accent-foreground)" strokeWidth="2.2" strokeLinecap="round">
        <path d="M7 23c3 0 4-4 6.5-4S17 23 20 23" />
        <path d="M20 23v-6M23.5 23V12M27 23v-8" opacity="0.8" />
      </g>
      <circle cx="7" cy="23" r="2.2" fill="var(--accent-foreground)" />
    </svg>
  );
}
