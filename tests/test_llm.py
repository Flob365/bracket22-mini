from datetime import date
from types import SimpleNamespace

import pytest

from bracket22.core import SampleProvider


def test_llm_roles_must_call_python_and_preserve_red_veto():
    from bracket22.llm import LLMHouston

    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if kwargs['tool_choice'] != 'none':
            return SimpleNamespace(status='completed', output=[SimpleNamespace(type='function_call', name='research_evidence', arguments='{}', call_id='test', model_dump=lambda **kw: {'type':'function_call','name':'research_evidence','arguments':'{}','call_id':'test'})], usage=None, id='r')
        return SimpleNamespace(status='completed', output=[], output_text='{"score":8,"confidence":0.7,"recommendation":"candidate","thesis":"Test","arguments":["Test"]}',usage=None,id='r')
    adapter = LLMHouston(client=SimpleNamespace(responses=SimpleNamespace(create=create)))
    feed = SampleProvider()
    bars = feed.history('SPY', date(2026,9,11))
    result = adapter.run(bars,bars)
    assert len(calls) == 8
    assert [c['model'] for c in calls[::2]] == ['gpt-6-luna']*2 + ['gpt-6-sol']*2
    assert [c['reasoning']['effort'] for c in calls[::2]] == ['max']*2+['medium']*2
    assert result['Steffi']['metrics']['sma200'] > 0
    assert result['Houston']['mode'] == 'llm'
    assert all(c['store'] is False for c in calls)


def test_missing_tool_call_fails_closed():
    from bracket22.llm import LLMHouston
    client=SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: SimpleNamespace(status='completed',output=[])))
    feed=SampleProvider(); bars=feed.history('SPY',date(2026,9,11))
    with pytest.raises(ValueError,match='outil'):
        LLMHouston(client=client).run(bars,bars)


def test_red_team_veto_cannot_be_overridden():
    from bracket22.llm import LLMHouston
    adapter = LLMHouston(client=object())
    def assess(name, evidence):
        return {'recommendation':'reject' if name=='Red Team' else 'candidate',
                'score':10,'confidence':1,'arguments':['Veto']}, evidence()
    adapter.assess = assess
    feed = SampleProvider(); bars=feed.history('SPY',date(2026,9,11))
    result = adapter.run(bars,bars)
    assert result['Houston']['recommendation'] == 'watch'
    assert result['Houston']['signal'] is False


def test_llm_failure_is_journaled_without_paper_order(tmp_path):
    from bracket22.service import Service
    class Failed:
        mode='llm'
        version='failed-llm-test'
        def run(self, *args):
            raise ValueError('OpenAI unavailable')
    service=Service(f'sqlite:///{tmp_path}/desk.db',today=lambda:date(2026,9,11),houston=Failed())
    report=service.analyze('SPY')
    assert report['mode']=='llm'
    assert report['risk']['status']=='REJECTED'
    assert report['data_error']=='OpenAI unavailable'
    assert service.portfolio()['positions']==[]
    assert service.store.verify()['valid']
