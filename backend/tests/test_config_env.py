# -*- coding: utf-8 -*-
"""環境変数の命名統一・既定値・不正値の扱いに関するtest。"""

import pytest

import config
import finance


@pytest.fixture(autouse=True)
def _isolated_adjustments(monkeypatch):
    monkeypatch.setattr(config, "ENV_ADJUSTMENTS", [])


def test_out_of_range_value_is_recorded_not_silently_clamped(monkeypatch):
    monkeypatch.setenv("CLAGE_HTTP_TIMEOUT_SEC", "999999")

    value = config._env_float("CLAGE_HTTP_TIMEOUT_SEC", 600.0, 5.0, 1800.0)

    assert value == 1800.0
    adjustment = config.env_adjustment_for("CLAGE_HTTP_TIMEOUT_SEC")
    assert adjustment is not None
    assert adjustment["requested"] == "999999"
    assert adjustment["effective"] == "1800.0"
    assert adjustment["reason"].startswith("outside_allowed_range")


def test_unparsable_value_is_recorded_not_silently_defaulted(monkeypatch):
    monkeypatch.setenv("CLAGE_HISTORY_TURNS", "ten")

    assert config._env_int("CLAGE_HISTORY_TURNS", 10, 0, 50) == 10
    assert config.env_adjustment_for("CLAGE_HISTORY_TURNS")["reason"] == "not_an_integer"


def test_valid_value_within_range_is_not_recorded(monkeypatch):
    monkeypatch.setenv("CLAGE_HISTORY_TURNS", "7")

    assert config._env_int("CLAGE_HISTORY_TURNS", 10, 0, 50) == 7
    assert config.env_adjustment_for("CLAGE_HISTORY_TURNS") is None


def test_invalid_bool_is_recorded_and_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CLAGE_LIVE_API_ENABLED", "perhaps")

    assert config._env_bool("CLAGE_LIVE_API_ENABLED", False) is False
    assert (
        config.env_adjustment_for("CLAGE_LIVE_API_ENABLED")["reason"]
        == "not_a_boolean"
    )


def test_invalid_budget_policy_is_not_an_in_band_sentinel(monkeypatch, tmp_path):
    """"invalid" という帯域内の魔法値をやめ、記録経由で起動を止める。"""
    monkeypatch.setenv("CLAGE_BUDGET_UNKNOWN_POLICY", "maybe")

    policy = config._env_choice(
        "CLAGE_BUDGET_UNKNOWN_POLICY",
        "block",
        frozenset({"block", "allow"}),
    )

    assert policy == "block"  # 安全側の既定へ落ちる(allowにはしない)
    monkeypatch.setattr(config, "BUDGET_UNKNOWN_POLICY", policy)
    monkeypatch.setattr(config, "PRICE_TABLE_FILE", "")
    monkeypatch.setattr(config, "PER_RUN_BUDGET_USD", "")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", "")
    monkeypatch.setattr(config, "BUDGET_UTC_OFFSET", "+00:00")
    guard = finance.BudgetGuard(tmp_path / "data")

    with pytest.raises(finance.FinanceConfigurationError):
        guard.validate_configuration()


def test_renamed_environment_variables_are_detected(monkeypatch):
    monkeypatch.setenv("HISTORY_TURNS", "5")
    monkeypatch.delenv("CLAGE_HISTORY_TURNS", raising=False)

    deprecated = dict(config.deprecated_env_names())

    assert deprecated["HISTORY_TURNS"] == "CLAGE_HISTORY_TURNS"


def test_new_name_takes_precedence_and_silences_the_rename_warning(monkeypatch):
    monkeypatch.setenv("HISTORY_TURNS", "5")
    monkeypatch.setenv("CLAGE_HISTORY_TURNS", "7")

    assert "HISTORY_TURNS" not in dict(config.deprecated_env_names())


def test_only_vendor_names_stay_unprefixed():
    """新旧表に載る全ての設定名がCLAGE_接頭辞を持つこと。"""
    for new_name in config.RENAMED_ENV_NAMES.values():
        assert new_name.startswith("CLAGE_")
    for vendor_name in config.VENDOR_ENV_NAMES:
        assert not vendor_name.startswith("CLAGE_")


def test_http_timeout_default_leaves_room_for_high_tier_reasoning():
    """タイムアウト=課金済みで成果ゼロ。retriesが0既定なので既定を長くする。"""
    assert config.HTTP_TIMEOUT_SEC >= 600.0
    assert config.HTTP_RETRIES == 0
