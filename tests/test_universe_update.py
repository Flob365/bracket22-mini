from datetime import date

import pytest

from bracket22.core import ASSETS
from bracket22.service import Service


def test_new_universe_and_retired_assets(tmp_path):
    symbols={a.symbol for a in ASSETS}
    assert len(symbols)==20
    assert {'SPCX','WMT','LRCX'} <= symbols
    assert not {'BTC','ETH','CRWD'} & symbols
    assert not any(a.crypto for a in ASSETS)
    service=Service(f'sqlite:///{tmp_path}/desk.db',today=lambda:date(2026,9,11))
    for symbol in ('BTC','ETH','CRWD'):
        with pytest.raises(KeyError):service.analyze(symbol)


def test_retired_report_is_archived_and_cannot_be_approved(tmp_path, monkeypatch):
    import bracket22.service as module
    from bracket22.service import Conflict
    service=Service(f'sqlite:///{tmp_path}/desk.db',today=lambda:date(2026,9,11))
    active=module.ACTIVE_SYMBOLS
    monkeypatch.setattr(module,'ACTIVE_SYMBOLS',active | {'BTC'})
    report=service.analyze('BTC')
    monkeypatch.setattr(module,'ACTIVE_SYMBOLS',active)
    assert service.latest()==[]
    with pytest.raises(Conflict,match='retiré'):
        service.review(report['id'],'approve')
    with service.store.engine.connect() as conn:
        assert service.store.report(conn,report['id'])['symbol']=='BTC'
    assert service.store.verify()['valid']
