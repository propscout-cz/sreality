/* The phone's chip language (#1762): a hairline pill in a row that scrolls
 * sideways, pressed = ink on inset, never colour (colour on these screens
 * means the stage). Shared by the feed's and Makléři's Filtry chips and the
 * feed's preset/portal rows so they read as one control. A pick-one (Makléři's
 * Řadit and horizon, Prohlídky's scope, the account sheet's Vzhled) is not a
 * chip row but the one joined bar in its phone tone (`SegmentedBar
 * tone="phone"`, system.md § Option groups), the same ink-on-inset rule; the
 * feed's scope row stays chips by ruling (each chip loads a saved view). */

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

/* iOS zooms the page into any focused field under 16 px, and the desktop
 * controls the phone reuses (the note composer, the tag picker, the filter
 * inputs) are 14 px. On a container, it lifts every field inside to 16 px. */
export const NO_FOCUS_ZOOM = '[&_input]:text-[16px] [&_select]:text-[16px] [&_textarea]:text-[16px]';
