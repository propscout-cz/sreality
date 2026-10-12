/* The phone shell's own glyphs, drawn to icons.tsx's grammar (24 box, 1.75
 * stroke, round caps, currentColor) so they sit beside FunnelIcon and
 * EyeOffIcon without a seam. Decorative: the control carries the label. */

type IconProps = { className?: string };

function Svg({ className = 'h-5 w-5', children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      aria-hidden
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

/* The newest-first roll: a card over two lines of text. */
export function RollIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="4" y="3.5" width="16" height="10" rx="1" />
      <path d="M4 17.5h16M4 20.5h10" />
    </Svg>
  );
}

export function PersonIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20.5c.8-3.8 3.6-6 7-6s6.2 2.2 7 6" />
    </Svg>
  );
}

export function BackIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M15 5l-7 7 7 7" />
    </Svg>
  );
}

/* The stage badge's "opens a menu" caret. */
export function ChevronDownIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M7 10l5 5 5-5" />
    </Svg>
  );
}

export function RefreshIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
      <path d="M19.5 4.5v4h-4" />
    </Svg>
  );
}

export function CheckIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </Svg>
  );
}
