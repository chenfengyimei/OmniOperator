"""真实 Chromium 验证 Agent 设置、目标归属、中文资料和手机布局。"""
from test_ui_flows import ui,delay_script
import pytest


def test_create_agent_preview_save_and_pending_restart(ui):
    page,client,runtime,errors=ui
    page.goto('http://127.0.0.1/storage/')
    page.locator('#agents button').first.wait_for()
    page.locator('#new-id').fill('writer');page.locator('#new-name').fill('写作助手');page.locator('#new-agent button').click()
    page.locator('#dir-workspace').fill('projects');page.locator('#agent-default').check();page.locator('#preview').click()
    page.locator('#layout-preview').get_by_text('projects',exact=False).wait_for()
    root=runtime.directories.legacy.data/'agents'/'writer'
    assert not root.exists()
    page.locator('#save').click();page.locator('#ui-dialog').wait_for()
    assert '旧数据不会自动搬移' in page.locator('#ui-dialog').inner_text()
    assert page.locator('#agent-name').is_disabled()
    page.get_by_role('button',name='保存下次启动设置',exact=True).click()
    page.locator('#running').get_by_text('需重启',exact=False).wait_for()
    assert client.get('/api/storage').json()['active']=='writer'
    assert runtime.paths.agent_id=='default'
    assert not root.exists()
    assert not errors


def test_file_upload_browse_and_preview(ui):
    page,_,runtime,errors=ui
    page.goto('http://127.0.0.1/storage/');page.locator('#agents button').first.wait_for()
    page.locator('[data-view="files"]').click();page.locator('#scope').select_option('documents')
    page.locator('#upload-file').set_input_files({'name':'中文资料.txt','mimeType':'text/plain','buffer':'只属于当前 Agent 的资料'.encode('utf-8')})
    page.locator('#upload').click();page.get_by_role('button',name='上传文件',exact=True).click()
    page.locator('.file-row').filter(has_text='中文资料.txt').wait_for()
    page.locator('.file-row').filter(has_text='中文资料.txt').click()
    page.locator('#file-preview').get_by_text('只属于当前 Agent 的资料',exact=False).wait_for()
    assert (runtime.paths.documents/'中文资料.txt').is_file()
    assert not errors


def test_settings_entry_opens_independent_agent_page(ui):
    page,_,_,errors=ui
    page.goto('http://127.0.0.1/');page.locator('.ui-settings:visible,button.hub-open:visible').first.click()
    link=page.locator('.hub-nav a[href="/storage/"]')
    link.wait_for()
    assert link.is_visible() and link.get_attribute('target')=='_blank'
    assert page.locator('.ui-global-nav a[href="/storage/"]').get_attribute('target')=='_blank'
    assert not errors


@pytest.mark.parametrize('width',[390,760,1440])
def test_storage_page_layout_and_no_implicit_directory_creation(ui,width):
    page,_,runtime,errors=ui
    page.set_viewport_size({'width':width,'height':900});page.goto('http://127.0.0.1/storage/')
    page.locator('#agents button').first.wait_for()
    page.locator('.ui-global-nav a[aria-current]').wait_for()
    assert page.locator('.ui-global-nav a[aria-current]').get_attribute('href')=='/storage/'
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.locator('[data-view="files"]').click()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not runtime.paths.files.exists() and not runtime.paths.documents.exists()
    assert not errors
