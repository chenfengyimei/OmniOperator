"""全局设置与当前运行目录分离、用途索引及真实浏览器交互。"""
from pathlib import Path

from test_agent_storage import client, spaces
from test_ui_flows import ui


def payload(snapshot):
    return {key:snapshot[key] for key in ('version','active','application','agents')}


def test_global_settings_preview_and_save_do_not_move_live_data(client):
    web,runtime=client
    runtime.store.save_fact('当前数据库仍然保留这条长期记忆')
    before=web.get('/api/storage').json();data=payload(before)
    destination=runtime.directories.legacy.data/'next-storage'
    data['application']={'data_root':str(destination),'workspace':'workspace','agents_root':'agents'}
    request={'revision':before['revision'],'catalog':data}
    preview=web.post('/api/storage/preview',json=request)
    assert preview.status_code==200,preview.text
    assert Path(preview.json()['application_resolved']['data_root'])==destination
    assert not destination.exists()
    response=web.put('/api/storage',json={**request,'acknowledge':True})
    assert response.status_code==200,response.text
    after=response.json()
    assert after['restart_required']
    assert after['running_application']==before['running_application']
    assert after['runtime_paths']==before['runtime_paths']
    assert after['public_roots']==before['public_roots']
    assert runtime.store.fact_rows() and not destination.exists()


def test_inventory_describes_memory_profiles_without_reading_them(client):
    web,runtime=client
    runtime.store.save_fact('不应该泄露在用途索引中的内容')
    response=web.get('/api/storage');data=response.json()
    rows={row['id']:row for row in data['inventory']}
    assert Path(rows['memory']['path'])==runtime.paths.memory
    assert '档案' in rows['memory']['contents'] and '向量' in rows['memory']['contents']
    assert Path(rows['history']['path'])==runtime.paths.history
    assert Path(rows['plugins']['path'])==runtime.paths.data/'plugins'
    assert '不应该泄露在用途索引中的内容' not in response.text
    assert '不包含' in rows['browser_profile']['contents']
    assert {row['category'] for row in rows.values()}>={'固定控制区','持久数据','公开文件'}


def test_global_settings_invalid_path_has_no_partial_save(client):
    web,runtime=client;before=web.get('/api/storage').json();data=payload(before)
    data['application']={'data_root':'../escape'}
    response=web.put('/api/storage',json={'revision':before['revision'],'catalog':data,'acknowledge':True})
    assert response.status_code==422
    assert web.get('/api/storage').json()['revision']==before['revision']


def test_global_directory_form_preview_confirmation_and_inventory(ui):
    page,web,runtime,errors=ui
    page.goto('http://127.0.0.1/storage/');page.locator('#agents button').first.wait_for()
    destination=runtime.directories.legacy.data/'organized'
    page.locator('#app-data_root').fill(str(destination))
    page.locator('#app-workspace').fill('workspace')
    page.locator('#app-agents_root').fill('agents')
    page.locator('#preview').click()
    page.locator('#layout-preview').get_by_text('organized',exact=False).wait_for()
    assert not destination.exists()
    page.locator('#save').click();page.locator('#ui-dialog').wait_for()
    assert 'organized' in page.locator('#ui-dialog').inner_text()
    assert page.locator('#app-data_root').is_disabled()
    page.get_by_role('button',name='保存下次启动设置',exact=True).click()
    page.locator('#running').get_by_text('需重启',exact=False).wait_for()
    assert web.get('/api/storage').json()['application']['workspace']=='workspace'
    assert not destination.exists()
    page.locator('[data-view="inventory"]').click()
    page.locator('#storage-inventory').get_by_text('长期记忆与档案',exact=True).wait_for()
    assert str(runtime.paths.memory) in page.locator('#storage-inventory').inner_text()
    assert not errors


def test_global_values_survive_agent_switch_and_mobile_inventory(ui):
    page,_,runtime,errors=ui
    page.set_viewport_size({'width':390,'height':900})
    page.goto('http://127.0.0.1/storage/');page.locator('#agents button').first.wait_for()
    page.locator('#app-workspace').fill(str(runtime.directories.legacy.data/'workspace'))
    page.locator('#new-id').fill('organizer');page.locator('#new-name').fill('资料整理')
    page.locator('#new-agent button').click();page.locator('#agents button').first.click()
    assert page.locator('#app-workspace').input_value().endswith('workspace')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.locator('[data-view="inventory"]').click()
    assert page.locator('#storage-inventory .inventory-row').count()>15
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors
