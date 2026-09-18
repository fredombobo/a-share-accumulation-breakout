"""Daily nine-field saves preserve server exits and never inherit research validation."""
from dataclasses import replace

import pytest

from ab_screener.application import strategy_profile_service as service
from ab_screener.data.strategy_profile_repository import (
    StrategyProfileRepository,
    StrategyProfileRepositoryError,
)
from ab_screener.domain.profile import default_profile
from ab_screener.research.store import ResearchRunStore
from tests.test_strategy_profile_closed_loop import _client, _completed_task


def seed(path):
    profile = replace(default_profile(), profile_id="exit-reference", version="preserved-v1",
                      vol_ratio_min=1.7, strong_reset=4, exit_window=12,
                      stop_pct=0.005, target_pct=0.19, max_hold_days=45)
    StrategyProfileRepository(path).activate(profile)
    return profile


def save(client, entry, config_hash=None, **extra):
    return client.post("/api/backtest/profile/entry", json={
        "entry": entry, "expected_config_hash": config_hash,
        "acknowledge_research_only": True, **extra,
    })


def test_entry_save_preserves_all_effective_exits_and_is_idempotent(tmp_path):
    path = tmp_path / "entry.db"
    client = _client(path)
    previous = seed(path)
    entry = {**previous.signal_kwargs(), "box_max_days": 180}
    response = save(client, entry, previous.config_hash())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["active"]["entry"] == entry
    assert result["active"]["exit_reference"] == previous.exit_params()
    assert result["backtest_validated"] is False
    again = save(client, entry, result["active"]["config_hash"])
    assert again.status_code == 200
    assert again.json()["idempotent"] is True
    assert again.json()["active"]["config_hash"] == result["active"]["config_hash"]
    stale = save(client, entry, previous.config_hash())
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "PROFILE_CHANGED"


@pytest.mark.parametrize("patch", [
    {"stop_pct": 0.2}, {"box_min_days": "60"}, {"box_min_days": True},
    {"require_structure": 1}, {"box_min_days": 1.5}, {"box_max_amp": -0.1},
])
def test_entry_endpoint_rejects_nonentry_or_invalid_values(tmp_path, patch):
    client = _client(tmp_path / "invalid.db")
    response = save(client, {**default_profile().signal_kwargs(), **patch})
    assert response.status_code == 422, response.text
    assert client.get("/api/backtest/profile").json()["active"]["is_default"]


def test_missing_entry_and_root_exit_are_rejected(tmp_path):
    client = _client(tmp_path / "missing.db")
    entry = default_profile().signal_kwargs()
    assert save(client, entry, exit_reference={"stop_pct": 0.2}).status_code == 422
    entry.pop("require_structure")
    assert save(client, entry).status_code == 422
    with pytest.raises(service.ProfileActivationError):
        service._validated_daily_entry({**default_profile().signal_kwargs(), "box_max_amp": float("nan")})


def test_cas_inside_transaction_preserves_concurrent_exit_change(tmp_path, monkeypatch):
    path = tmp_path / "race.db"
    _client(path)
    previous = seed(path)
    newer = replace(previous, version="concurrent-exit", stop_pct=0.09)
    original = StrategyProfileRepository.activate
    injected = False

    def race(repo, profile, *, expected_config_hash=None):
        nonlocal injected
        if not injected:
            injected = True
            original(StrategyProfileRepository(path), newer)
        return original(repo, profile, expected_config_hash=expected_config_hash)

    monkeypatch.setattr(StrategyProfileRepository, "activate", race)
    with pytest.raises(StrategyProfileRepositoryError) as exc:
        service.save_daily_entry(path, {**previous.signal_kwargs(), "box_max_days": 180},
                                 acknowledge_research_only=True)
    assert exc.value.code == "PROFILE_CHANGED"
    assert StrategyProfileRepository(path).effective()["profile"].config_hash() == newer.config_hash()


def test_copy_weak_stale_result_is_explicitly_unvalidated_and_cannot_bypass_activation(tmp_path, monkeypatch):
    path = tmp_path / "copy.db"
    client = _client(path)
    previous = seed(path)
    task = _completed_task(path, "weak-copy", verdict="EXPLORATORY_WEAK",
                           code_version="old-code", dataset_version="old-data")
    monkeypatch.setattr(service, "build_version", lambda: "new-code")
    monkeypatch.setattr(service, "latest_research_cutoff", lambda _: "new-data")
    response = client.post("/api/backtest/profile/copy-entry", json={
        "task_id": task["task_id"], "expected_config_hash": previous.config_hash(),
        "acknowledge_research_only": True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["active"]["entry"] == task["result"]["selected"]["signal"]
    assert result["active"]["exit_reference"] == previous.exit_params()
    assert result["active"]["source"]["kind"] == "BACKTEST_ENTRY_COPY"
    assert result["backtest_validated"] is False
    status = client.get("/api/backtest/status/weak-copy").json()
    assert status["entry_copy"]["can_copy"]
    assert not status["profile_activation"]["can_activate"]
    activation = client.post("/api/backtest/profile/activate", json={
        "task_id": "weak-copy", "acknowledge_exploratory": True,
    })
    assert activation.status_code == 409
    assert StrategyProfileRepository(path).effective()["profile"].source_kind == "BACKTEST_ENTRY_COPY"


@pytest.mark.parametrize("mutation", ["mechanism", "unfinished", "missing_signal", "malformed"])
def test_copy_rejects_inexpressible_or_unfinished_source(tmp_path, mutation):
    path = tmp_path / "source.db"
    client = _client(path)
    task = _completed_task(path, "source", research_mechanism=mutation == "mechanism")
    if mutation == "unfinished":
        task = ResearchRunStore(path).update("source", status="running")
    if mutation == "missing_signal":
        task["result"]["selected"]["signal"].pop("box_min_days")
    if mutation == "malformed":
        task["result"]["selected"] = []
    assert not service.entry_copy_status(task)["can_copy"]
    if mutation in {"mechanism", "unfinished"}:
        assert client.post("/api/backtest/profile/copy-entry", json={
            "task_id": "source", "acknowledge_research_only": True,
        }).status_code == 409


def test_entry_hash_excludes_exit_top_and_source_identity():
    base = default_profile()
    unrelated = replace(base, version="different", stop_pct=0.2, top_n_trade=3,
                        source_kind="BACKTEST_ENTRY_COPY", source_task_id="task-x")
    changed = replace(base, breakout_vol_ratio=base.breakout_vol_ratio + 0.1)
    assert len(base.entry_hash()) == 64
    assert unrelated.entry_hash() == base.entry_hash()
    assert unrelated.config_hash() != base.config_hash()
    assert changed.entry_hash() != base.entry_hash()
