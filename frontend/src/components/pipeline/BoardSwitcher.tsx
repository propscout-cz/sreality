/* The Pipeline tab's board switcher (migration 641, design §3.5): "Můj
 * pipeline" plus every board shared with me, labelled `email · jen ke čtení |
 * úpravy`. The choice is the per-device ACTIVE BOARD (lib/acting); the page
 * renders a shared one inside <ActingAs>. Drawn only when there is something
 * to switch between. One component for the desktop header (inline, beside the
 * page title) and the phone's strip; only the chip size differs. */

import { PickButton } from '@/components/controls';
import { resetActiveBoard, setActiveBoard, sharedBoardLabel, type ActiveBoard } from '@/lib/acting';
import type { PipelineShare } from '@/lib/types';

const BOARD_SWITCHER_LABEL = 'Zobrazený pipeline';
const OWN_BOARD_LABEL = 'Můj pipeline';

export default function BoardSwitcher({
  boards,
  active,
  size = 'desktop',
}: {
  /* GET /pipeline/shares' `shared_with_me`. */
  boards: readonly PipelineShare[];
  active: ActiveBoard;
  size?: 'desktop' | 'phone';
}) {
  if (boards.length === 0 && active == null) return null;
  /* Until the lists load, the remembered board is still a choice on screen. */
  const shown =
    active != null && !boards.some((b) => b.account_id === active.account_id) ? [active, ...boards] : boards;
  const chip = size === 'phone' ? 'shrink-0 whitespace-nowrap h-9 px-3 text-[13px]' : 'whitespace-nowrap';
  return (
    <div
      role="group"
      aria-label={BOARD_SWITCHER_LABEL}
      className={
        size === 'phone'
          ? 'flex gap-1.5 overflow-x-auto px-4 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden'
          : 'flex flex-wrap gap-1'
      }
    >
      <PickButton on={active == null} onClick={resetActiveBoard} className={chip}>
        {OWN_BOARD_LABEL}
      </PickButton>
      {shown.map((b) => (
        <PickButton
          key={b.account_id}
          on={active?.account_id === b.account_id}
          onClick={() => setActiveBoard(b)}
          className={chip}
        >
          {sharedBoardLabel(b)}
        </PickButton>
      ))}
    </div>
  );
}
