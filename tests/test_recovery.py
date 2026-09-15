from datetime import UTC, date, datetime

from bracket22.core import SampleProvider
from bracket22.service import Service


class RecoveringProvider(SampleProvider):
    broken = True

    def history(self, symbol, day):
        if self.broken and symbol == 'AAPL':
            raise OSError('Feed unavailable')
        return super().history(symbol, day)


def test_daily_failure_recovers_without_replacing_journal(tmp_path):
    provider = RecoveringProvider()
    service = Service(f'sqlite:///{tmp_path}/desk.db', provider, lambda: date(2026, 9, 11))
    first = service.daily()
    assert any(e['symbol'] == 'AAPL' for e in first['errors'])
    old = next(r for r in first['reports'] if r['symbol'] == 'AAPL')
    provider.broken = False
    second = service.daily()
    assert second['errors'] == []
    new = next(r for r in second['reports'] if r['symbol'] == 'AAPL')
    assert new['id'] != old['id']
    assert new['supersedes'] == old['id']
    assert service.analyze('AAPL')['id'] == new['id']
    with service.store.engine.connect() as conn:
        assert service.store.report(conn, old['id'])['data_error']
    assert service.store.verify()['valid']
    status = service.operations()
    assert status['last_run']['errors'] == []
    assert status['last_success']['day'] == '2026-09-11'


def test_scheduler_restart_uses_persisted_success(tmp_path):
    from bracket22.scheduler import run_once

    url = f'sqlite:///{tmp_path}/desk.db'
    service = Service(url, today=lambda: date(2026, 9, 11))
    assert run_once(service, datetime(2026, 9, 11, 6, tzinfo=UTC)) is None
    assert run_once(service, datetime(2026, 9, 11, 7, tzinfo=UTC))['errors'] == []
    restarted = Service(url, today=service.today)
    assert run_once(restarted, datetime(2026, 9, 11, 8, tzinfo=UTC)) is None


def test_measurements_reject_missing_sessions(tmp_path):
    class GapProvider(SampleProvider):
        gap = False

        def history(self, symbol, day):
            bars = super().history(symbol, day)
            return [b for b in bars if not self.gap or b.day != date(2026, 9, 14)]

    provider = GapProvider()
    service = Service(f'sqlite:///{tmp_path}/desk.db', provider, lambda: date(2026, 9, 11))
    service.analyze('SPY')
    provider.gap = True
    service.today = lambda: date(2026, 9, 21)
    result = service.refresh()
    assert result['added'] == 0
    assert result['errors']
