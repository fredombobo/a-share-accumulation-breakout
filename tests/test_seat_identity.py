"""T05 席位标准化与身份假设：as-of、通道标签、冲突不合并、误合并率。"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from ab_screener.data.migration_intents.lhb_tracking_v2 import apply_lhb_tracking
from ab_screener.data.seat_repository import (
    lookup_as_of,
    lookup_candidates_as_of,
    queue_if_conflict,
    save_hypothesis,
)
from ab_screener.domain.seat_identity import (
    canonical_seat_name,
    classify_official_tag,
    detect_name_conflict,
    hypotheses_from_hm_list,
    hypothesis_from_raw,
    nfkc_name,
    precision_from_labeled_rows,
    precision_report,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lhb" / "seat_aliases.csv"
HM_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lhb" / "hm_list_sample_20260829.json"
TS = "2026-08-10T16:00:00+08:00"
EARLY_TS = "2022-01-01T16:00:00+08:00"


def test_nfkc_and_space_folding():
    assert nfkc_name("某 证　券 深圳益田路营业部") == "某证券深圳益田路营业部"
    assert nfkc_name("机构专用") == nfkc_name("机构专用")


def test_full_legal_branch_name_matches_hm_list_abbreviation():
    full = "华泰证券股份有限公司南京六合雄州西路证券营业部"
    short = "华泰证券南京六合雄州西路"
    assert canonical_seat_name(full) == canonical_seat_name(short)
    assert canonical_seat_name("机构专用席位") == "机构专用"


def test_official_tags_are_channels_not_named_institutions():
    inst = hypothesis_from_raw("机构专用1", event_date="20260810")
    assert inst.official_tag == "INSTITUTION_CHANNEL"
    assert inst.actor_type == "INSTITUTION_CHANNEL"
    assert inst.display_name == "机构专用通道"
    assert "公募" not in inst.display_name
    sh = hypothesis_from_raw("沪股通专用", event_date="20260810")
    assert sh.official_tag == "SH_CONNECT"
    assert sh.actor_type == "CONNECT_CHANNEL"
    assert "单一外资" not in sh.display_name
    sz = hypothesis_from_raw("深股通专用", event_date="20260810")
    assert sz.official_tag == "SZ_CONNECT"
    assert classify_official_tag("某证券深圳益田路营业部") == "BRANCH"
    assert classify_official_tag("某证券总部") == "HQ_NON_BRANCH"


def test_hot_money_is_candidate_not_grade_a():
    hyp = hypothesis_from_raw(
        "某证券上海某路营业部",
        event_date="20260810",
        hm_name="知名游资",
        evidence_grade="A",
    )
    assert hyp.actor_type == "HOT_MONEY_CANDIDATE"
    assert hyp.evidence_grade == "B"
    assert "疑似" in hyp.display_name
    assert "候选" in hyp.display_name


def test_as_of_does_not_use_future_mapping(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "seat.db"))
    try:
        apply_lhb_tracking(conn)
        hyp = hypothesis_from_raw(
            "机构专用",
            event_date="20240102",
            valid_from="20240101",
            valid_to=None,
        )
        save_hypothesis(conn, hyp, available_at=TS, source="manual")
        assert lookup_as_of(conn, alias_raw="机构专用", event_date="20230101") is None
        today = lookup_as_of(conn, alias_raw="机构专用", event_date="20260810")
        assert today is not None
        assert today["official_tag"] == "INSTITUTION_CHANNEL"
        assert today["evidence_source"]
        assert today["evidence_grade"]
        assert "valid_from" in today
    finally:
        conn.close()


def test_conflict_not_auto_merged():
    a = hypothesis_from_raw("某证券深圳益田路营业部", event_date="20260810")
    b = hypothesis_from_raw("另一证券深圳益田路营业部", event_date="20260810")
    # 不同标准名若被赋予同一 seat_id 视为冲突
    from dataclasses import replace

    collided = replace(b, seat_id=a.seat_id)
    assert detect_name_conflict(a, collided) is True
    queued = queue_if_conflict(a, collided)
    assert len(queued) == 2
    assert all(item.conflict for item in queued)


def test_overlapping_alias_to_different_seat_is_visible_conflict(tmp_path: Path):
    from dataclasses import replace

    conn = sqlite3.connect(str(tmp_path / "overlap.db"))
    try:
        apply_lhb_tracking(conn)
        first = hypothesis_from_raw(
            "某证券深圳益田路营业部",
            event_date="20260810",
            valid_from="20200101",
        )
        conflicting = replace(
            first,
            seat_id="manual-review-seat",
            actor_id="manual-review-actor",
            canonical_name="人工复核候选标准名",
            valid_from="20250101",
        )
        save_hypothesis(conn, first, available_at=EARLY_TS, source="manual")
        save_hypothesis(conn, conflicting, available_at=TS, source="manual")
        statuses = conn.execute(
            "SELECT seat_id,conflict_status FROM seat_actor_hypothesis ORDER BY seat_id"
        ).fetchall()
        assert ("manual-review-seat", "OPEN") in statuses
        assert conn.execute("SELECT COUNT(DISTINCT seat_id) FROM seat_alias").fetchone()[0] == 2
    finally:
        conn.close()


def test_precision_reports_coverage_and_mis_merge():
    report = precision_report(true_pairs=100, predicted_pairs=80, false_merges=2)
    assert report["coverage"] == pytest.approx(0.8)
    assert report["mis_merge_rate"] == pytest.approx(0.025)
    assert "mis_merge_rate" in report


def test_precision_gate_uses_reviewed_alias_fixture():
    with FIXTURE.open(encoding="utf-8") as fh:
        report = precision_from_labeled_rows(csv.DictReader(fh))
    assert report["sample_size"] >= 10
    assert report["coverage"] >= 0.8
    assert report["mis_merge_rate"] <= 0.05
    assert report["pass"] is True


def test_hm_list_expands_many_to_many_and_keeps_actor_identity(tmp_path: Path):
    rows = [
        {"name": "人物甲", "orgs": '["华泰证券南京六合雄州西路","国泰君安证券上海江苏路"]'},
        {"name": "人物乙", "orgs": '["华泰证券南京六合雄州西路"]'},
    ]
    hypotheses = hypotheses_from_hm_list(rows, list_date="20260810")
    assert len(hypotheses) == 3
    assert hypotheses[0].actor_id == hypotheses[1].actor_id
    assert hypotheses[0].actor_id != hypotheses[2].actor_id

    conn = sqlite3.connect(str(tmp_path / "many.db"))
    try:
        apply_lhb_tracking(conn)
        for hyp in hypotheses:
            save_hypothesis(conn, hyp, available_at=TS, source="tushare_hm_list")
        candidates = lookup_candidates_as_of(
            conn,
            alias_raw="华泰证券南京六合雄州西路",
            event_date="20260810",
        )
        assert {row["actor_id"] for row in candidates} == {
            hypotheses[0].actor_id,
            hypotheses[2].actor_id,
        }
        assert all(row["evidence_grade"] == "B" for row in candidates)
    finally:
        conn.close()


def test_real_hm_list_orgs_shape_expands_candidate_seats():
    rows = json.loads(HM_FIXTURE.read_text(encoding="utf-8"))
    hypotheses = hypotheses_from_hm_list(rows, list_date="20260829")
    by_name: dict[str, set[str]] = {}
    for hyp in hypotheses:
        by_name.setdefault(hyp.display_name, set()).add(hyp.seat_id)
    assert len(hypotheses) == 4
    assert len(next(seats for name, seats in by_name.items() if "陈小群" in name)) == 3


def test_mapping_changes_append_revision_but_identical_save_is_idempotent(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "revision.db"))
    try:
        apply_lhb_tracking(conn)
        hyp = hypothesis_from_raw(
            "华泰证券南京六合雄州西路",
            event_date="20260810",
            hm_name="人物甲",
        )
        save_hypothesis(conn, hyp, available_at=TS, source="manual", confidence=0.5)
        save_hypothesis(conn, hyp, available_at=TS, source="manual", confidence=0.5)
        save_hypothesis(conn, hyp, available_at=TS, source="manual", confidence=0.8)
        revisions = conn.execute(
            "SELECT revision,confidence FROM seat_actor_hypothesis ORDER BY revision"
        ).fetchall()
        assert revisions == [(1, 0.5), (2, 0.8)]
    finally:
        conn.close()


def test_future_knowledge_does_not_backfill_hot_money_identity(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "knowledge.db"))
    try:
        apply_lhb_tracking(conn)
        official = hypothesis_from_raw(
            "华泰证券南京六合雄州西路",
            event_date="20260810",
            valid_from="20200101",
        )
        hot = hypothesis_from_raw(
            "华泰证券南京六合雄州西路",
            event_date="20260810",
            hm_name="人物甲",
            valid_from="20200101",
        )
        save_hypothesis(conn, official, available_at=EARLY_TS, source="official")
        save_hypothesis(
            conn,
            hot,
            available_at="2026-08-11T16:00:00+08:00",
            source="hm_list",
        )
        historical = lookup_candidates_as_of(
            conn,
            alias_raw=official.seat_raw,
            event_date="20260810",
        )
        assert all(row["actor_id"] != hot.actor_id for row in historical)
        later_known = lookup_candidates_as_of(
            conn,
            alias_raw=official.seat_raw,
            event_date="20260810",
            knowledge_as_of="2026-08-12T16:00:00+08:00",
        )
        assert any(row["actor_id"] == hot.actor_id for row in later_known)
    finally:
        conn.close()


def test_second_alias_for_same_seat_does_not_conflict(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "alias2.db"))
    try:
        apply_lhb_tracking(conn)
        a = hypothesis_from_raw("某证券深圳益田路营业部", event_date="20260810")
        b = hypothesis_from_raw("某证券深圳益田路证券营业部", event_date="20260810")
        assert a.seat_id == b.seat_id
        save_hypothesis(conn, a, available_at=TS, source="manual")
        save_hypothesis(conn, b, available_at=TS, source="manual")
        aliases = {
            r[0]
            for r in conn.execute("SELECT alias_raw FROM seat_alias WHERE seat_id=?", (a.seat_id,))
        }
        assert aliases == {a.seat_raw, b.seat_raw}
        assert conn.execute("SELECT COUNT(*) FROM seat_master WHERE seat_id=?", (a.seat_id,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_as_of_filters_master_and_hypothesis_validity(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "asof.db"))
    try:
        apply_lhb_tracking(conn)
        conn.execute(
            "INSERT INTO seat_master (seat_id, revision, canonical_name, official_tag,"
            " valid_from, valid_to, source, available_at, ingested_at, content_hash, payload_json)"
            " VALUES ('seat-x',1,'机构专用','INSTITUTION_CHANNEL','20240101',NULL,'m',?,?, 'h','{}')",
            (TS, TS),
        )
        conn.execute(
            "INSERT INTO seat_alias (alias_raw, seat_id, revision, valid_from, valid_to, source,"
            " available_at, ingested_at, content_hash) VALUES ('机构专用','seat-x',1,'19900101',NULL,'m',?,?, 'h')",
            (TS, TS),
        )
        conn.execute(
            "INSERT INTO actor_master (actor_id,revision,actor_type,display_name,valid_from,valid_to,"
            " source,available_at,ingested_at,content_hash,payload_json) VALUES"
            " ('act-x',1,'UNKNOWN','候选','20240101',NULL,'m',?,?, 'h','{}')",
            (TS, TS),
        )
        conn.execute(
            "INSERT INTO seat_actor_hypothesis (seat_id, actor_id, revision, valid_from, valid_to,"
            " confidence, evidence_grade, evidence_source, conflict_status, source, available_at,"
            " ingested_at, content_hash, payload_json) VALUES"
            " ('seat-x','act-x',1,'20240101',NULL,0.5,'A','future_map','NONE','m',?,?, 'h','{}')",
            (TS, TS),
        )
        conn.commit()
        assert lookup_as_of(conn, alias_raw="机构专用", event_date="20230101") is None
        today = lookup_as_of(conn, alias_raw="机构专用", event_date="20260810")
        assert today is not None
        assert today["evidence_source"] == "future_map"
    finally:
        conn.close()


def test_as_of_does_not_leak_future_hypothesis(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "leak.db"))
    try:
        apply_lhb_tracking(conn)
        conn.execute(
            "INSERT INTO seat_master (seat_id, revision, canonical_name, official_tag,"
            " valid_from, valid_to, source, available_at, ingested_at, content_hash, payload_json)"
            " VALUES ('seat-y',1,'机构专用','INSTITUTION_CHANNEL','19900101',NULL,'m',?,?, 'h','{}')",
            (EARLY_TS, EARLY_TS),
        )
        conn.execute(
            "INSERT INTO seat_alias (alias_raw, seat_id, revision, valid_from, valid_to, source,"
            " available_at, ingested_at, content_hash) VALUES ('机构专用','seat-y',1,'19900101',NULL,'m',?,?, 'h')",
            (EARLY_TS, EARLY_TS),
        )
        conn.execute(
            "INSERT INTO actor_master (actor_id,revision,actor_type,display_name,valid_from,valid_to,"
            " source,available_at,ingested_at,content_hash,payload_json) VALUES"
            " ('act-y',1,'UNKNOWN','候选','20240101',NULL,'m',?,?, 'h','{}')",
            (TS, TS),
        )
        conn.execute(
            "INSERT INTO seat_actor_hypothesis (seat_id, actor_id, revision, valid_from, valid_to,"
            " confidence, evidence_grade, evidence_source, conflict_status, source, available_at,"
            " ingested_at, content_hash, payload_json) VALUES"
            " ('seat-y','act-y',1,'20240101',NULL,0.9,'A','leaked','NONE','m',?,?, 'h','{}')",
            (TS, TS),
        )
        conn.commit()
        past = lookup_as_of(conn, alias_raw="机构专用", event_date="20230101")
        assert past is not None
        assert past["evidence_source"] is None
        now = lookup_as_of(conn, alias_raw="机构专用", event_date="20260810")
        assert now["evidence_source"] == "leaked"
    finally:
        conn.close()


def test_alias_fixture_roundtrip(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "alias.db"))
    try:
        apply_lhb_tracking(conn)
        with FIXTURE.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                hyp = hypothesis_from_raw(
                    row["alias_raw"],
                    event_date="20260810",
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"] or None,
                )
                save_hypothesis(conn, hyp, available_at=TS, source="fixture")
        found = lookup_as_of(conn, alias_raw="机构专用", event_date="20260810")
        assert found is not None
        assert found["conflict_status"] in {"NONE", "OPEN", "RESOLVED"}
    finally:
        conn.close()
