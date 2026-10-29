import { motion, type MotionValue } from "motion/react";

/**
 * A side-on saloon driving to the right, drawn in layers: shadow, body with a
 * light-to-dark paint gradient and a reflection band, glasshouse with chrome
 * trim, lights, and alloy wheels whose spokes turn with `wheelTurn` (degrees,
 * tied to scroll) while the brake calipers stay put, as they do on a real car.
 */
export function Car({ wheelTurn, className }: { wheelTurn: MotionValue<number>; className?: string }) {
  return (
    <svg viewBox="0 0 640 240" className={className} role="img" aria-label="A car driving to the right">
      <defs>
        <linearGradient id="rv-paint" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="oklch(0.8 0.12 40)" />
          <stop offset="28%" stopColor="oklch(0.68 0.17 33)" />
          <stop offset="62%" stopColor="oklch(0.56 0.17 30)" />
          <stop offset="100%" stopColor="oklch(0.34 0.11 28)" />
        </linearGradient>
        <linearGradient id="rv-reflect" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="white" stopOpacity="0" />
          <stop offset="35%" stopColor="white" stopOpacity="0.22" />
          <stop offset="60%" stopColor="white" stopOpacity="0.06" />
          <stop offset="100%" stopColor="white" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="rv-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="oklch(0.5 0.04 240)" />
          <stop offset="45%" stopColor="oklch(0.24 0.03 250)" />
          <stop offset="100%" stopColor="oklch(0.14 0.02 260)" />
        </linearGradient>
        <linearGradient id="rv-rim" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="oklch(0.92 0.005 260)" />
          <stop offset="55%" stopColor="oklch(0.66 0.01 260)" />
          <stop offset="100%" stopColor="oklch(0.42 0.01 260)" />
        </linearGradient>
        <radialGradient id="rv-shadow" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0%" stopColor="black" stopOpacity="0.6" />
          <stop offset="100%" stopColor="black" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="rv-beam" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="oklch(0.97 0.06 95)" stopOpacity="0.5" />
          <stop offset="100%" stopColor="oklch(0.97 0.06 95)" stopOpacity="0" />
        </linearGradient>
        <clipPath id="rv-body-clip">
          <path d={BODY} />
        </clipPath>
      </defs>

      {/* headlight beam: only visible at night (see --beam-opacity) */}
      <path d="M598 140 L640 116 L640 196 Z" fill="url(#rv-beam)" style={{ opacity: "var(--beam-opacity, 1)" }} />
      {/* ground shadow */}
      <ellipse cx="322" cy="216" rx="300" ry="14" fill="url(#rv-shadow)" />

      {/* body */}
      <path d={BODY} fill="url(#rv-paint)" />
      <g clipPath="url(#rv-body-clip)">
        {/* reflection band along the doors and a darker sill */}
        <rect x="40" y="128" width="580" height="22" fill="url(#rv-reflect)" />
        <rect x="0" y="176" width="640" height="20" fill="black" opacity="0.18" />
      </g>
      {/* shoulder line catching the light, and the lower character line */}
      <path d="M58 129 C200 122 420 122 590 133" fill="none" stroke="white" strokeOpacity="0.32" strokeWidth="1.6" />
      <path d="M112 166 C260 162 420 162 548 165" fill="none" stroke="black" strokeOpacity="0.22" strokeWidth="1.4" />

      {/* glasshouse */}
      <path d="M176 119 C190 104 212 86 254 76 L300 73 L300 119 Z" fill="url(#rv-glass)" />
      <path d="M311 119 L311 72 L362 72 C392 76 418 94 440 116 Z" fill="url(#rv-glass)" />
      <path d="M178 118 C192 103 214 86 255 76 L362 71 C394 75 421 94 443 117" fill="none" stroke="oklch(0.86 0.005 260)" strokeWidth="2" opacity="0.7" />
      {/* B-pillar and a sky reflection across the glass */}
      <rect x="300" y="75" width="11" height="45" fill="oklch(0.16 0.01 260)" />
      <path d="M232 119 L262 76 L278 75 L248 119 Z M352 119 L372 74 L382 76 L364 119 Z" fill="white" opacity="0.1" />

      {/* door seams, handles, mirror */}
      <g fill="none" stroke="oklch(0.3 0.08 28)" strokeWidth="1.6" strokeLinecap="round">
        <path d="M186 124 C190 150 194 172 200 186" />
        <path d="M305 121 L302 188" />
        <path d="M444 119 C446 140 450 160 456 176" />
      </g>
      <rect x="258" y="134" width="24" height="5" rx="2.5" fill="oklch(0.3 0.08 28)" />
      <rect x="398" y="134" width="24" height="5" rx="2.5" fill="oklch(0.3 0.08 28)" />
      <path d="M440 113 C446 104 458 102 466 106 L464 116 C456 117 447 117 440 113 Z" fill="oklch(0.5 0.15 30)" />
      <path d="M447 112 L462 108" stroke="white" strokeOpacity="0.35" strokeWidth="1.2" />

      {/* front: headlight with daytime running light, intake, bumper */}
      <path d="M560 131 C576 131 594 135 606 142 L602 152 C588 150 572 147 558 143 Z" fill="oklch(0.95 0.03 90)" />
      <path d="M564 134 C580 135 594 139 603 145" stroke="white" strokeWidth="2.2" strokeLinecap="round" fill="none" />
      <path d="M574 170 L612 168 L610 182 L578 184 Z" fill="oklch(0.18 0.01 260)" />
      <path d="M570 186 L606 184" stroke="oklch(0.85 0.005 260)" strokeOpacity="0.6" strokeWidth="1.4" />
      {/* rear: tail light and bumper */}
      <path d="M38 132 L70 128 L70 142 L40 147 Z" fill="oklch(0.55 0.22 25)" />
      <path d="M42 136 L66 133" stroke="oklch(0.78 0.18 25)" strokeWidth="2" strokeLinecap="round" />
      <path d="M36 176 L84 178" stroke="oklch(0.2 0.02 28)" strokeOpacity="0.6" strokeWidth="3" strokeLinecap="round" />

      {/* a quiet sound wave on the rear door: the car is listening */}
      <g stroke="white" strokeOpacity="0.75" strokeWidth="3" strokeLinecap="round">
        <path d="M222 146v10M232 142v18M242 148v6M252 140v22M262 145v12" />
      </g>

      {[150, 500].map((cx) => (
        <Wheel key={cx} cx={cx} cy={184} turn={wheelTurn} />
      ))}
    </svg>
  );
}

// Side profile: rear bumper, boot, C-pillar, roof, windscreen, bonnet, nose,
// front bumper, then the sill between two wheel arches.
const BODY =
  "M44 192 C36 186 32 176 33 166 L35 140 C36 132 44 126 56 124 L150 118 C176 98 204 80 250 70 " +
  "C290 63 340 62 368 64 C400 68 432 90 458 114 L560 126 C584 129 602 136 610 146 C616 156 616 172 612 184 " +
  "C610 190 604 194 596 194 L548 194 A48 48 0 0 0 452 194 L198 194 A48 48 0 0 0 102 194 Z";

function Wheel({ cx, cy, turn }: { cx: number; cy: number; turn: MotionValue<number> }) {
  const spokes = Array.from({ length: 10 }, (_, i) => i * 36);
  return (
    <g>
      {/* wheel-well shadow inside the arch (above the sill only), tyre, sidewall */}
      <path d={`M${cx - 46} 194 A46 46 0 0 1 ${cx + 46} 194 Z`} fill="oklch(0.08 0.004 270)" />
      <circle cx={cx} cy={cy} r={38} fill="oklch(0.17 0.004 270)" />
      <circle cx={cx} cy={cy} r={34} fill="none" stroke="oklch(0.27 0.004 270)" strokeWidth="2" />
      <circle cx={cx} cy={cy} r={26} fill="oklch(0.12 0.004 270)" />
      {/* brake disc and caliper: fixed, behind the spokes */}
      <circle cx={cx} cy={cy} r={18} fill="oklch(0.45 0.005 260)" />
      <path d={`M${cx + 9} ${cy - 17} A19 19 0 0 1 ${cx + 19} ${cy - 3} L${cx + 13} ${cy - 2} A13 13 0 0 0 ${cx + 6} ${cy - 12} Z`} fill="oklch(0.55 0.2 27)" />
      {/* the rim, which rotates; the clear circle keeps the group's box centred on the hub */}
      <motion.g style={{ rotate: turn }}>
        <circle cx={cx} cy={cy} r={26} fill="transparent" />
        <circle cx={cx} cy={cy} r={25} fill="none" stroke="url(#rv-rim)" strokeWidth="3.5" />
        {spokes.map((a) => (
          <path
            key={a}
            d={`M${cx - 2} ${cy - 6} L${cx - 3.2} ${cy - 23} L${cx + 3.2} ${cy - 23} L${cx + 2} ${cy - 6} Z`}
            fill="url(#rv-rim)"
            transform={`rotate(${a} ${cx} ${cy})`}
          />
        ))}
        <circle cx={cx} cy={cy} r={7} fill="url(#rv-rim)" />
        <circle cx={cx} cy={cy} r={3} fill="oklch(0.3 0.01 260)" />
      </motion.g>
    </g>
  );
}
