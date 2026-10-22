"""What the docs symbol check treats as a code symbol."""

from __future__ import annotations

from scripts.docs_symbol_check import symbols_in


def test_camel_case_hit() -> None:
    assert symbols_in("every card write is `usePipelineCard` over `useOptimisticWrite()`") == [
        "usePipelineCard",
        "useOptimisticWrite",
    ]


def test_snake_case_hit_with_call_args() -> None:
    assert symbols_in("created through `create_estimation_run(conn, source='ui')`") == ["create_estimation_run"]


def test_url_param_like_token_hit() -> None:
    assert symbols_in("the map's `point_budget` shapes the answer") == ["point_budget"]


def test_path_and_sentence_skipped() -> None:
    assert symbols_in("see `toolkit/property_identity.py` and `merge it when green`") == []


def test_other_shapes_skipped() -> None:
    line = "`?deal=any` `merge_group_id=` `db.gone_evidence` `PROPERTY_CARRIERS` `browse` `a-b_c` `<level>_id`"
    assert symbols_in(line) == []


def test_duplicates_collapse() -> None:
    assert symbols_in("`is_active` then `is_active` again") == ["is_active"]
