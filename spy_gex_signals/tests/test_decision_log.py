import json
from datetime import date

from spy_gex_signals.decision_log import DecisionLogger


def test_decision_log_appends_jsonl(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    dlog = DecisionLogger(log_path)

    dlog.log(component="test.comp", rule="rule.a", inputs={"x": 1},
             output={"y": 2}, reason="because")
    dlog.log(component="test.comp", rule="rule.b",
             inputs={"unserializable": date(2026, 7, 13)},  # coerced, must not raise
             output=None, reason="edge case")

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["component"] == "test.comp"
    assert first["rule"] == "rule.a"
    assert first["inputs"] == {"x": 1}
    assert first["reason"] == "because"
    assert "ts" in first
    second = json.loads(lines[1])  # valid JSON despite the date object
    assert "2026-07-13" in str(second["inputs"]["unserializable"])
