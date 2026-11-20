#!/usr/bin/env python3
"""tests/test_kaizen_self_improvement.py: Tests for Kaizen self-improvement and quarantine gate."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from engine import kaizen_self_improvement as kaizen
from engine.kaizen_self_improvement import check_kaizen_quarantine


def test_kaizen_macro_veto_oil_longs():
    """Kaizen must always veto counter-trend oil longs during macro bear regime."""
    is_q, reason = check_kaizen_quarantine("USWTI.p", "5M", "EMA20_BOUNCE_BULLISH")
    assert is_q is True
    assert "oil" in reason.lower()

    is_q, reason = check_kaizen_quarantine("USWTI.p", "1H", "VWAP_0_LONG")
    assert is_q is True
    assert "oil" in reason.lower()


def test_kaizen_macro_veto_gold_1h():
    """Kaizen must veto 1H gold setups to prevent oversized dollar stop distances."""
    is_q, reason = check_kaizen_quarantine("XAUUSD.pi", "1H", "VWAP_M2_LONG")
    assert is_q is True
    assert "gold" in reason.lower()


def test_kaizen_permits_valid_champions():
    """Kaizen must permit prime champion setups (e.g. EURUSD 15M, USWTI Short)."""
    is_q, reason = check_kaizen_quarantine("EURUSD.pi", "15M", "VWAP_P1_LONG")
    assert is_q is False

    is_q, reason = check_kaizen_quarantine("USWTI.p", "5M", "EMA20_REJECTION_SHORT")
    assert is_q is False


def test_kaizen_ledger_and_quarantine_generation(tmp_path, monkeypatch):
    """Exercise attribution without connecting to a broker or rewriting runtime files."""
    quarantine = tmp_path / "quarantine.json"
    ledger = tmp_path / "ledger.json"
    for name, path in {
        "KAIZEN_QUARANTINE_FILE": quarantine,
        "KAIZEN_LEDGER_FILE": ledger,
        "KAIZEN_CHAMPIONS_FILE": tmp_path / "champions.json",
        "LIVE_LEDGER_FILE": tmp_path / "execution.csv",
    }.items():
        monkeypatch.setattr(kaizen, name, path)

    broker = Mock()
    broker.initialize.return_value = True
    broker.history_deals_get.return_value = [
        SimpleNamespace(
            entry=1, position_id=12, magic=42, symbol="EURUSD.pi",
            comment="Alpha_example", profit=2.0, time=1700000000,
        )
    ]
    monkeypatch.setattr(kaizen, "mt5", broker)
    result = kaizen.evaluate_kaizen_trade_attribution()
    assert result["total_alpha_trades"] == 1
    assert json.loads(ledger.read_text(encoding="utf-8"))["total_alpha_trades"] == 1
    payload = json.loads(quarantine.read_text(encoding="utf-8"))
    assert payload["quarantine_map"] == {}
    assert payload["macro_vetoes"]
    broker.shutdown.assert_called_once()
