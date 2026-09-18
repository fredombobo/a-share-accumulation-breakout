"""An explicitly selected local dataset cannot fall through to production/network."""
import pandas as pd
import pytest

from ab_screener.screener.data_loader import load_market_data
from ab_screener.screener.prefilter import prefilter


def test_failed_local_loader_stops_scan(monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError('selected database unavailable')
    monkeypatch.setattr('ab_screener.data.market_loader.load_market_for_scan', failed)
    with pytest.raises(RuntimeError, match='selected database'):
        load_market_data(160, db_path='explicit-test.db')


def test_empty_local_loader_stays_empty(monkeypatch):
    empty = pd.DataFrame()
    monkeypatch.setattr('ab_screener.data.market_loader.load_market_for_scan', lambda *a, **kw: (empty, [], empty, empty, empty, {}))
    assert load_market_data(160, db_path='explicit-test.db')[2].empty


def test_listing_age_is_relative_to_signal_date():
    basic = pd.DataFrame([{'ts_code': '000001.SZ', 'name': '测试', 'list_date': '20260101'}])
    assert prefilter(basic, pd.DataFrame(), as_of='20260201').empty
    assert len(prefilter(basic, pd.DataFrame(), as_of='20260911')) == 1
