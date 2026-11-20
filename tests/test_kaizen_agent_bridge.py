#!/usr/bin/env python3
"""
tests/test_kaizen_agent_bridge.py
================================================================================
UNIT TESTS FOR KAIZEN AGENT INTEGRATION BRIDGE
================================================================================
Verifies that the Kaizen Agent framework (Kaizen-agent/kaizen-agent) is cleanly
integrated, executes YAML-driven behavioral evaluations, and enforces trading desk
invariants.
"""

import os
import yaml
import pytest
from pathlib import Path

from engine.kaizen_self_improvement import KaizenTradingAgent, check_kaizen_quarantine
from engine.kaizen_agent_bridge import run_kaizen_agent_suite, format_kaizen_agent_report_section


def test_kaizen_agent_package_installed():
    """Verify kaizen-agent package is installed and importable."""
    import kaizen
    assert hasattr(kaizen, "__file__"), "kaizen package missing __file__ attribute"


def test_kaizen_yaml_configuration_validity():
    """Verify kaizen.yaml exists and contains valid required schema."""
    config_path = Path("kaizen.yaml")
    assert config_path.exists(), "kaizen.yaml must exist in workspace root"
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    assert config.get("language") == "python", "language must be python"
    assert "agent" in config, "agent block missing"
    assert config["agent"].get("module") == "engine.kaizen_self_improvement"
    assert config["agent"].get("class") == "KaizenTradingAgent"
    assert config["agent"].get("method") == "evaluate_setup"
    assert len(config.get("steps", [])) >= 5, "Must have at least 5 test steps"


def test_kaizen_trading_agent_class():
    """Verify KaizenTradingAgent evaluation methods."""
    agent = KaizenTradingAgent()
    
    # Macro Oil Long Veto -> should be True (quarantined)
    assert agent.evaluate_setup("USWTI.p:4H:VWAP_M2_LONG") is True
    
    # Gold 1H Volatility Veto -> should be True (quarantined)
    assert agent.evaluate_setup("XAUUSD.pi:1H:VWAP_M2_LONG") is True
    
    # Active Champion -> should be False (permitted)
    assert agent.evaluate_setup("EURUSD.pi:15M:VWAP_M2_TWO_BAR_BULLISH") is False

    # Detailed verdict format check
    verdict = agent.get_setup_verdict("USWTI.p:4H:VWAP_M2_LONG")
    assert verdict["quarantined"] is True
    assert verdict["status"] == "QUARANTINED"
    assert "Macro Bear Regime" in verdict["reason"]


def test_run_kaizen_agent_suite_full_execution():
    """Verify run_kaizen_agent_suite() executes Kaizen's TestRunner with 100% pass rate."""
    res = run_kaizen_agent_suite("kaizen.yaml")
    assert res["success"] is True, f"Kaizen Agent suite failed: {res.get('error')}"
    assert res["total_tests"] >= 5
    assert res["passed_tests"] == res["total_tests"]
    assert res["failed_tests"] == 0
    assert len(res["test_cases"]) >= 5


def test_format_kaizen_agent_report_section():
    """Verify markdown formatting for CRON_HEALTH_REPORT.md."""
    sample_res = {
        "success": True,
        "suite_name": "Test Suite",
        "target_file": "engine/kaizen_self_improvement.py",
        "total_tests": 5,
        "passed_tests": 5,
        "failed_tests": 0,
        "execution_time_sec": 0.45,
        "test_cases": [
            {"name": "Step 1", "expected_output": True, "actual_output": True, "status": "PASSED"}
        ]
    }
    md = format_kaizen_agent_report_section(sample_res)
    assert "### 🧪 Kaizen Agent Framework Verification (✅ PASSED)" in md
    assert "5/5 Passed" in md
    assert "| `Step 1` | `True` | `True` | ✅ `PASSED` |" in md
