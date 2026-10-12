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

/* A row that opens a page of its own. */
export function ChevronIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M9 5l7 7-7 7" />
    </Svg>
  );
}

export function PlusIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  );
}

export function SearchIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="10.5" cy="10.5" r="6" />
      <path d="M15 15l5 5" />
    </Svg>
  );
}

/* Clears a field. */
export function ClearIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M7 7l10 10M17 7L7 17" />
    </Svg>
  );
}

/* A place filter. */
export function PinIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 21s-6.5-6.2-6.5-11.2a6.5 6.5 0 0 1 13 0C18.5 14.8 12 21 12 21z" />
      <circle cx="12" cy="9.8" r="2.2" />
    </Svg>
  );
}

/* A monitored collection ("sledováno"). */
export function BellIcon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 3.5a5 5 0 0 0-5 5c0 4.5-2 6-2 6h14s-2-1.5-2-6a5 5 0 0 0-5-5z" />
      <path d="M10 18.5a2 2 0 0 0 4 0" />
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
