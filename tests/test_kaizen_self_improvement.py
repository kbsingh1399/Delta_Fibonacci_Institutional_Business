#!/usr/bin/env python3
"""tests/test_kaizen_self_improvement.py: Tests for Kaizen self-improvement and quarantine gate."""

import os
import json
import pytest
from engine.kaizen_self_improvement import (
    check_kaizen_quarantine,
    evaluate_kaizen_trade_attribution,
    KAIZEN_QUARANTINE_FILE,
    KAIZEN_LEDGER_FILE
)


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


def test_kaizen_ledger_and_quarantine_generation():
    """Evaluates Kaizen trade attribution generator and verifies JSON files."""
    res = evaluate_kaizen_trade_attribution()
    assert res.get("ok") is not False
    assert os.path.exists(KAIZEN_QUARANTINE_FILE)
    assert os.path.exists(KAIZEN_LEDGER_FILE)

    with open(KAIZEN_QUARANTINE_FILE, "r", encoding="utf-8") as f:
        q_data = json.load(f)
        assert "quarantine_map" in q_data
        assert "macro_vetoes" in q_data
