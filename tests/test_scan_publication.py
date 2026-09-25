"""A scan success publishes exactly its own payload, even when empty."""
import sqlite3

import pandas as pd
import pytest

from ab_screener.application.scan_audit import complete_scan_run
from ab_screener.application.scan_jobs import ScanJobStore
from ab_screener.application.scan_publication import read_scan_publication
from ab_screener.domain.profile import default_profile
from local_store import LocalStore


@pytest.fixture
def market(tmp_path):
    return LocalStore(tmp_path / 'publication.db')


def candidate(date='20260911', code='000001.SZ'):
    return {'trade_date': date, 'ts_code': code, 'name': '测试', 'reasons': '[池B|relaxed|] test', 'total_score': 80.0}


def publish(store, task, rows, date='20260911', **overrides):
    jobs = ScanJobStore(store.db_path)
    jobs.reserve_running(task, top_n=15, days=160)
    profile = default_profile()
    args = {'run_id': task, 'task_id': task, 'as_of': date, 'days': 160,
                'result': {'scan_candidates': rows, 'freshness': {'can_publish_a': True}, 'hits': len(rows)},
                'count_a': 0, 'count_b': len(rows), 'strategy_snapshot': profile.to_canonical_dict(),
                'config_hash': profile.config_hash(), 'code_version': 'test', 'research_mode': 'test'}
    args.update(overrides)
    return complete_scan_run(store.db_path, **args)


@pytest.mark.parametrize('date', ['20260911', '20260914'])
def test_empty_success_replaces_latest_without_reusing_old_rows(market, date):
    publish(market, 'first', [candidate()])
    publish(market, 'empty', [], date=date)
    latest = read_scan_publication(market.db_path)
    assert latest['run_id'] == 'empty'
    assert latest['as_of'] == date
    assert latest['verified'] and latest['state'] == 'READY'
    assert latest['candidates'] == []
    assert market.load_scan_result().empty
    assert len(read_scan_publication(market.db_path, 'first')['candidates']) == 1


def test_owned_rows_ignore_contaminated_shared_table(market):
    market.upsert_scan_result(pd.DataFrame([candidate(code='000999.SZ')]))
    publish(market, 'owned', [candidate(code='000002.SZ')])
    assert [r['ts_code'] for r in read_scan_publication(market.db_path)['candidates']] == ['000002.SZ']
    assert market.load_scan_result('20260911')['ts_code'].tolist() == ['000002.SZ']


def test_failed_audit_rolls_back_projection_and_success(market):
    publish(market, 'original', [candidate()])
    with sqlite3.connect(market.db_path) as conn:
        conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON scan_runs WHEN NEW.run_id='broken' BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        publish(market, 'broken', [candidate(code='000002.SZ')])
    assert read_scan_publication(market.db_path)['run_id'] == 'original'
    assert market.load_scan_result('20260911')['ts_code'].tolist() == ['000001.SZ']
    assert ScanJobStore(market.db_path).get('broken')['status'] == 'RUNNING'


def test_cancel_prevents_empty_publication(market):
    publish(market, 'original', [candidate()])
    jobs = ScanJobStore(market.db_path)
    jobs.reserve_running('cancel', top_n=15, days=160)
    jobs.request_cancel('cancel')
    profile = default_profile()
    assert not complete_scan_run(market.db_path, run_id='cancel', task_id='cancel', as_of='20260911', days=160,
                                 result={'scan_candidates': []}, count_a=0, count_b=0,
                                 strategy_snapshot=profile.to_canonical_dict(), config_hash=profile.config_hash(),
                                 code_version='test', research_mode='test')
    assert read_scan_publication(market.db_path)['run_id'] == 'original'
    assert jobs.get('cancel')['status'] == 'CANCELLED'


@pytest.mark.parametrize('rows,count_a,count_b', [([candidate(), candidate()], 0, 2), ([candidate('20260910')], 0, 1), ([candidate()], 1, 0)])
def test_mismatched_payload_rejected(market, rows, count_a, count_b):
    with pytest.raises(ValueError):
        publish(market, 'invalid', rows, count_a=count_a, count_b=count_b)
    assert read_scan_publication(market.db_path) is None


def test_stale_strict_cannot_be_published(market):
    row = {**candidate(), 'reasons': '[池A|strict|] test'}
    with pytest.raises(ValueError, match='verification'):
        publish(market, 'stale', [row], count_a=1, count_b=0,
                result={'scan_candidates': [row], 'freshness': {'can_publish_a': False}})


def test_manifest_identity_is_stable_across_connections(market):
    from ab_screener.application.scan_audit import read_dataset_version
    with sqlite3.connect(market.db_path) as conn:
        conn.execute("INSERT INTO dataset_partitions(dataset,trade_date,row_count,content_sha256,revision,ingested_at) VALUES ('daily','20260911',1,'abc',1,'2026-09-11')")
    assert read_dataset_version(market.db_path) == read_dataset_version(market.db_path)


def test_changed_data_manifest_prevents_success(market):
    with pytest.raises(ValueError, match='data manifest changed'):
        publish(market, 'changed', [], result={'scan_candidates': [], 'input_dataset_version': 'old'})
    assert read_scan_publication(market.db_path) is None
