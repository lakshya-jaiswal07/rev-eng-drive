import { motion, type MotionValue } from "motion/react";

/**
 * A side-on hatchback driving to the right. `wheelTurn` is a rotation in
 * degrees, tied to scroll so the wheels roll exactly as far as the car moves.
 */
export function Car({ wheelTurn, className }: { wheelTurn: MotionValue<number>; className?: string }) {
  return (
    <svg viewBox="0 0 420 170" className={className} role="img" aria-label="A car driving to the right">
      <defs>
        <linearGradient id="rv-body" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="oklch(0.74 0.15 36)" />
          <stop offset="55%" stopColor="var(--accent)" />
          <stop offset="100%" stopColor="oklch(0.48 0.14 32)" />
        </linearGradient>
        <linearGradient id="rv-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="oklch(0.42 0.03 250)" />
          <stop offset="100%" stopColor="oklch(0.2 0.02 260)" />
        </linearGradient>
        <radialGradient id="rv-beam" cx="0" cy="0.5" r="1">
          <stop offset="0%" stopColor="oklch(0.97 0.06 90 / 0.55)" />
          <stop offset="100%" stopColor="oklch(0.97 0.06 90 / 0)" />
        </radialGradient>
      </defs>

      {/* headlight beam on the road ahead */}
      <path d="M392 104 L420 78 L420 140 Z" fill="url(#rv-beam)" />
      {/* shadow */}
      <ellipse cx="200" cy="150" rx="178" ry="9" fill="black" opacity="0.45" />

      {/* body */}
      <path
        d="M30 118 C26 98 40 88 66 84 L118 78 C140 54 168 40 210 38 L262 38 C292 38 312 52 334 76 L374 84 C392 88 400 100 398 116 L396 124 C396 128 392 130 388 130 L36 130 C32 130 30 126 30 118 Z"
        fill="url(#rv-body)"
      />
      {/* windows */}
      <path d="M134 80 C150 60 172 50 204 48 L210 48 L210 80 Z" fill="url(#rv-glass)" />
      <path d="M220 48 L258 48 C284 48 300 60 318 80 L220 80 Z" fill="url(#rv-glass)" />
      {/* door line, handle, mirror */}
      <path d="M214 84 L214 126" stroke="oklch(0.38 0.1 32)" strokeWidth="2" />
      <rect x="226" y="92" width="18" height="4" rx="2" fill="oklch(0.38 0.1 32)" />
      <path d="M318 78 L332 74 L334 82 Z" fill="oklch(0.38 0.1 32)" />
      {/* lights */}
      <path d="M384 92 C392 94 397 100 397 106 L382 104 Z" fill="oklch(0.97 0.05 95)" />
      <rect x="31" y="96" width="9" height="12" rx="3" fill="oklch(0.6 0.22 25)" />
      {/* a sound wave on the door: the car is talking */}
      <g stroke="oklch(0.98 0.01 40)" strokeWidth="3" strokeLinecap="round" opacity="0.9">
        <path d="M150 100v12M160 96v20M170 102v8M180 94v24M190 100v12" />
      </g>

      {/* wheels */}
      {[104, 316].map((cx) => (
        <g key={cx}>
          <circle cx={cx} cy={128} r={25} fill="oklch(0.16 0.004 270)" />
          {/* motion rotates SVG groups about their own centre: the hub */}
          <motion.g style={{ rotate: wheelTurn }}>
            <circle cx={cx} cy={128} r={14} fill="oklch(0.62 0.01 260)" />
            {[0, 72, 144, 216, 288].map((a) => (
              <rect key={a} x={cx - 1.6} y={116} width={3.2} height={10} rx={1.5} fill="oklch(0.32 0.01 260)" transform={`rotate(${a} ${cx} 128)`} />
            ))}
            <circle cx={cx} cy={128} r={4} fill="oklch(0.32 0.01 260)" />
          </motion.g>
        </g>
      ))}
    </svg>
  );
}
