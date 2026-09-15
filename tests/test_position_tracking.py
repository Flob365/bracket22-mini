from datetime import date

import pytest

from bracket22.core import SampleProvider
from bracket22.service import Service


class Feed(SampleProvider):
    failed = False
    crash = False

    def history(self, symbol, day):
        if self.failed:
            raise OSError('Unavailable')
        bars = super().history(symbol, day)
        if self.crash:
            bars = [b.model_copy(update={k: getattr(b, k) * 0.5 for k in ('open', 'high', 'low', 'close')}) for b in bars]
        return bars


def desk(tmp_path):
    feed = Feed()
    service = Service(f'sqlite:///{tmp_path}/desk.db', feed, lambda: date(2026, 9, 11))
    report = service.analyze('SPY')
    service.review(report['id'], 'approve')
    return service, feed


def test_position_exit_alerts_and_realized_accounting(tmp_path):
    service, feed = desk(tmp_path)
    initial = service.portfolio()['positions'][0]
    assert initial['holding_sessions'] == 0
    assert initial['exit_alerts'] == []
    service.today = lambda: date(2026, 10, 20)
    feed.crash = True
    portfolio = service.portfolio()
    pos = portfolio['positions'][0]
    assert set(pos['exit_alerts']) == {'stop_reached', 'horizon_reached'}
    assert pos['current_price'] < pos['entry_price']
    assert portfolio['realized_pnl_eur'] == 0
    assert portfolio['unrealized_pnl_eur'] == pytest.approx(pos['pnl_eur'])
    assert len(portfolio['positions']) == 1  # Alerts never execute a close.
    close = service.close('SPY')
    after = service.portfolio()
    assert after['realized_pnl_eur'] == pytest.approx(close['realized_pnl_eur'])
    assert after['unrealized_pnl_eur'] == 0


def test_missing_data_does_not_present_exit_signal_as_current(tmp_path):
    service, feed = desk(tmp_path)
    feed.failed = True
    pos = service.portfolio()['positions'][0]
    assert pos['data_status'] == 'missing'
    assert pos['current_price'] is None
    assert pos['exit_alerts'] == []
    assert pos['pnl_eur'] is None


def test_split_adjusts_stop_without_false_alert(tmp_path):
    service, feed = desk(tmp_path)
    before = service.portfolio()['positions'][0]
    feed.crash = True
    feed.split_factor = lambda symbol, reference_day, as_of: 2.0
    after = service.portfolio()['positions'][0]
    assert after['stop_price'] == pytest.approx(before['stop_price'] / 2)
    assert after['entry_price'] == pytest.approx(before['entry_price'] / 2)
    assert after['pnl_eur'] == pytest.approx(0)
    assert after['exit_alerts'] == []


def test_stale_close_does_not_raise_current_exit_alert(tmp_path):
    service, feed = desk(tmp_path)
    history = feed.history
    feed.history = lambda symbol, day: history(symbol, date(2026, 9, 11))
    service.today = lambda: date(2026, 9, 14)
    feed.crash = True
    pos = service.portfolio()['positions'][0]
    assert pos['data_status'] == 'stale'
    assert pos['exit_alerts'] == []
