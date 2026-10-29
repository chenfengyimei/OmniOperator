"""源码不执行、目录覆盖、API 实况、只读元数据与真实 Chromium 导航。"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

from fastapi.testclient import TestClient
import pytest

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.config import PROJECT_ROOT
from omni.safety.gates import Risk
from omni.system.catalog import build_index
from omni.system.catalog.render import render_documents
from omni.tools.browser.driver import BrowserDriver
from omni.tools.registry import Tool, ToolRegistry


@pytest.fixture
def workbench(tmp_path):
    config = tmp_path / 'config.yaml'
    config.write_text('persona:\n  name: 索引测试\n', encoding='utf-8')
    runtime = build_runtime(data_dir=tmp_path/'data', db_path=tmp_path/'memory.db',
                            config_path=config, skills_root=tmp_path/'skills', start_background_workers=False)
    app = create_app(runtime)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        yield client, runtime


def pair(client):
    assert client.post('/api/auth/pair', json={'code':client.app.state.web_auth.pairing_code}).status_code == 200


def test_index_parses_inert_source_and_ignores_private_roots(tmp_path):
    source = tmp_path/'src/omni'; source.mkdir(parents=True)
    marker = tmp_path/'executed'
    (source/'config.py').write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nclass Example: pass\n",encoding='utf-8')
    (tmp_path/'data').mkdir(); (tmp_path/'data/private.py').write_text('SECRET_DATA_CANARY')
    (tmp_path/'.env').write_text('SECRET_KEY_CANARY')
    result = build_index(tmp_path)
    assert not marker.exists()
    assert len(result['files']) == 1 and result['files'][0]['symbols'][0]['name'] == 'Example'
    assert 'SECRET_' not in json.dumps(result)
    assert result == build_index(tmp_path)
    assert render_documents(result) == render_documents(result)


def test_repository_sources_have_unique_owners_and_resolvable_flows():
    result = build_index()
    assert not result['warnings']
    paths = [path for module in result['modules'] for path in module['files']]
    assert len(paths) == len(set(paths)) == len(result['files'])
    assert any(path.endswith('desktop/uia.ps1') for path in paths)
    for module in result['modules']:
        assert module['files'], module['id']
        for path in module['docs'] + module['tests']:
            assert (PROJECT_ROOT/path).is_file(), path
    assert all(step['line'] for flow in result['flows'] for step in flow['steps'])


def test_oversized_or_invalid_source_cannot_produce_a_silent_partial_index(tmp_path):
    source = tmp_path/'src/omni'; source.mkdir(parents=True)
    file = source/'config.py'; file.write_text('x'*2_000_001)
    with pytest.raises(ValueError,match='超大'): build_index(tmp_path)
    file.write_text('def invalid(')
    with pytest.raises(SyntaxError): build_index(tmp_path)


def test_registry_metadata_is_a_copy_and_never_executes_tools():
    registry = ToolRegistry()
    registry.register(Tool(name='confirm-canary', description='metadata', toolset='test', risk=Risk.CONFIRM,
                           requires_human=True, func=lambda _: pytest.fail('元数据接口执行了工具'),
                           parameters={'type':'object','properties':{'text':{'type':'string'}}}))
    row = registry.describe()[0]
    assert row['requires_human'] and row['risk'] == 'confirm' and not row['read_only']
    assert 'func' not in row
    row['parameters']['properties'].clear()
    assert 'text' in registry.get('confirm-canary').parameters['properties']


@pytest.mark.parametrize('path', ['/api/workbench/project', '/api/workbench/tools', '/api/workbench/extensions', '/api/workbench/docs/ARCHITECTURE.md'])
def test_project_apis_require_pairing_and_host_checks(workbench, path):
    client, _ = workbench
    assert client.get(path).status_code == 401
    pair(client)
    assert client.get(path,headers={'Host':'evil.example'}).status_code == 403
    assert client.get(path).status_code == 200


def test_api_index_matches_real_workbench_routes_and_request_models(workbench):
    client, runtime = workbench; pair(client)
    index = client.get('/api/workbench/project').json()
    declared = {(row['path'],row['method']) for row in index['endpoints'] if row['scope']=='workbench'}
    actual = set()
    # FastAPI 的 OpenAPI 展开 include_router；WebSocket 独立列出。
    for path, methods in client.get('/openapi.json').json()['paths'].items():
        if path.startswith('/api/') or path == '/pair/{code}':
            actual.update((path,method.upper()) for method in methods if method.upper() in {'GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'})
    actual.add(('/api/terminal/ws','WEBSOCKET'))
    actual.add(('/api/terminal/sessions/{identifier}/ws','WEBSOCKET'))
    assert declared == actual
    model = next(row for row in index['request_models'] if row['name']=='ChatIn')
    assert any(field['name']=='message' and field['required'] for field in model['fields'])
    runtime.registry.register(Tool(name='dynamic-canary',description='实际动态工具',parameters={},func=lambda _:pytest.fail('执行了工具'), toolset='plugin:canary'))
    row = next(row for row in client.get('/api/workbench/tools').json()['items'] if row['name']=='dynamic-canary')
    assert row['source'] is None and row['toolset']=='plugin:canary'
    runtime.registry.unregister('dynamic-canary')
    assert all(row['name']!='dynamic-canary' for row in client.get('/api/workbench/tools').json()['items'])


def test_docs_are_allowlisted_and_quarantined_plugin_is_not_activated(workbench, tmp_path):
    client, runtime = workbench; pair(client)
    for name in ('.env', 'config.yaml', 'web_secret', 'missing.md'):
        assert client.get('/api/workbench/docs/'+name).status_code == 404
    marker = tmp_path/'plugin-executed'
    host = runtime.plugins
    with zipfile.ZipFile(host.inbox_dir/'canary.zip','w') as archive:
        archive.writestr('manifest.json',json.dumps({'id':'catalog-canary','name':'目录插件','version':'1.0','description':'inert','entry':'plugin.py','permissions':['tools']}))
        archive.writestr('plugin.py',f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef register(ctx): pass\n")
    assert host.install_package('canary.zip')[0]
    result = client.get('/api/workbench/extensions').json()
    assert result['pending'][0]['id']=='catalog-canary' and not marker.exists()
    assert result['plugins']==[]


def test_real_browser_project_navigation_search_details_and_mobile(workbench, tmp_path):
    client, _ = workbench; pair(client)
    driver = BrowserDriver(headless=True, slow_mo_ms=0, profile_dir=tmp_path/'browser', shots_dir=tmp_path/'shots',action_timeout_s=20)
    def exercise():
        page = driver._ensure_page()
        errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
        def route(request):
            parsed = urlsplit(request.request.url)
            assert parsed.hostname=='127.0.0.1'
            response = client.get(parsed.path+('?' + parsed.query if parsed.query else ''))
            request.fulfill(status=response.status_code,body=response.content,headers={'Content-Type':response.headers.get('content-type','application/octet-stream')})
        page.route('**/*',route)
        page.goto('http://127.0.0.1/project/')
        page.locator('#status').get_by_text('索引就绪',exact=False).wait_for()
        page.screenshot(path=str(tmp_path/'project-desktop.png'),full_page=True)
        page.locator('#search').fill('内置终端')
        page.locator('.card').get_by_role('heading',name='内置终端',exact=True).click()
        assert page.locator('#detail-body').get_by_text('src/omni/channels/terminal/pool.py',exact=True).is_visible()
        page.locator('#close-detail').click();page.locator('#search').fill('')
        page.locator('[data-view="apis"]').click()
        assert page.locator('.flow').count()>=6
        page.locator('#category').select_option('module:http-conversation')
        assert sorted(page.locator('tbody .link-button').all_text_contents()) == ['/api/chat','/api/chat/stream','/api/runs']
        page.locator('#search').fill('/api/chat/stream');page.locator('tbody .link-button').click()
        assert page.locator('#detail-title').inner_text()=='POST /api/chat/stream'
        assert page.locator('#detail-body').get_by_text('ChatIn',exact=True).is_visible()
        page.get_by_role('button',name='查看所属模块与依赖',exact=True).click()
        assert page.locator('#detail-title').inner_text() == '对话与流式 API'
        page.locator('#close-detail').click();page.locator('#search').fill('')
        page.locator('[data-view="tools"]').click();page.locator('#search').fill('get_time')
        page.locator('.card').get_by_role('heading',name='get_time',exact=True).click()
        assert page.locator('#detail-body').get_by_text('参数 JSON Schema',exact=True).is_visible()
        page.locator('#close-detail').click();page.locator('#search').fill('')
        page.locator('[data-view="extensions"]').click()
        assert page.locator('#content').get_by_role('heading',name='Python 插件',exact=True).is_visible()
        page.locator('[data-doc="ARCHITECTURE.md"]').click()
        page.locator('#detail-body pre').get_by_text('OmniOperator 架构说明',exact=False).wait_for()
        page.locator('#close-detail').click()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.locator('[data-view="apis"]').click()
        page.locator('#category').select_option('module:http-memory')
        assert page.locator('tbody .link-button').count()>=8
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert page.locator('tbody tr').first.bounding_box()['height'] < 120
        page.screenshot(path=str(tmp_path/'project-mobile.png'),full_page=True)
        assert not errors
    try:
        driver._run(exercise,timeout_s=45)
    finally:
        assert '已关闭' in driver.close(wait_s=5)
