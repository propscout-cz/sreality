"""The headline price change ignores a placeholder price (operator ruling 2026-10-26): a price
under its deal's floor (toolkit.measures: 10 000 Kč capital, 500 Kč rent) neither opens the
span nor closes it, so a 1 Kč stand-in followed by the real price is no change, and a drop to
"price on request" is no −100 %. Executed through the rollup on CI's replayed schema; every
test rolls back.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tests._live_property import REQUIRED_DB
from tests.test_merge_safety_live import _advert, _property, _recompute, _snapshot

pytestmark = REQUIRED_DB


def _total(cur: Any, pid: int) -> Decimal | None:
    _recompute(cur, pid)
    cur.execute("SELECT round(total_price_change_pct, 2) FROM properties WHERE id = %s", (pid,))
    return cur.fetchone()[0]


def test_a_placeholder_first_price_opens_no_span(cur):
    pid = _property(cur)
    ad = _advert(cur, pid, source="sreality", price=5_000_000)
    _snapshot(cur, ad, 1, 5)
    _snapshot(cur, ad, 5_000_000, 1)
    assert _total(cur, pid) is None


def test_a_placeholder_between_two_real_prices_is_skipped(cur):
    pid = _property(cur)
    ad = _advert(cur, pid, source="sreality", price=4_500_000)
    _snapshot(cur, ad, 5_000_000, 5)
    _snapshot(cur, ad, 1, 3)
    _snapshot(cur, ad, 4_500_000, 1)
    assert _total(cur, pid) == Decimal("-10.00")


def test_a_placeholder_shown_price_is_no_change(cur):
    pid = _property(cur)
    ad = _advert(cur, pid, source="sreality", price=0)
    _snapshot(cur, ad, 5_000_000, 5)
    _snapshot(cur, ad, 0, 1)
    assert _total(cur, pid) is None


def test_a_rent_floor_is_its_own(cur):
    """400 Kč a month is a stand-in, 18 000 Kč is a rent: the span opens at 20 000."""
    pid = _property(cur)
    ad = _advert(cur, pid, source="sreality", price=18_000)
    cur.execute("UPDATE listings SET category_type = 'pronajem' WHERE id = %s", (ad,))
    _snapshot(cur, ad, 400, 5)
    _snapshot(cur, ad, 20_000, 3)
    _snapshot(cur, ad, 18_000, 1)
    assert _total(cur, pid) == Decimal("-10.00")


def test_real_prices_keep_the_change_as_before(cur):
    pid = _property(cur)
    ad = _advert(cur, pid, source="sreality", price=4_750_000)
    _snapshot(cur, ad, 5_000_000, 5)
    _snapshot(cur, ad, 4_750_000, 1)
    assert _total(cur, pid) == Decimal("-5.00")
