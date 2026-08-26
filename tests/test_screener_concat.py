"""候选表合并在 pandas 版本升级下保持 schema 与零警告。"""
from __future__ import annotations

import warnings

import pandas as pd

from ab_screener.screener.orchestrator import _concat_candidate_frames


def test_concat_candidate_frames_preserves_all_na_columns_without_warning():
    left = pd.DataFrame(
        {"ts_code": ["000001.SZ"], "score": [None], "left_only": [1]}
    )
    right = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "score": [2.0],
            "left_only": [None],
            "all_missing": [None],
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        result = _concat_candidate_frames(left, right)

    assert result["ts_code"].tolist() == ["000001.SZ", "600000.SH"]
    assert list(result.columns) == [
        "ts_code",
        "score",
        "left_only",
        "all_missing",
    ]
    assert result["all_missing"].isna().all()
