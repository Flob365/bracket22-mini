from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import event

from bracket22.app import create_app


def test_busy_database_returns_actionable_conflict_without_second_analysis(tmp_path):
    app=create_app(f'sqlite:///{tmp_path}/busy.db',today=lambda:date(2026,9,11))
    with TestClient(app) as client:
        engine=app.state.service.store.engine
        @event.listens_for(engine,'connect')
        def no_wait(dbapi_connection, record):
            dbapi_connection.execute('PRAGMA busy_timeout=0')
        with app.state.service.store.transaction():
            response=client.post('/analysis/SPY')
            assert response.status_code==409
            assert 'en cours' in response.json()['detail']
        assert client.get('/reports/latest').json()==[]
        assert client.post('/analysis/SPY').status_code==200
