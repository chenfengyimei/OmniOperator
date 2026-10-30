"""生成 README 演示截图：隔离 runtime 种子演示对话 + 真实 Chromium 渲染，零真实数据。

用法：
    uv run --no-sync python scripts/readme_shots.py [--out docs/assets]

- 复用 tests/test_ui_flows.py 的 TestClient 路由拦截模式：不起真实端口、不碰真机 data，
  静态资源与 API 全部经内存应用应答；
- 种子内容只用占位数据（无真实 QQ 号/密钥/会话名/对话）——README 是公开资产，
  演示图必须可复现且零隐私；
- 产物：workbench.png（对话 + 展开的调用链）、hub.png（设置中心·小鱼大脑）。
  重新生成后注意：README 里引用的是相对路径 docs/assets/…，同名覆盖无需改链接。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.safety.gates import DenyApprover

ROOT = Path(__file__).resolve().parents[1]
VIEWPORT = {'width': 1440, 'height': 900}


def _seed(client: TestClient, runtime) -> None:
    """按真实时序回放一条演示对话：run 先建、事件后追加、消息最后落库。"""
    sid = runtime.store.new_session('和小鱼聊 B 站')
    rid = 'run-readme-demo'
    runtime.runs.create(run_id=rid, session_id=sid, channel='web', principal='demo', owner='demo')
    runtime.runs.append_event(rid, 'run_started')
    runtime.runs.append_event(rid, 'model_start', step=1)
    runtime.runs.append_event(rid, 'model_end', step=1, text_chars=42,
                              tool_calls=['bilibili_video_info'],
                              prompt_tokens=520, completion_tokens=60)
    runtime.runs.append_event(rid, 'tool', name='bilibili_video_info', status='ok',
                              args='{"url":"https://www.bilibili.com/video/BV1demo"}',
                              excerpt='演示视频 · 12 分钟 · 8.4 万播放',
                              elapsed_ms=2130.4)
    runtime.runs.append_event(rid, 'tool', name='remember', status='ok',
                              args='{"content":"视频要点：四入口一内核；红线动作一律人工确认"}',
                              excerpt='已写入长期记忆', elapsed_ms=412.0)
    runtime.runs.append_event(rid, 'tool', name='cron_once', status='ok',
                              args='{"at":"21:30","deliver_to_qq":true}',
                              excerpt='已排定今晚 21:30 提醒', elapsed_ms=634.7)
    runtime.runs.append_event(rid, 'answer')
    runtime.runs.touch(rid, status='done')

    runtime.store.append_message(sid, 'user', '帮我看看这个B站视频讲了什么，晚上顺口提醒我')
    runtime.store.append_message(
        sid, 'assistant',
        '看完了！视频讲的是给个人电脑装一个「像人」的常驻 Agent：四个入口共用一个内核，'
        '红线动作一律人工确认。要点已经记进长期记忆，今晚 21:30 通过 QQ 提醒你。')
    runtime.store.append_message(sid, 'user', '好，晚上见。')
    runtime.store.append_message(sid, 'assistant', '好的，到点见～')

    # 第二条轻量会话，让列表更接近真实使用形态（同样是占位内容）。
    sid2 = runtime.store.new_session('昨天的整理')
    runtime.store.append_message(sid2, 'user', '把昨天聊的偏好整理进档案')
    runtime.store.append_message(sid2, 'assistant', '整理好了：偏好短回答、重视测试证据。')


def _route_through(client: TestClient):
    def route(requested):
        url = urlsplit(requested.request.url)
        response = client.request(requested.request.method,
                                  url.path + ('?' + url.query if url.query else ''),
                                  content=requested.request.post_data,
                                  headers={'Content-Type': 'application/json'})
        requested.fulfill(status=response.status_code, body=response.content,
                          headers={'Content-Type': response.headers.get('content-type', '')})
    return route


def main() -> int:
    parser = argparse.ArgumentParser(description='生成 README 演示截图（隔离数据，零隐私）')
    parser.add_argument('--out', type=Path, default=ROOT / 'docs' / 'assets',
                        help='输出目录（默认 docs/assets）')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='omni-readme-shots-') as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / 'config.yaml'
        # napcat_dir 必须指向不存在的演示路径：默认值会指向本机真实 NapCat 目录，
        # 服务面板会读出真实快速登录 uin——README 公开图里绝不能出现。
        config_path.write_text(
            'persona:\n'
            '  name: 小鱼\n'
            'system:\n'
            '  napcat_dir: "C:/napcat-demo"\n',
            encoding='utf-8')
        runtime = build_runtime(
            approver=DenyApprover(),
            db_path=tmp_path / 'm.db',
            config_path=config_path,
            skills_root=None,
            data_dir=tmp_path / 'data',  # 隔离真机运行态
            start_background_workers=False,
        )
        try:
            app = create_app(runtime)
            client = TestClient(app, base_url='http://127.0.0.1')
            assert client.post('/api/auth/pair', json={'code': app.state.web_auth.pairing_code}).status_code == 200
            _seed(client, runtime)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport=VIEWPORT)
                errors: list[str] = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('**/*', _route_through(client))
                try:
                    page.goto('http://127.0.0.1/')
                    page.locator('#sessions li').filter(has_text='和小鱼聊 B 站').click()
                    page.wait_for_selector('.msg.assistant .scaffolds', timeout=10_000)
                    chain = page.locator('.msg.assistant .scaffolds').first
                    chain.locator('.scaf-toggle').click()
                    page.wait_for_timeout(400)
                    (args.out / 'workbench.png').write_bytes(page.screenshot())
                    print(f'workbench -> {args.out / "workbench.png"}')

                    page.locator('button.hub-open').click()
                    page.wait_for_selector('#hub', timeout=10_000)
                    page.wait_for_timeout(400)
                    (args.out / 'hub.png').write_bytes(page.screenshot())
                    print(f'hub -> {args.out / "hub.png"}')
                finally:
                    page.close()
                    browser.close()
            if errors:
                print('PAGE ERRORS:', errors, file=sys.stderr)
                return 1
        finally:
            runtime.close()
    print('done (0 page errors)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
