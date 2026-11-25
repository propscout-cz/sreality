import Panel from '@/components/Panel';
import { DiffersGlyph } from '@/components/register/marks';
import { registerBuildingUrl, registerDate, registerFacts } from '@/lib/registerBuilding';
import { useRegisterBuilding } from '@/lib/useRegisterBuilding';

/* Budova: what the RÚIAN register states about the building at the property's door. Its own card, apart from the
 * facts the ads state, with the source and the date of the data on it (the licence asks for both). A row where
 * the property's page says something else carries that value. A property whose door names no building
 * shows no card at all, and so does a failed read: the page stands without it. */
export default function BuildingCard({ propertyId }: { propertyId: number }) {
  const building = useRegisterBuilding(propertyId);
  if (!building) return null;
  const facts = registerFacts(building);
  return (
    <Panel title="Budova" note="podle registru RÚIAN" className="min-w-0">
      <dl>
        {facts.map((f, i) => (
          <div
            key={f.label}
            className={[
              'flex min-w-0 items-baseline justify-between gap-3 py-1.5',
              i === 0 ? 'pt-0' : 'border-t border-[var(--color-rule-soft)]',
            ].join(' ')}
          >
            <dt className="shrink-0 text-[12px] text-[var(--color-ink-3)]">{f.label}</dt>
            <dd className="min-w-0 text-right text-[13px] text-[var(--color-ink)]">
              {f.value ?? <span className="text-[var(--color-ink-3)]">nezjištěno</span>}
              {f.derived ? <span className="block text-[12px] text-[var(--color-ink-3)]">{f.derived}</span> : null}
              {f.note ? (
                <span className="block text-[12px] text-[var(--color-ink-2)]">
                  <DiffersGlyph /> {f.note}
                </span>
              ) : null}
            </dd>
          </div>
        ))}
      </dl>
      <p className="border-t border-[var(--color-rule-soft)] pt-2.5 text-xs text-[var(--color-ink-3)]">
        Zdroj: ČÚZK, RÚIAN{building.source_date ? `, stav k ${registerDate(building.source_date)}` : ''} ·{' '}
        <a href={registerBuildingUrl(building.code)} target="_blank" rel="noreferrer"
          className="text-[var(--color-ink-2)] underline decoration-[var(--color-rule-strong)] underline-offset-2 hover:text-[var(--color-copper)]">
          stavební objekt <span className="font-mono">{building.code}</span>
        </a>
      </p>
    </Panel>
  );
}
