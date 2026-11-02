import json
import pytest
import httpx
from test_workflow import setup, opened, base_snapshot, CATALOG, PROPOSAL
from app.provider import proposal_schema, compact_context

KEY = "sk-test-never-real-" + "x" * 40

def settings_payload(**extra):
    return {"model": "gpt-6-astra", "api_key": KEY, "max_completion_tokens": 4096, **extra}

def test_settings_secret_not_returned_and_reloaded(setup, monkeypatch):
    client, _, _ = setup
    assert client.get('/api/model').json()['configured'] is False
    result = client.post('/api/model', json=settings_payload())
    assert result.status_code == 200, result.text
    assert KEY not in result.text and 'api_key"' not in result.text
    assert result.json()['configured'] is True
    assert client.get('/api/me').json()['model_configured'] is True
    changed = client.post('/api/model', json=settings_payload(api_key=None, model='gpt-6.1-sol'))
    assert changed.status_code == 200 and changed.json()['model'] == 'gpt-6.1-sol'
    from app.model_settings import read_config
    assert read_config()['api_key'] == KEY
    client.headers['Origin'] = 'http://other.example'
    assert client.post('/api/model', json=settings_payload()).status_code == 403
    client.cookies.clear()
    assert client.get('/api/model').status_code == 401

def test_connection_check_uses_small_synthetic_request(setup, monkeypatch):
    import app.provider as provider
    client, _, _ = setup
    client.post('/api/model', json=settings_payload())
    original = httpx.Client
    def respond(request):
        data = json.loads(request.content)
        assert request.url == 'https://api.openai.com/v1/chat/completions'
        assert data['max_completion_tokens'] == 512
        assert data['store'] is False and data['reasoning_effort'] == 'low'
        assert 'EMP001' not in json.dumps(data)
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'{"connected":true}'}}], 'usage':{'total_tokens':20}})
    monkeypatch.setattr(provider.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    response=client.post('/api/model/test',json={})
    assert response.status_code==200, response.text
    assert response.json()['passed'] is True and KEY not in response.text
    assert client.get('/api/model').json()['last_test']['passed'] is True

@pytest.mark.parametrize('status',[400,401,403,404,429,500])
def test_provider_errors_are_sanitized(setup, monkeypatch, status):
    import app.provider as provider
    client, _, _ = setup
    client.post('/api/model',json=settings_payload())
    original=httpx.Client
    def respond(request):
        return httpx.Response(status,json={'error':{'message':'DO NOT LEAK '+KEY}})
    monkeypatch.setattr(provider.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    response=client.post('/api/model/test',json={})
    assert response.status_code==502 and KEY not in response.text and 'DO NOT LEAK' not in response.text

def test_schema_restricts_parameters_groups_and_sources():
    snapshot=base_snapshot()
    sources=[{'id':'catalog:vacations-1','content':{**CATALOG,'input_schema':{'large':'unused'}}}]
    schema=proposal_schema(snapshot,sources)
    variants=schema['properties']['changes']['items']['anyOf']
    assert all(variant['properties']['group_id']['enum']==[None,10] for variant in variants)
    assert 'max_installments' in [variant['properties']['parameter']['enum'][0] for variant in variants]
    assert schema['properties']['sources']['items']['enum']==['catalog:vacations-1']
    context,reduced=compact_context(snapshot,sources)
    assert 'schedules' not in context and 'input_schema' not in reduced[0]['content']
    assert 'input_schema' in sources[0]['content']

def test_first_key_required_and_other_provider_key_not_reused(setup, monkeypatch):
    client,_,_=setup
    assert client.post('/api/model',json=settings_payload(api_key=None)).status_code==422
    monkeypatch.setenv('LLM_BASE_URL','https://other.example/v1')
    monkeypatch.setenv('LLM_API_KEY',KEY)
    assert client.post('/api/model',json=settings_payload(api_key=None)).status_code==422

def test_automatic_proposal_flows_through_official_validator(setup, monkeypatch):
    import app.provider as provider
    from app.main import app
    from app.models import Case
    client,_,_=setup
    row=opened(client)
    client.post('/api/model',json=settings_payload())
    original=httpx.Client
    def respond(request):
        data=json.loads(request.content)
        assert data['model']=='gpt-6-astra' and data['reasoning_effort']=='medium'
        assert KEY not in json.dumps(data)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(PROPOSAL)}}],'usage':{'total_tokens':250}})
    monkeypatch.setattr(provider.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    response=client.post(f"/api/cases/{row['id']}/analyze",json={'revision':row['revision']})
    assert response.status_code==200, response.text
    result=response.json()
    assert result['status']=='review' and result['proposal']['payloads'][0]['max_installments']==3
    assert result['events'][-1]['metrics']['prompt_version']==provider.PROMPT_VERSION
