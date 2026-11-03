"""Observation must be live, read-only and dispensable to business execution."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import create_engine

from app import progress
from app.models import WorkflowProgress
from test_workflow import setup
from test_orchestration import agents, start, post
from test_execution_flow import Executor


@pytest.fixture
def store(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'observation.db'}", connect_args={"check_same_thread": False})
    WorkflowProgress.__table__.create(engine)
    monkeypatch.setattr(progress, "storage_engine", lambda bind: engine)
    yield engine
    engine.dispose()


def test_progress_is_visible_while_agent_is_still_working(agents, store, monkeypatch):
    client, fake, factory, calls = agents
    entered, release = Event(), Event()
    original = progress.node  # Graph wrappers do not replace the agent itself.
    from app import orchestration
    completion = orchestration.provider.completion

    def slow(*args, **kwargs):
        if kwargs.get("name") == "module_classifier":
            entered.set()
            assert release.wait(10)
        return completion(*args, **kwargs)

    monkeypatch.setattr(orchestration.provider, "completion", slow)
    before = copy.deepcopy(fake.data)
    row = client.post('/api/cases', json={"request": "Configurar quinze dias antes das férias na ABC", "effective_from": "2026-10-31"}).json()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(post, client, row, "orchestrate")
        try:
            assert entered.wait(10)
            response = client.get(f"/api/cases/{row['id']}/progress")
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-store'
            live = response.json()
            assert live['progress']['active']
            assert live['progress']['step'] == 'classification'
            assert live['progress']['state'] == 'running'
            assert not pending.done()
            assert fake.data == before
        finally:
            release.set()
        finished = pending.result(timeout=10)
    data = client.get(f"/api/cases/{row['id']}/progress").json()
    assert not data['progress']['active']
    assert data['progress']['step'] == 'choice'
    assert data['progress']['state'] == 'waiting'
    assert {'catalog', 'snapshot', 'knowledge'} <= {e['step'] for e in data['progress']['events']}
    # Observation cannot change the revision or call an agent/HCM again.
    assert data['revision'] == finished['revision']
    assert data['progress']['base_revision'] < data['revision']
    for _ in range(3):
        assert client.get(f"/api/cases/{row['id']}/progress").json()['revision'] == finished['revision']
    assert len(calls) == 2 and fake.data == before
    assert progress.node is original


def test_observation_failure_does_not_change_approval_execution_or_rollback(agents, monkeypatch):
    client, fake, factory, calls = agents
    def unavailable(bind):
        raise RuntimeError('Observation storage unavailable; secret must not reach UI')
    monkeypatch.setattr(progress, 'storage_engine', unavailable)
    executor = Executor(fake)
    fake.apply, fake.verify, fake.rollback = executor.apply, executor.verify, executor.rollback
    before = copy.deepcopy(fake.data)
    row = start(client)
    assert fake.data == before
    row = post(client, row, action='choose', choice='accept')
    assert row['status'] == 'review' and row['approval_hash']
    assert fake.data == before and executor.calls == 0
    assert client.post(f"/api/cases/{row['id']}/execute", json={'revision': row['revision'], 'approval_hash': '0' * 64}).status_code == 409
    assert executor.calls == 0
    row = post(client, row, 'execute', approval_hash=row['approval_hash'])
    assert row['status'] == 'applied' and executor.calls == 1
    row = post(client, row, 'confirm-result')
    assert row['status'] == 'validated'
    row = post(client, row, 'rollback')
    assert row['status'] == 'reverted' and fake.data == before
    response = client.get(f"/api/cases/{row['id']}/progress")
    assert response.status_code == 200 and response.json()['progress'] is None
    assert 'secret' not in response.text


def test_recorded_data_is_bounded_and_contains_only_step_metadata(store):
    recorder = progress.Recorder(store, 42)
    for _ in range(180):
        recorder.emit('classification')
    recorder.emit('untrusted prompt or credential')
    recorder.finish()
    data = progress.read(store, 42)
    assert len(data['events']) == 160
    assert set(data) == {'run_id', 'events', 'events_truncated', 'active', 'updated_at', 'started_at', 'base_revision', 'step', 'state', 'operation', 'flow_id', 'history', 'history_truncated'}
    assert all(set(e) == {'step', 'state', 'at'} for e in data['events'])
    assert not data['active']


def test_progress_requires_authentication_and_existing_case(agents, store):
    client, *_ = agents
    assert client.get('/api/cases/99999/progress').status_code == 404
    client.cookies.clear()
    assert client.get('/api/cases/1/progress').status_code == 401


def test_reanalysis_and_repairs_are_reported_without_repeating_accepted_agents(agents, store):
    client, fake, factory, calls = agents
    row = start(client)
    first = client.get(f"/api/cases/{row['id']}/progress").json()['progress']['run_id']
    row = post(client, row, action='choose', choice='accept')
    data = client.get(f"/api/cases/{row['id']}/progress").json()['progress']
    assert data['run_id'] != first
    assert data['step'] == 'approval' and data['state'] == 'waiting' and not data['active']
    assert {'configuration', 'validation'} <= {e['step'] for e in data['events']}
    assert len(calls) == 3


def test_internal_model_request_is_live_and_archived_across_human_pauses(setup, store, monkeypatch, tmp_path):
    import json
    import httpx
    from app import orchestration
    from test_orchestration import SUGGESTION
    from test_workflow import PROPOSAL
    client, fake, factory = setup
    monkeypatch.setenv('LANGGRAPH_SQLITE_PATH', str(tmp_path / 'trace-checkpoints.db'))
    secret = 'sk-observation-must-not-leak'
    config = {'api_key': secret, 'base_url': 'https://example.invalid/v1', 'model': 'gpt-6-luna', 'reasoning_effort': 'medium'}
    monkeypatch.setattr(orchestration.provider, 'read_config', lambda: config.copy())
    entered, release = Event(), Event()
    original = httpx.Client
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        name = payload['response_format']['json_schema']['name']
        requests.append(name)
        assert payload['reasoning_effort'] == 'medium'
        if name == 'interpretation_agent':
            entered.set()
            assert release.wait(10)
            value = SUGGESTION
        elif name == 'module_classifier':
            value = {'module': 'vacations', 'company_query': 'ABC Ltda', 'scope': 'parametrization', 'explanation': 'Identificado'}
        else:
            value = PROPOSAL
        return httpx.Response(200, json={'model': 'gpt-6-luna-test-snapshot',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}],
            'usage': {'prompt_tokens': 40, 'completion_tokens': 15, 'total_tokens': 55,
                      'completion_tokens_details': {'reasoning_tokens': 6}, 'prompt_tokens_details': {'cached_tokens': 12}}})

    monkeypatch.setattr(orchestration.provider.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    row = client.post('/api/cases', json={'request': 'Configurar quinze dias antes das férias na ABC', 'effective_from': '2026-10-31'}).json()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(post, client, row, 'orchestrate')
        try:
            assert entered.wait(10)
            live = client.get(f"/api/cases/{row['id']}/progress").json()['progress']
            operation = live['events'][-1]
            assert operation['step'] == 'interpretations' and operation['activity'] == 'model_request'
            assert operation['state'] == 'running'
            assert operation['meta']['model'] == 'gpt-6-luna'
            assert operation['meta']['reasoning_effort'] == 'medium'
            assert secret not in json.dumps(live) and 'messages' not in json.dumps(live)
            assert not future.done()
        finally:
            release.set()
        row = future.result(timeout=10)
    first = client.get(f"/api/cases/{row['id']}/progress").json()['progress']
    response = next(e for e in first['events'] if e.get('activity') == 'model_response' and e['step'] == 'interpretations')
    assert response['meta']['total_tokens'] == 55 and response['meta']['reasoning_tokens'] == 6
    assert response['meta']['returned_model'] == 'gpt-6-luna-test-snapshot'
    assert response['meta']['estimated_usd'] >= 0
    assert any(e.get('activity') == 'alternatives_organize' and e['state'] == 'done' for e in first['events'])
    row = post(client, row, action='choose', choice='accept')
    second = client.get(f"/api/cases/{row['id']}/progress").json()['progress']
    assert second['history'][-1]['run_id'] == first['run_id']
    assert second['history'][-1]['events'] == first['events']
    assert requests == ['module_classifier', 'interpretation_agent', 'vacation_proposal']
    assert row['status'] == 'review'


def test_request_retry_records_each_attempt_and_never_records_payloads(store, monkeypatch):
    import json
    import httpx
    from app import provider
    attempts = []
    original = httpx.Client
    def respond(request):
        payload = json.loads(request.content)
        attempts.append(payload)
        assert 'reasoning_effort' not in payload  # Unsupported model: do not invent an effort.
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if len(attempts) == 1 else 'stop', 'message': {'content': '{}'}}],
                                      'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    monkeypatch.setattr(provider.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    config = {'api_key': 'sk-secret', 'base_url': 'https://example.invalid/v1', 'model': 'custom-model'}
    with progress.observe(store, 11):
        progress.node('alternatives', lambda state: provider.completion(config, [{'role': 'user', 'content': 'PRIVATE REQUEST'}], {}, token_limit=64))({})
    data = progress.read(store, 11)
    calls = [e for e in data['events'] if e.get('activity') == 'model_request' and e['state'] == 'running']
    assert [e['meta']['request_attempt'] for e in calls] == [1, 2]
    assert [e['meta']['max_completion_tokens'] for e in calls] == [64, 128]
    assert all(e['meta']['reasoning_effort'] == 'not_supported' for e in calls)
    assert any(e.get('activity') == 'output_retry' for e in data['events'])
    assert 'PRIVATE REQUEST' not in json.dumps(data) and 'sk-secret' not in json.dumps(data)


def test_metadata_allowlist_and_bounded_history(store):
    import math
    assert progress.safe_metadata({'api_key': 'secret', 'messages': ['secret'], 'duration_seconds': math.inf,
                                   'model': 'sk-secret', 'total_tokens': -1, 'reasoning_effort': 'high'}) == {'reasoning_effort': 'high'}
    for _ in range(23):
        with progress.observe(store, 10):
            with progress.span('interpretations', 'model_request', model='gpt-6-luna', reasoning_effort='medium'):
                pass
    data = progress.read(store, 10)
    assert len(data['history']) == 20 and data['history_truncated']
    assert all('history' not in item for item in data['history'])


def test_langgraph_stays_within_the_marked_stages():
    """The journey marks LangGraph from Escopo (guard_input) to after Validação (plan → END)."""
    from app.orchestration import build_graph
    graph = build_graph(None, None, None).get_graph()
    nodes = set(graph.nodes) - {'__start__', '__end__'}
    assert nodes <= set(progress.NODE_STEPS) | {'plan'}
    assert {e.target for e in graph.edges if e.source == '__start__'} == {'guard_input'}
    assert progress.NODE_STEPS['guard_input'] == 'scope'
    # plan is the main exit; the others are detours: out of scope and closure by the consultant.
    assert {e.source for e in graph.edges if e.target == '__end__'} == {'guard_input', 'classify', 'plan', 'consultant'}
    assert not set(progress.NODE_STEPS.values()) & {'approval', 'apply', 'tests', 'experience'}


def test_routes_record_the_path_taken_with_node_names_only(agents, store):
    client, *_ = agents
    row = start(client)
    routes = lambda: [(e['meta']['source'], e['meta']['target']) for e in client.get(f"/api/cases/{row['id']}/progress").json()['progress']['events'] if e.get('activity') == 'route']
    assert routes() == [('guard_input', 'classify'), ('classify', 'preview'), ('preview', 'context'), ('context', 'alternatives'), ('alternatives', 'choice')]
    row = post(client, row, action='choose', choice='accept')
    assert routes() == [('choice', 'remember'), ('remember', 'plan'), ('plan', '__end__')]
    assert progress.safe_metadata({'source': 'guard_input', 'target': 'Configurar férias da ABC'}) == {'source': 'guard_input'}


def test_stage_map_shows_every_langgraph_route_and_recorded_activity():
    """A new route or activity must be placed in the stage view (workflow_map.py) before it ships."""
    from app.orchestration import build_graph
    from app.workflow_map import DETOURS, MAIN, STAGES
    edges = {(e.source, e.target) for e in build_graph(None, None, None).get_graph().edges}
    items = [item for stage in STAGES for item in stage['items']]
    # guide is a legacy pause that no route reaches; the compiled graph publishes it without edges.
    shown = {('__start__', 'guard_input'), *MAIN.items()}
    shown |= {(i['node'], i['to']) for i in items if i['type'] == 'decision' and not i['internal']}
    shown |= {(node, target) for node, detour in DETOURS.items() for target in detour['routes']}
    assert shown == edges
    assert progress.ACTIVITIES - {'route'} <= {a for i in items if i['type'] == 'stop' for a in i['activities']}
    ids = [stage['id'] for stage in STAGES]
    assert ids == ['request', 'scope', 'classification', 'context', 'interpretations', 'choice', 'configuration', 'validation', 'approval', 'apply', 'tests', 'experience']
    assert [stage['id'] for stage in STAGES if stage['nodes']] == ids[1:8]
    assert all(i['node'] in stage['nodes'] for stage in STAGES for i in stage['items'] if i.get('node'))


def test_stage_map_requires_authentication(agents):
    client, *_ = agents
    data = client.get('/api/workflow/map').json()
    assert data['stages'][1]['id'] == 'scope' and data['main']['plan'] == '__end__'
    client.cookies.clear()
    assert client.get('/api/workflow/map').status_code == 401
