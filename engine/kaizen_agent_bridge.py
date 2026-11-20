#!/usr/bin/env python3
"""
engine/kaizen_agent_bridge.py
================================================================================
KAIZEN AGENT (Kaizen-agent/kaizen-agent) INTEGRATION BRIDGE
================================================================================
SOP v2.1.0 | Autonomous Continuous LLM/Agent Testing, Evaluation & Improvement.

Integrates the official Kaizen Agent framework (https://github.com/Kaizen-agent/kaizen-agent)
into the Institutional Quantitative Trading Desk:
1. Loads YAML test suites (kaizen.yaml) specifying quantitative behavior & invariants.
2. Executes Kaizen's TestRunner with CodeRegionExecutor and AssertionRunner.
3. Assesses trade setup quarantine accuracy, macro veto enforcement, and champion permission.
4. Generates structured audit telemetry for continuous desk health reporting.
"""

import os
import sys
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("KaizenAgentBridge")


def run_kaizen_agent_suite(config_path: str = "kaizen.yaml") -> Dict[str, Any]:
    """Executes the Kaizen Agent autonomous evaluation suite on the trading desk.
    
    Args:
        config_path: Path to the kaizen.yaml configuration file.
        
    Returns:
        Dict with test summary, passed/failed counts, and detailed case results.
    """
    full_config_path = PROJECT_ROOT / config_path
    if not full_config_path.exists():
        logger.error(f"Kaizen configuration file not found at: {full_config_path}")
        return {
            "success": False,
            "error": f"Config not found: {config_path}",
            "total_tests": 0,
            "passed_tests": 0,
            "failed_tests": 0,
            "test_cases": []
        }

    # Ensure an offline/local fallback API key is present if not set by operator
    # so that Kaizen's TestRunner initializes gracefully without failing on env checks
    if not os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = "institutional_local_eval_token"

    try:
        from kaizen.autofix.test.runner import TestRunner
        from kaizen.cli.commands.models import TestStatus

        with open(full_config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        config["config_file"] = str(full_config_path)

        target_file_path = full_config_path.parent / config.get("file_path", "engine/kaizen_self_improvement.py")
        if not target_file_path.exists():
            target_file_path = PROJECT_ROOT / config.get("file_path", "engine/kaizen_self_improvement.py")

        runner = TestRunner(config, verbose=False)
        result = runner.run_tests(Path(target_file_path))

        case_results: List[Dict[str, Any]] = []
        for tc in result.test_cases:
            is_passed = (tc.status == TestStatus.PASSED)
            assertions = tc.metadata.get("assertions", []) if tc.metadata else []
            expected_val = tc.expected_output
            if expected_val is None and assertions:
                expected_val = assertions[0].get("expected")

            case_results.append({
                "name": tc.name,
                "status": "PASSED" if is_passed else "FAILED",
                "actual_output": tc.actual_output,
                "expected_output": expected_val,
                "error": tc.error_message
            })

        total = result.summary.total_tests
        passed = result.summary.passed_tests
        failed = result.summary.failed_tests

        return {
            "success": (failed == 0 and total > 0),
            "suite_name": config.get("name", "Kaizen Agent Evaluation"),
            "target_file": str(config.get("file_path")),
            "total_tests": total,
            "passed_tests": passed,
            "failed_tests": failed,
            "execution_time_sec": round(result.summary.total_execution_time or 0.0, 3),
            "test_cases": case_results
        }

    except Exception as e:
        logger.error(f"Error executing Kaizen Agent suite: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "total_tests": 0,
            "passed_tests": 0,
            "failed_tests": 0,
            "test_cases": []
        }


def format_kaizen_agent_report_section(result: Dict[str, Any]) -> str:
    """Formats the Kaizen Agent evaluation outcome into Markdown for the hourly report."""
    if not result or not result.get("total_tests"):
        return "> ⚠️ *Kaizen Agent evaluation suite was not run or encountered an error.*"

    status_icon = "✅ PASSED" if result.get("success") else "❌ FAILED"
    table_rows = []
    for tc in result.get("test_cases", []):
        icon = "✅" if tc["status"] == "PASSED" else "❌"
        table_rows.append(f"| `{tc['name']}` | `{tc['expected_output']}` | `{tc['actual_output']}` | {icon} `{tc['status']}` |")
    
    rows_str = "\n".join(table_rows)
    return f"""### 🧪 Kaizen Agent Framework Verification ({status_icon})
* **Suite Name:** `{result.get('suite_name')}`
* **Target Spec:** [`kaizen.yaml`](file:///c:/Users/SIGMA/Documents/Delta_Fibonacci_Institutional_Business/kaizen.yaml) → [`{result.get('target_file')}`](file:///c:/Users/SIGMA/Documents/Delta_Fibonacci_Institutional_Business/{result.get('target_file')})
* **Score:** `{result.get('passed_tests')}/{result.get('total_tests')} Passed` (`{result.get('execution_time_sec')}s` execution)

| Test Invariant | Expected | Actual | Outcome |
| :--- | :--- | :--- | :--- |
{rows_str}
"""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Executing Kaizen Agent Autonomous Test Suite...")
    res = run_kaizen_agent_suite()
    print(f"Outcome: {res.get('passed_tests')}/{res.get('total_tests')} Passed (Success: {res.get('success')})")
    print("\nMarkdown Preview:")
    print(format_kaizen_agent_report_section(res))
