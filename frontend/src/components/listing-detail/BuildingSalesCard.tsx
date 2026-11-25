import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ErrorBanner from '@/components/ErrorBanner';
import Panel, { PANEL_ACTION, PanelEmpty } from '@/components/Panel';
import { Th } from '@/components/table';
import {
  buyBuildingPrices, buyBuildingUnit, cancelBuildingPurchase, cancelBuildingUnitPurchase, fetchBuildingPrices, fetchPropertyBuildingSales,
  type BuildingPrices, type CadastreUnit,
} from '@/lib/api';
import { saleRows, showsCadastre, type SaleRow } from '@/lib/buildingSales';
import { fmtArea, fmtCzk, fmtDateSlash, fmtMeasuredPricePerM2 } from '@/lib/format';
import { propertyPath } from '@/lib/listingUrl';
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
 * Building-level facts. The card does not claim a sale is this property's own flat; a bought deed that borrows its
 * area from an advertisement says whose (a link to that property, plain text when it is the one on screen). With
 * no building and no sale, no card. */

const pricesKey = (propertyId: number) => ['property-building-prices', propertyId] as const;
const NOTE = 'text-[0.78rem] text-[var(--color-ink-2)]';
const LIVE_POLL_MS = 4000;
/* Why a request did nothing, in the user's words. `preparing` and `fetching` are said by the state's own note. */
const NOTICE: Record<string, string> = {
  busy: 'Vaše přihlášení k ČÚZK právě používá jiný požadavek, proto se teď nic neprovedlo. Zkuste to za chvíli znovu.',
  in_flight: 'Načtení této budovy už je rozpracované, nové se proto nezačalo. Pokračujte v rozpracovaném.',
  time: 'Požadavek nestihl doběhnout, nic dalšího se neodeslalo. Pokračujte tlačítkem pro dokončení.',
  login: 'Uložené přihlášení k ČÚZK teď nelze použít (bylo změněno či odmítnuto, nebo se na něj právě čeká). Nic dalšího se neodeslalo.',
};

/* The same answers for a unit's request, where the building's wording would mislead. */
const UNIT_NOTICE: Record<string, string> = {
  in_flight: 'Načtení této jednotky už je rozpracované, nové se proto nezačalo. Pokračujte v rozpracovaném.',
  preparing: 'ČÚZK sestavu o jednotce ještě připravuje. Nic nebylo zaúčtováno; za chvíli zvolte u řádku „dokončit“.',
  fetching: 'Sestava o jednotce se právě vyzvedává. Nic dalšího se neodeslalo.',
};

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
    refetchInterval: (q) => (q.state.data?.running || q.state.data?.purchase?.fetching
      || q.state.data?.units?.some((u) => u.purchase.fetching) ? LIVE_POLL_MS : false),
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
  /* One unit's information (10 Kč): the same two steps, the same server-side run and lease. */
  const [askingUnit, setAskingUnit] = useState<{ id: number; label: string | null; repeat: boolean } | null>(null);
  const unitBuy = useMutation({
    mutationFn: (how: { unit: number; resume?: number; repeat?: boolean; cancel?: number }) => (how.cancel != null
      ? cancelBuildingUnitPurchase(propertyId, how.unit, how.cancel)
      : buyBuildingUnit(propertyId, how.unit, pricesQ.data?.unit_price_czk ?? 0, how)),
    onSuccess: (state) => queryClient.setQueryData<BuildingPrices>(pricesKey(propertyId), state),
    onError: () => void queryClient.invalidateQueries({ queryKey: pricesKey(propertyId) }),
    onSettled: () => setAskingUnit(null),
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
  /* A live request (this tab's or another's) holds the account's ČÚZK login: nothing may be asked beside it. */
  const live = buy.isPending || unitBuy.isPending || !!prices?.running;
  const unitPrice = prices?.unit_price_czk ?? 0;
  const unitsById = new Map((prices?.units ?? []).map((u) => [u.unit_iskn_id, u]));
  /* A fetch was sent moments ago and may still be being served: it is not sent again until that cannot be. */
  const fetching = !!purchase?.fetching;
  const loginUsable = !!prices?.login_set && !prices.login_refused;
  const canPay = !!prices?.available && loginUsable && !live;
  const notice = buy.data?.notice ? NOTICE[buy.data.notice] : undefined;
  const unitNotice = unitBuy.data?.notice ? UNIT_NOTICE[unitBuy.data.notice] ?? NOTICE[unitBuy.data.notice] : undefined;

  let accessory = null;
  if (canPay && inFlight && !fetching) {
    accessory = (
      <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ resume: purchase.id })}>
        {purchase.fetch_sent
          ? `Dokončit načtení (celkem nejvýše ${price} Kč)`
          : `Dokončit načtení z katastru · ${price} Kč`}
      </button>
    );
  } else if (canPay && !inFlight) {
    accessory = (
      <button type="button" className={PANEL_ACTION} disabled={confirming} onClick={() => setConfirming(true)}>
        {paidBefore ? 'Načíst znovu' : 'Načíst ceny'} z katastru · {price} Kč
      </button>
    );
  }
  const note = bought?.finished_at
    ? `ceny z katastru načteny ${fmtDateSlash(bought.finished_at)}`
    : 'registrované prodeje ve stejné budově';
  const boughtNothing = !!bought && (prices?.deeds ?? []).length === 0;

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
      {notice && !live ? <p role="status" className={NOTE}>{notice}</p> : null}
      {fetching && !live ? (
        <p role="status" className={NOTE}>
          Sestava byla u ČÚZK vyžádána před chvílí a možná se ještě vyřizuje; mohla být zaúčtována ({price} Kč).
          Několik minut počkejte: mezitím se nic znovu neodesílá, aby nemohla být zaúčtována dvakrát.
        </p>
      ) : null}
      {inFlight && !live && !fetching ? (
        <p role="status" className={`${NOTE} flex flex-wrap items-center gap-x-3 gap-y-2`}>
          <span className="min-w-0 flex-1">
            {purchase.fetch_sent
              ? `Sestava byla u ČÚZK vyžádána, ale odpověď nedorazila, takže mohla být zaúčtována (${price} Kč). Nezačínejte nový nákup: dokončení načte tutéž sestavu, a pokud už zaúčtována byla, bez dalšího poplatku (ČÚZK účtuje jen první otevření).`
              : `Načtení z katastru je rozpracované: ČÚZK sestavu připravuje, nebo se objednávka nedokončila. Dokončením zaplatíte nejvýše jednou, ${price} Kč.`}
            {' '}Pokud dokončení opakovaně selhává, můžete toto načtení zrušit; tím se nic neodesílá
            {purchase.fetch_sent ? ' a sestava zůstane vedená jako možná zaplacená.' : ' a nic se neúčtuje.'}
          </span>
          <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ cancel: purchase.id })}>
            Zrušit toto načtení
          </button>
        </p>
      ) : null}
      {unread && !live && !fetching ? (
        <p role="status" className={`${NOTE} flex flex-wrap items-center gap-x-3 gap-y-2`}>
          <span className="min-w-0 flex-1">
            {unread.file_kept
              ? 'Sestavu z katastru jste zaplatili, ale nepodařilo se ji přečíst. Je u nás uložená; přečíst ji lze zkusit znovu bez placení.'
              : unread.gone
                ? 'ČÚZK tuto sestavu už nenabízí (je smazaná). Byla u něj vyžádána, takže mohla být zaúčtována; ověříte to ve vyúčtování svého účtu u ČÚZK. Ceny teď získáte jen novým placeným načtením.'
                : `Sestava byla u ČÚZK vyžádána a mohla být zaúčtována, ale nemáme ji. Můžeme ji zkusit vyzvednout znovu: za tuto sestavu nezaplatíte celkem víc než ${price} Kč.`}
          </span>
          {unread.file_kept ? (
            <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ resume: unread.id })}>
              Zkusit přečíst znovu (zdarma)
            </button>
          ) : !unread.gone && loginUsable ? (
            <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ resume: unread.id })}>
              Zkusit vyzvednout znovu (celkem nejvýše {price} Kč)
            </button>
          ) : null}
        </p>
      ) : null}
      {boughtNothing ? (
        <p role="status" className={`${NOTE} flex flex-wrap items-center gap-x-3 gap-y-2`}>
          <span className="min-w-0 flex-1">
            V sestavě z katastru jsme nenašli žádný prodej (načteno {fmtDateSlash(bought.finished_at ?? bought.requested_at)}).
          </span>
          {bought.file_kept && !live ? (
            <button type="button" className={PANEL_ACTION} onClick={() => buy.mutate({ resume: bought.id })}>
              Zkusit přečíst znovu (zdarma)
            </button>
          ) : null}
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
      {askingUnit && canPay && unitPrice > 0 ? (
        <div role="group" aria-label="Potvrzení placeného načtení jednotky"
          className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-[0.78rem] text-[var(--color-ink-2)]">
          <span className="min-w-0 flex-1">
            {askingUnit.repeat
              ? `Za údaje o jednotce ${askingUnit.label ?? ''} jste už jednou platili nebo o ně žádali. Nové načtení stojí dalších ${unitPrice} Kč na vašem účtu u ČÚZK.`
              : `Údaje o jednotce ${askingUnit.label ?? ''} z katastru stojí ${unitPrice} Kč na vašem účtu u ČÚZK.`}
            {' '}Katastr eviduje podlahovou plochu až od roku 2024; u starší jednotky ji uvádět nemusí a platí se i tak.
          </span>
          <button type="button" className={PANEL_ACTION}
            onClick={() => unitBuy.mutate({ unit: askingUnit.id, repeat: askingUnit.repeat })}>
            Zaplatit {unitPrice} Kč a načíst
          </button>
          <button type="button" className={PANEL_ACTION} onClick={() => setAskingUnit(null)}>
            Zrušit
          </button>
        </div>
      ) : null}
      {buy.isError ? <ErrorBanner flush title="Nenačteno:" message={buy.error.message} /> : null}
      {unitBuy.isError ? <ErrorBanner flush title="Jednotka nenačtena:" message={unitBuy.error.message} /> : null}
      {unitNotice ? <p role="status" className={NOTE}>{unitNotice}</p> : null}
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
              {rows.map((row) => (
                <SaleRowView key={row.key} row={row} propertyId={propertyId}
                  unit={row.unit_iskn_id != null ? unitsById.get(row.unit_iskn_id) ?? null : null}
                  ask={canPay && unitPrice > 0 && row.unit_iskn_id != null ? {
                    price: unitPrice,
                    start: (repeat: boolean) => setAskingUnit({ id: row.unit_iskn_id!, label: row.unit, repeat }),
                    resume: (id: number) => unitBuy.mutate({ unit: row.unit_iskn_id!, resume: id }),
                    cancel: (id: number) => unitBuy.mutate({ unit: row.unit_iskn_id!, cancel: id }),
                  } : null} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {showsCadastre(rows) ? (
        <p className="text-xs text-[var(--color-ink-3)]">
          Cena z katastru je za celou listinu a cenová sestava plochu neuvádí. Kde přesně tu cenu před prodejem žádal jediný
          inzerát v tomto domě, je plocha a dispozice převzatá z něj (označeno „inzerát“); jinak je řádek bez ceny za
          m². Ceny z katastru vidí jen váš účet. Zdroj: ČÚZK.
        </p>
      ) : null}
    </Panel>
  );
}

const CELL = 'px-2.5 py-2 whitespace-nowrap';
const LINK = 'underline decoration-[var(--color-rule-strong)] underline-offset-2 hover:text-[var(--color-copper)]';
const NUM = `${CELL} text-right font-mono text-[13px] tabular-nums`;

interface UnitAsk {
  price: number;
  start: (repeat: boolean) => void;
  resume: (purchaseId: number) => void;
  cancel: (purchaseId: number) => void;
}

const UNIT_NOTE = 'ml-1.5 font-sans text-xs text-[var(--color-ink-3)]';
const UNIT_ACT = `ml-1.5 font-sans text-xs ${LINK}`;

/* What the account's purchase of this unit's information says, or the way to it. Every state says truthfully
 * whether money moved and has a way forward: in flight is continued by its id or given up (which sends nothing);
 * paid and unread is read again (free only when its file is kept), or bought anew behind the confirm step. Facts
 * as the cadastre states them: an area it does not record is said so, never shown as a gap that looks unasked. */
function UnitInfo({ unit, ask }: { unit: CadastreUnit | null; ask: UnitAsk | null }) {
  if (unit?.facts) {
    const f = unit.facts;
    const share = f.share_num != null && f.share_den != null ? `podíl ${f.share_num}/${f.share_den}` : null;
    const title = [f.use_label ? `využití: ${f.use_label}` : null, share].filter(Boolean).join(', ') || undefined;
    return (
      <span className={UNIT_NOTE} title={title}>
        {f.floor_area_m2 != null ? `katastr: ${fmtArea(f.floor_area_m2)}`
          : f.area_state === 'empty' ? 'katastr plochu neeviduje' : 'plochu se v sestavě nepodařilo zjistit'}
        {share ? ` · ${share}` : ''}
      </span>
    );
  }
  const p = unit?.purchase;
  if (p?.fetching) {
    return <span className={UNIT_NOTE}>{p.status === 'paid_unread' ? 'vyžádáno, čeká se na odpověď' : 'vyzvedává se…'}</span>;
  }
  if (p && (p.status === 'pending' || p.status === 'ordered')) {
    return ask ? (
      <>
        <button type="button" className={UNIT_ACT} onClick={() => ask.resume(p.id)}>
          dokončit ({p.fetch_sent ? 'celkem nejvýše ' : ''}{ask.price} Kč)
        </button>
        <button type="button" className={UNIT_ACT} onClick={() => ask.cancel(p.id)}
          title={p.fetch_sent ? 'Nic se neodešle; sestava už mohla být zaúčtována' : 'Nic se neodešle a nic se nezaúčtuje'}>
          zrušit
        </button>
      </>
    ) : <span className={UNIT_NOTE}>rozpracováno</span>;
  }
  if (p && p.status === 'paid_unread') {
    const again = p.file_kept ? 'přečíst znovu (zdarma)' : p.gone ? null : `vyzvednout znovu (celkem nejvýše ${ask?.price ?? ''} Kč)`;
    return (
      <>
        <span className={UNIT_NOTE} title="Ověříte to ve vyúčtování svého účtu u ČÚZK">
          {p.cost_czk != null ? 'zaplaceno, nepřečteno' : 'mohlo být zaúčtováno, nepřečteno'}
        </span>
        {ask && again ? (
          <button type="button" className={UNIT_ACT} onClick={() => ask.resume(p.id)}>{again}</button>
        ) : null}
        {ask && !p.file_kept ? (
          <button type="button" className={UNIT_ACT} onClick={() => ask.start(true)}>načíst nově · {ask.price} Kč</button>
        ) : null}
      </>
    );
  }
  if (!ask) return null;
  return (
    <button type="button" className={UNIT_ACT} onClick={() => ask.start(false)}>
      z katastru · {ask.price} Kč
    </button>
  );
}

function SaleRowView({ row, propertyId, unit, ask }: {
  row: SaleRow; propertyId: number; unit: CadastreUnit | null; ask: UnitAsk | null;
}) {
  const link = advertLink(row);
  const price = row.price_czk == null ? '—' : row.currency && row.currency !== 'CZK'
    ? `${row.price_czk.toLocaleString('cs-CZ')} ${row.currency}`
    : fmtCzk(row.price_czk);
  return (
    <tr className="border-b border-[var(--color-rule-soft)] last:border-b-0">
      <td className={`${CELL} font-mono text-[13px] tabular-nums`}>{fmtDateSlash(row.date)}</td>
      <td className={`${CELL} font-mono text-[13px]`}>
        {row.unit ?? <span className="font-sans text-[var(--color-ink-3)]">{row.scope ?? 'celá budova'}</span>}
        {row.also ? <span className="ml-1.5 font-sans text-xs text-[var(--color-ink-3)]">{row.also}</span> : null}
      </td>
      <td className={CELL}>{row.disposition ?? '—'}</td>
      <td className={NUM}>
        {fmtArea(row.area_m2)}
        {row.area_property_id == null ? null : row.area_property_id === propertyId ? (
          <span title="Plocha a dispozice podle inzerátu této nemovitosti, který žádal stejnou cenu"
            className="ml-1.5 font-sans text-xs text-[var(--color-ink-3)]">
            inzerát
          </span>
        ) : (
          <a href={propertyPath(row.area_property_id)} title="Plocha a dispozice podle inzerátu se stejnou cenou"
            className={`ml-1.5 font-sans text-xs ${LINK}`}>
            inzerát
          </a>
        )}
        {row.unit_iskn_id != null ? <UnitInfo unit={unit} ask={ask} /> : null}
      </td>
      <td className={NUM}>{price}</td>
      <td className={NUM}>{fmtMeasuredPricePerM2(row.price_per_m2, ppm2BasisFromToken(row.price_per_m2_basis))}</td>
      <td className={`${CELL} text-[var(--color-ink-2)]`}>
        {row.source === 'katastr' ? (
          <span title={row.proceeding ?? undefined}>Katastr <span className="font-mono text-xs text-[var(--color-ink-3)]">{row.proceeding}</span></span>
        ) : link ? (
          <a href={link} target="_blank" rel="noreferrer" className={LINK}>
            Reas
          </a>
        ) : 'Reas'}
        {row.source === 'reas' && row.proceeding ? (
          <span className="ml-1.5" title="Stejný prodej je i ve vaší sestavě z katastru">
            + katastr <span className="font-mono text-xs text-[var(--color-ink-3)]">{row.proceeding}</span>
          </span>
        ) : null}
      </td>
    </tr>
  );
}
