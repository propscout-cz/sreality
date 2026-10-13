/* The phone's chip language (#1762): a hairline pill in a row that scrolls
 * sideways, pressed = ink on inset, never colour (colour on these screens
 * means the stage). Shared by the feed's preset/portal rows and the brokers'
 * filter rows so the two read as one control. */

export const CHIP =
  'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-[var(--radius-sm)] border transition-colors';
export const CHIP_MD = 'h-8 px-3 text-[13px]';
export const CHIP_SM = 'h-7 px-2 text-[12px]';
export const chipState = (pressed: boolean): string =>
  pressed
    ? 'border-[var(--color-ink-2)] bg-[var(--color-inset)] font-medium text-[var(--color-ink)]'
    : 'border-[var(--color-rule-strong)] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]';
export const CHIP_ROW =
  'flex gap-2 overflow-x-auto px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden';
/* The thin upright rule that separates groups inside one chip row. */
export const CHIP_DIVIDER = 'my-1 w-px shrink-0 bg-[var(--color-rule-strong)]';
