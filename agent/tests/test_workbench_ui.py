"""真实 Chromium 验证工作台新增页签可到达，不启动真实服务。"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from omni.tools.browser.driver import BrowserDriver

STATIC = Path(__file__).resolve().parents[1] / 'src/omni/channels/web_static'


def test_desktop_and_publication_tabs_are_reachable(tmp_path):
    driver = BrowserDriver(headless=True, slow_mo_ms=0, profile_dir=tmp_path / 'profile',
                           shots_dir=tmp_path / 'shots', action_timeout_s=20)
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    def exercise():
        page = driver._ensure_page()
        def route(request):
            url = request.request.url
            path = urlsplit(url).path
            if url == 'http://127.0.0.1/':
                request.fulfill(body=html, content_type='text/html')
            elif path.startswith(('/ui/', '/vendor/', '/memory/')):
                # 工作台样式/脚本已外置（Phase 1 token 统一）：从磁盘喂真实文件，页签显隐逻辑才成立
                asset = STATIC / path.lstrip('/')
                if asset.is_file():
                    ctype = 'text/css' if path.endswith('.css') else 'text/javascript'
                    request.fulfill(body=asset.read_text(encoding='utf-8'), content_type=ctype)
                else:
                    request.fulfill(status=404, body='', content_type='text/plain')
            elif url.endswith('/api/publications'):
                request.fulfill(body=json.dumps({'enabled': False, 'items': []}), content_type='application/json')
            elif url.endswith('/api/desktop/windows'):
                request.fulfill(body=json.dumps({'enabled': False, 'windows': []}), content_type='application/json')
            else:
                request.fulfill(status=503, body='{}', content_type='application/json')
        page.route('**/*', route)
        page.goto('http://127.0.0.1/')
        for name in ('desktop', 'publications', 'facts'):
            page.locator(f'[data-tab="{name}"]').click()
            assert page.locator('.tab-body:visible').count() == 1
            assert page.locator(f'#tab-{name}').is_visible()
        page.locator('[data-tab="publications"]').click()
        page.locator('#publication-list').get_by_text('B站写操作未启用', exact=False).wait_for()
    try:
        driver._run(exercise)
    finally:
        assert '已关闭' in driver.close(wait_s=5)
