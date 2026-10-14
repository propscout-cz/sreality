/* The property's Artifact link (migration 599) as a card prints it: a small
 * button-shaped anchor to the page the operator keeps about the property,
 * drawn only when the property has one and only when it parses as http(s)
 * (lib/artifactLink). One component for the board card and the phone card,
 * over the one whole-set read (lib/useArtifactLinks); editing stays on the
 * property page's row. Opens in a new tab: a card is a triage surface and
 * following its link must not unload it. Drawn in the accent (copper) so it is
 * found at a glance on a dense column. */

import { artifactLinkLabel, safeHttpUrl } from '@/lib/artifactLink';
import { useArtifactLinks } from '@/lib/useArtifactLinks';

export const ARTIFACT_BUTTON_LABEL = 'Artifact';

export default function ArtifactLinkButton({
  property_id,
  className = '',
}: {
  property_id: number;
  /* The host's own size and stacking (the phone's `card-action`). */
  className?: string;
}) {
  const linksQ = useArtifactLinks();
  const href = safeHttpUrl(linksQ.data?.get(property_id)?.url ?? '');
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={href}
      aria-label={`${ARTIFACT_BUTTON_LABEL}: ${artifactLinkLabel(href)}`}
      className={[
        /* The accent: copper on copper-soft, the app's "on" chip look, so the
           button stands out on the card without shouting over the price; solid
           copper under the pointer, like a primary button. */
        'inline-flex items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--color-copper)] bg-[var(--color-copper-soft)] font-medium text-[var(--color-copper)] transition-colors hover:bg-[var(--color-copper)] hover:text-white',
        className,
      ].join(' ')}
    >
      {ARTIFACT_BUTTON_LABEL}
      <span aria-hidden>↗</span>
    </a>
  );
}
