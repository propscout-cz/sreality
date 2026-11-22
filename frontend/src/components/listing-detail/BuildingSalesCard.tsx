import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ErrorBanner from '@/components/ErrorBanner';
import Panel, { PANEL_ACTION, PanelEmpty } from '@/components/Panel';
import { Th } from '@/components/table';
import {
  buyBuildingPrices, cancelBuildingPurchase, fetchBuildingPrices, fetchPropertyBuildingSales, type BuildingPrices,
} from '@/lib/api';
import { saleRows, type SaleRow } from '@/lib/buildingSales';
import { fmtArea, fmtCzk, fmtDateSlash, fmtMeasuredPricePerM2 } from '@/lib/format';
import { ppm2BasisFromToken } from '@/lib/measure';
import { advertLink } from '@/lib/portals';

/* Prodeje v domě: the registered sales in the building at the property's door. Two sources in one list, each row
 * naming its own: the shared sales we hold, and the cadastre deeds THIS account bought (visible to no other account).
 *
 * The paid action buys on the account's own ČÚZK account, and never on one click: the button opens a step that names
 * the price (and, for a building already paid for, that fact) before anything is sent. A purchase in flight is
 * CONTINUED by its id, which can never start another purchase, or given up (which sends nothing). Every state has a
 * text that says truthfully whether money moved, and a way forward. While a run of the account is live the card
 * offers nothing and re-reads the state.
 *
 * Building-level facts: the card never says which sale, if any, is this property's own flat. With no building and no
 * sale, no card. */

const pricesKey = (propertyId: number) => ['property-building-prices', propertyId] as const;
const NOTE = 'text-[0.78rem] text-[var(--color-ink-2)]';
const LIVE_POLL_MS = 4000;

type How = { repeat?: boolean; resume?: number; cancel?: number };

export default function BuildingSalesCard({ propertyId }: { propertyId: number }) {
  const queryClient = useQueryClient();
  const salesQ = useQuery({
    queryKey: ['property-building-sales', propertyId],
    queryFn: () => fetchPropertyBuildingSales(propertyId),
    staleTime: 10 * 60_000,
  });
  const pricesQ = useQuery({
    queryKey: pricesKey(propertyId),
    queryFn: () => fetchBuildingPrices(propertyId),
    /* The read calls nothing at ČÚZK, so watching a live run costs nothing. */
    refetchInterval: (q) => (q.state.data?.running ? LIVE_POLL_MS : false),
  });
  const [confirming, setConfirming] = useState(false);
  const buy = useMutation({
    mutationFn: (how: How) => (how.cancel != null
      ? cancelBuildingPurchase(propertyId, how.cancel)
      : buyBuildingPrices(propertyId, pricesQ.data?.price_czk ?? 0, how)),
    onSuccess: (state) => queryClient.setQueryData<BuildingPrices>(pricesKey(propertyId), state),
    /* A refusal can change what the card may offer (a login ČÚZK refused, a purchase that ended). */
    onError: () => void queryClient.invalidateQueries({ queryKey: pricesKey(propertyId) }),
    onSettled: () => setConfirming(false),
  });

  const prices = pricesQ.data;
  const rows = saleRows(salesQ.data?.sales ?? [], prices?.deeds ?? []);
  if (rows.length === 0 && !prices?.available) return null;

  const purchase = prices?.purchase ?? null;
  const price = prices?.price_czk ?? 0;
  const bought = purchase?.status === 'done' ? purchase : null;
  const inFlight = purchase != null && (purchase.status === 'pending' || purchase.status === 'ordered');
  const unread = purchase?.status === 'paid_unread' ? purchase : null;
  const paidBefore = bought ?? unread;
  /* A live run (this tab's or another's) holds the account's ČÚZK login: nothing may be asked beside it. */
  const live = buy.isPending || !!prices?.running;
  const canPay = !!prices?.available && prices.login_set && !prices.login_refused && !live;

  let accessory = null;
  if (canPay && inFlight) {
    accessory = (
      <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ resume: purchase.id })}>
        {purchase.fetch_sent ? 'Dokončit načtení (bez dalšího poplatku)' : `Dokončit načtení z katastru · ${price} Kč`}
      </button>
    );
  } else if (canPay) {
    accessory = (
      <button type="button" className={PANEL_ACTION} disabled={confirming} onClick={() => setConfirming(true)}>
        {paidBefore ? 'Načíst znovu' : 'Načíst ceny'} z katastru · {price} Kč
      </button>
    );
  }
  const note = bought?.finished_at
    ? `ceny z katastru načteny ${fmtDateSlash(bought.finished_at)}`
    : 'registrované prodeje ve stejné budově';
  const boughtNothing = !!bought && !rows.some((r) => r.source === 'katastr');

  return (
    <Panel title="Prodeje v domě" count={rows.length} note={note} accessory={accessory}>
      {prices?.available && !prices.login_set ? (
        <p className={NOTE}>
          Ceny z katastru načtete po uložení přihlášení k ČÚZK (menu účtu → ČÚZK login). Platí se na vašem účtu u ČÚZK.
        </p>
      ) : null}
      {prices?.available && prices.login_set && prices.login_refused ? (
        <p role="status" className={NOTE}>
          ČÚZK vaše uložené přihlášení odmítl. Nic dalšího se na něj proto neodesílá, aby se váš účet u ČÚZK
          nezablokoval. Uložte ho znovu (menu účtu → ČÚZK login).
        </p>
      ) : null}
      {live ? (
        <p role="status" className={NOTE}>
          Načítání z katastru právě probíhá. Může trvat až dvě minuty; nic dalšího teď neodesílejte.
        </p>
      ) : null}
      {inFlight && !live ? (
        <p role="status" className={`${NOTE} flex flex-wrap items-center gap-x-3 gap-y-2`}>
          <span className="min-w-0 flex-1">
            {purchase.fetch_sent
              ? `Sestava byla u ČÚZK vyžádána, ale odpověď nedorazila, takže mohla být zaúčtována (${price} Kč). Nezačínejte nový nákup: dokončení načte tutéž sestavu bez dalšího poplatku, ČÚZK účtuje jen první otevření.`
              : `Načtení z katastru je rozpracované: ČÚZK sestavu připravuje, nebo se objednávka nedokončila. Dokončením zaplatíte nejvýše jednou, ${price} Kč.`}
            {' '}Pokud dokončení opakovaně selhává, můžete toto načtení zrušit; tím se nic neodesílá
            {purchase.fetch_sent ? ' a sestava zůstane vedená jako možná zaplacená.' : ' a nic se neúčtuje.'}
          </span>
          {prices?.login_set ? (
            <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ cancel: purchase.id })}>
              Zrušit toto načtení
            </button>
          ) : null}
        </p>
      ) : null}
      {unread && !live ? (
        <p role="status" className={`${NOTE} flex flex-wrap items-center gap-x-3 gap-y-2`}>
          <span className="min-w-0 flex-1">
            {unread.file_kept
              ? 'Sestavu z katastru jste zaplatili, ale nepodařilo se ji přečíst. Je u nás uložená; přečíst ji lze zkusit znovu bez placení.'
              : `Sestava byla u ČÚZK vyžádána a mohla být zaúčtována, ale nemáme ji. Můžeme ji zkusit vyzvednout znovu: za tuto sestavu nezaplatíte celkem víc než ${price} Kč. Pokud ji ČÚZK už nenabízí, najdete ji mezi výstupy svého účtu u ČÚZK.`}
          </span>
          {prices?.login_set ? (
            <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ resume: unread.id })}>
              {unread.file_kept ? 'Zkusit přečíst znovu (zdarma)' : 'Zkusit vyzvednout znovu'}
            </button>
          ) : null}
        </p>
      ) : null}
      {boughtNothing ? (
        <p role="status" className={NOTE}>
          Katastr k této budově neeviduje žádnou cenu (načteno {fmtDateSlash(bought.finished_at ?? bought.requested_at)}).
        </p>
      ) : null}
      {confirming && canPay && !inFlight ? (
        <div role="group" aria-label="Potvrzení placeného načtení"
          className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-[0.78rem] text-[var(--color-ink-2)]">
          <span className="min-w-0 flex-1">
            {bought
              ? `Ceny z katastru už máte${bought.finished_at ? ` (načteny ${fmtDateSlash(bought.finished_at)})` : ''}. Nové načtení stojí dalších ${price} Kč na vašem účtu u ČÚZK.`
              : unread
                ? `Za tuto budovu jste už jednou platili a sestavu se nepodařilo získat. Nové načtení stojí dalších ${price} Kč na vašem účtu u ČÚZK.`
                : `Načtení cen z katastru stojí ${price} Kč na vašem účtu u ČÚZK.`}
            {' '}Platí se i tehdy, když katastr k budově žádnou cenu neeviduje.
          </span>
          <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ repeat: !!paidBefore })}>
            Zaplatit {price} Kč a načíst
          </button>
          <button type="button" className={PANEL_ACTION} onClick={() => setConfirming(false)}>
            Zrušit
          </button>
        </div>
      ) : null}
      {buy.isError ? <ErrorBanner flush title="Nenačteno:" message={buy.error.message} /> : null}
      {rows.length === 0 ? (
        <PanelEmpty>
          Žádný prodej v tomto domě zatím neevidujeme.{purchase ? '' : ' Z katastru zatím nenačteno.'}
        </PanelEmpty>
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
