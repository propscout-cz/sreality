import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ErrorBanner from '@/components/ErrorBanner';
import Panel, { PANEL_ACTION, PanelEmpty } from '@/components/Panel';
import { Th } from '@/components/table';
import { buyBuildingPrices, fetchBuildingPrices, fetchPropertyBuildingSales, type BuildingPrices } from '@/lib/api';
import { saleRows, type SaleRow } from '@/lib/buildingSales';
import { fmtArea, fmtCzk, fmtDateSlash, fmtMeasuredPricePerM2 } from '@/lib/format';
import { ppm2BasisFromToken } from '@/lib/measure';
import { advertLink } from '@/lib/portals';

/* Prodeje v domě: the registered sales in the building at the property's door. Two sources in one list, each row
 * naming its own: the shared sales we hold, and the cadastre deeds THIS account bought (visible to no other account).
 * The one paid action is the button, which shows its price and buys on the account's own ČÚZK account; paying again
 * for a building already bought takes a second, explicit step that names the earlier purchase. Building-level
 * facts: the card never says which sale, if any, is this property's own flat. With no building and no sale, no card. */

const pricesKey = (propertyId: number) => ['property-building-prices', propertyId] as const;

export default function BuildingSalesCard({ propertyId }: { propertyId: number }) {
  const queryClient = useQueryClient();
  const salesQ = useQuery({
    queryKey: ['property-building-sales', propertyId],
    queryFn: () => fetchPropertyBuildingSales(propertyId),
    staleTime: 10 * 60_000,
  });
  const pricesQ = useQuery({ queryKey: pricesKey(propertyId), queryFn: () => fetchBuildingPrices(propertyId) });
  const [confirmingRepeat, setConfirmingRepeat] = useState(false);
  const buy = useMutation({
    mutationFn: ({ costCzk, repeat }: { costCzk: number; repeat: boolean }) => buyBuildingPrices(propertyId, costCzk, repeat),
    onSuccess: (state) => queryClient.setQueryData<BuildingPrices>(pricesKey(propertyId), state),
    onSettled: () => setConfirmingRepeat(false),
  });

  const prices = pricesQ.data;
  const rows = saleRows(salesQ.data?.sales ?? [], prices?.deeds ?? []);
  if (rows.length === 0 && !prices?.available) return null;

  const bought = prices?.purchase?.status === 'done' ? prices.purchase : null;
  const inFlight = prices?.purchase != null && prices.purchase.status !== 'done';
  let accessory = null;
  if (prices?.available && prices.login_set) {
    accessory = (
      <button type="button" className={PANEL_ACTION} disabled={buy.isPending || confirmingRepeat}
        onClick={() => (bought && !inFlight ? setConfirmingRepeat(true) : buy.mutate({ costCzk: prices.price_czk, repeat: false }))}>
        {buy.isPending
          ? 'Načítám z katastru…'
          : inFlight
            ? 'Dokončit načtení z katastru'
            : `${bought ? 'Načíst znovu' : 'Načíst ceny'} z katastru · ${prices.price_czk} Kč`}
      </button>
    );
  }
  const note = bought?.finished_at
    ? `ceny z katastru načteny ${fmtDateSlash(bought.finished_at)}`
    : 'registrované prodeje ve stejné budově';

  return (
    <Panel title="Prodeje v domě" count={rows.length} note={note} accessory={accessory}>
      {prices?.available && !prices.login_set ? (
        <p className="text-[0.78rem] text-[var(--color-ink-2)]">
          Ceny z katastru načtete po uložení přihlášení k ČÚZK (menu účtu → ČÚZK login). Platí se na vašem účtu u ČÚZK.
        </p>
      ) : null}
      {confirmingRepeat && bought && prices ? (
        <div role="group" aria-label="Potvrzení nového načtení"
          className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-[0.78rem] text-[var(--color-ink-2)]">
          <span className="min-w-0 flex-1">
            Ceny z katastru už máte{bought.finished_at ? ` (načteny ${fmtDateSlash(bought.finished_at)})` : ''}. Nové načtení
            stojí dalších {prices.price_czk} Kč na vašem účtu u ČÚZK.
          </span>
          <button type="button" className={PANEL_ACTION} disabled={buy.isPending}
            onClick={() => buy.mutate({ costCzk: prices.price_czk, repeat: true })}>
            {buy.isPending ? 'Načítám z katastru…' : `Zaplatit ${prices.price_czk} Kč a načíst znovu`}
          </button>
          <button type="button" className={PANEL_ACTION} disabled={buy.isPending} onClick={() => setConfirmingRepeat(false)}>
            Zrušit
          </button>
        </div>
      ) : null}
      {buy.isError ? <ErrorBanner flush title="Nenačteno:" message={buy.error.message} /> : null}
      {rows.length === 0 ? (
        <PanelEmpty>Žádný prodej v tomto domě zatím neevidujeme. Z katastru zatím nenačteno.</PanelEmpty>
      ) : (
        /* A table that scrolls keeps its own box (system.md § Section cards). */
        <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[var(--color-rule)]">
          <table className="w-full text-sm">
            <thead className="border-b border-[var(--color-rule)] bg-[var(--color-paper-2)]">
              <tr>
                <Th size="xs">Prodáno</Th>
                <Th size="xs">Jednotka</Th>
                <Th size="xs">Dispozice</Th>
                <Th size="xs" align="right">Plocha</Th>
                <Th size="xs" align="right">Cena</Th>
                <Th size="xs" align="right">Za m²</Th>
                <Th size="xs">Zdroj</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => <SaleRowView key={row.key} row={row} />)}
            </tbody>
          </table>
        </div>
      )}
      {rows.some((r) => r.source === 'katastr') ? (
        <p className="text-xs text-[var(--color-ink-3)]">
          Cena z katastru je za celou listinu a katastr neuvádí plochu, proto je bez ceny za m². Ceny z katastru vidí jen
          váš účet. Zdroj: ČÚZK.
        </p>
      ) : null}
    </Panel>
  );
}

const CELL = 'px-2.5 py-2 whitespace-nowrap';
const NUM = `${CELL} text-right font-mono text-[13px] tabular-nums`;

function SaleRowView({ row }: { row: SaleRow }) {
  const link = advertLink(row);
  const price = row.price_czk == null ? '—' : row.currency && row.currency !== 'CZK'
    ? `${row.price_czk.toLocaleString('cs-CZ')} ${row.currency}`
    : fmtCzk(row.price_czk);
  return (
    <tr className="border-b border-[var(--color-rule-soft)] last:border-b-0">
      <td className={`${CELL} font-mono text-[13px] tabular-nums`}>{fmtDateSlash(row.date)}</td>
      <td className={`${CELL} font-mono text-[13px]`}>
        {row.unit ?? <span className="font-sans text-[var(--color-ink-3)]">celá budova</span>}
        {row.also ? <span className="ml-1.5 font-sans text-xs text-[var(--color-ink-3)]">{row.also}</span> : null}
      </td>
      <td className={CELL}>{row.disposition ?? '—'}</td>
      <td className={NUM}>{fmtArea(row.area_m2)}</td>
      <td className={NUM}>{price}</td>
      <td className={NUM}>{fmtMeasuredPricePerM2(row.price_per_m2, ppm2BasisFromToken(row.price_per_m2_basis))}</td>
      <td className={`${CELL} text-[var(--color-ink-2)]`}>
        {row.source === 'katastr' ? (
          <span title={row.proceeding ?? undefined}>Katastr <span className="font-mono text-xs text-[var(--color-ink-3)]">{row.proceeding}</span></span>
        ) : link ? (
          <a href={link} target="_blank" rel="noreferrer"
            className="underline decoration-[var(--color-rule-strong)] underline-offset-2 hover:text-[var(--color-copper)]">
            Reas
          </a>
        ) : 'Reas'}
      </td>
    </tr>
  );
}
