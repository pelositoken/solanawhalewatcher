from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from spy_gex_signals.config import SmtConfig
from spy_gex_signals.structure import fixtures
from spy_gex_signals.structure.models import Direction
from spy_gex_signals.structure.smt import SmtChecker

CFG = SmtConfig(enabled=True, min_correlation=0.7, correlation_lookback_bars=40)


def sweep_ts():
    df = fixtures.ltf_frame()
    return df.index[20 + 9].to_pydatetime()  # the sweep bar


def test_divergence_when_correlated_holds_its_swing():
    checker = SmtChecker(CFG, 2, fixtures.ltf_frame(), fixtures.correlated_frame(), "CORR")
    status, detail = checker.check(sweep_ts(), Direction.LONG)
    assert status == "divergence"
    assert detail["correlated_broke"] is False
    assert detail["correlation"] > 0.9


def test_no_divergence_when_correlated_breaks_too():
    corr = fixtures.ltf_frame() * 10.0  # identical tape: it sweeps its low as well
    checker = SmtChecker(CFG, 2, fixtures.ltf_frame(), corr, "CORR")
    status, detail = checker.check(sweep_ts(), Direction.LONG)
    assert status == "no_divergence"
    assert detail["correlated_broke"] is True


def test_low_correlation_disables_smt():
    primary = fixtures.ltf_frame()
    rng = np.random.default_rng(7)
    noise = pd.DataFrame({
        "open": 50 + rng.normal(0, 1, len(primary)).cumsum() * 0,
        "high": 51.0, "low": 49.0,
        "close": 50 + np.sin(np.arange(len(primary))) * 2 + rng.normal(0, 0.5, len(primary)),
        "volume": 1.0,
    }, index=primary.index)
    checker = SmtChecker(CFG, 2, primary, noise, "NOISE")
    status, detail = checker.check(sweep_ts(), Direction.LONG)
    assert status == "disabled_low_correlation"
    assert detail["correlation"] < 0.7


def test_disabled_or_missing_data_reports_not_available():
    checker = SmtChecker(SmtConfig(enabled=False), 2, fixtures.ltf_frame(),
                         fixtures.correlated_frame(), "CORR")
    assert checker.check(sweep_ts(), Direction.LONG)[0] == "not_available"
    checker2 = SmtChecker(CFG, 2, fixtures.ltf_frame(), None, "")
    assert checker2.check(sweep_ts(), Direction.LONG)[0] == "not_available"
