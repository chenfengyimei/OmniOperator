"""多设备定址、应用白名单、操作边界与归属；不接真实手机。"""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import time

from PIL import Image
import pytest

from omni.config import AndroidConfig
from omni.tools.android.adb import AdbDevice, AdbError
from omni.tools.android.driver import AndroidDriver
from test_android import FakeRunner,SAMPLE_XML


@pytest.fixture
def env(tmp_path):
    runner=FakeRunner()
    runner.expect('devices','List of devices attached\nA\tdevice\nB\tdevice\nC\tunauthorized\nD\toffline\n')
    runner.expect('cat /sdcard/omni_uidump.xml',SAMPLE_XML.replace('resource-id="com.x:id/input"','resource-id="com.x:id/input" focused="true"'))
    png=BytesIO();Image.new('RGB',(1080,2400)).save(png,format='PNG');runner.expect('screencap',png.getvalue())
    runner.expect('settings get secure default_input_method','com.gboard/.Gboard')
    runner.expect('settings get secure enabled_input_methods','com.gboard/.Gboard')
    runner.expect('resolve-activity','com.android.settings/.Settings')
    driver=AndroidDriver(adb=AdbDevice(adb_path='fake',runner=runner),cache_path=tmp_path/'cache/tree.json',allowed_apps=['com.android.settings'])
    return driver,runner


def writes(runner):
    return [argv for argv in runner.calls if any(s in ' '.join(argv) for s in ('input tap','input swipe','input text','input keyevent','ADB_INPUT_B64','am start'))]


def test_explicit_devices_and_observations_never_cross(env):
    driver,runner=env
    with pytest.raises(AdbError,match='多台'):driver.observe(session_id=1)
    a=driver.observe(serial='A',session_id=1);b=driver.observe(serial='B',session_id=1)
    assert a['serial']=='A' and b['serial']=='B'
    assert driver.execute_action('tap_element',a['observation'],serial='B',session_id=1,idx=0).status=='error'
    assert not writes(runner)
    assert driver.execute_action('tap_element',b['observation'],serial='B',session_id=1,idx=0).status=='unknown'
    assert writes(runner)[0][:3]==['fake','-s','B']
    assert driver._views['A']['observation']==a['observation']


@pytest.mark.parametrize('action,args',[('swipe',{'direction':'left'}),('swipe',{'direction':'right'}),('long_press',{'idx':0,'duration_ms':600}),('type_text',{'text':'中文输入'})])
def test_extended_actions_are_single_use(env,action,args):
    driver,runner=env;observation=driver.observe(serial='A',session_id=1)['observation']
    result=driver.execute_action(action,observation,serial='A',session_id=1,**args)
    assert result.status=='unknown' and result.data['serial']=='A'
    assert driver.execute_action(action,observation,serial='A',session_id=1,**args).status=='error'
    assert len(writes(runner))==1
    if action=='type_text':assert '-p com.android.adbkeyboard -a ADB_INPUT_B64' in ' '.join(writes(runner)[0])


def test_app_launch_checks_package_and_component_before_dispatch(env):
    driver,runner=env
    assert driver.list_apps(serial='A')['apps'][0]['component']=='com.android.settings/.Settings'
    token=driver.observe(serial='A',session_id=1)['observation']
    assert driver.execute_action('launch_app',token,serial='A',session_id=1,package='evil.app').status=='error'
    assert not writes(runner)
    runner.expect('resolve-activity','evil.app/.Settings')
    assert driver.execute_action('launch_app',token,serial='A',session_id=1,package='com.android.settings').status=='error'
    assert not writes(runner)
    runner.expect('resolve-activity','com.android.settings/.Settings')
    result=driver.execute_action('launch_app',token,serial='A',session_id=1,package='com.android.settings')
    assert result.status=='unknown' and len(writes(runner))==1
    with pytest.raises(ValueError):AndroidConfig(allowed_apps=['com.android.settings; reboot'])


def test_dump_cleanup_on_read_failure_and_total_deadline(tmp_path):
    class Runner(FakeRunner):
        def __call__(self,argv,timeout):
            if 'cat /sdcard/omni_uidump.xml' in argv:raise AdbError('fixture read failed')
            return super().__call__(argv,timeout)
    runner=Runner();device=AdbDevice(adb_path='fake',runner=runner)
    with pytest.raises(AdbError):device.dump_ui('A')
    assert any('rm /sdcard/omni_uidump.xml' in argv for argv in runner.calls)
    before=len(runner.calls)
    with device.deadline(-1):
        with pytest.raises(AdbError) as error:device.screen_size('A')
    assert error.value.code=='adb_timeout' and len(runner.calls)==before


def test_parallel_device_views_return_their_own_tokens(env):
    driver,_=env
    with ThreadPoolExecutor(2) as pool:
        observations=list(pool.map(lambda serial:driver.observe(serial=serial,session_id=1),['A','B']))
    for row in observations:assert driver._observations[row['observation']]['serial']==row['serial']


def test_device_http_scope_is_bound_to_paired_browser(env,tmp_path):
    from fastapi.testclient import TestClient
    from test_web import _client
    client,runtime=_client(tmp_path);runtime.android=env[0]
    try:
        assert [d['state'] for d in client.get('/api/android/devices').json()['devices']]==['device','device','unauthorized','offline']
        observed=client.get('/api/android/devices/A/observe').json()
        other=TestClient(client.app,base_url='http://127.0.0.1')
        other.post('/api/auth/pair',json={'code':client.app.state.web_auth.pairing_code})
        payload={'action':'tap_element','idx':0,'observation':observed['observation']}
        assert other.post('/api/android/devices/A/actions',json=payload).status_code==409
        assert not writes(env[1])
        assert client.post('/api/android/devices/A/actions',json=payload).json()['status']=='unknown'
        assert client.post('/api/android/devices/A/actions',json=payload).status_code==409
        other.close();client.cookies.clear()
        assert client.get('/api/android/devices').status_code==401
    finally:assert runtime.close()['complete'];client.close()


@pytest.mark.parametrize('stream',['stdout','stderr'])
def test_real_adb_client_reader_has_output_limits(stream):
    import sys
    from omni.tools.android.process import run_adb
    code=f'import sys;sys.{stream}.write("x"*1000000)'
    with pytest.raises(OverflowError):run_adb([sys.executable,'-c',code],5,limit=16384)


def test_real_adb_client_timeout_kills_only_its_owned_process(monkeypatch):
    import sys,subprocess
    from omni.tools.android import process
    original=subprocess.Popen;owned=[]
    def spawn(*args,**kwargs):
        child=original(*args,**kwargs);owned.append(child);return child
    monkeypatch.setattr(process.subprocess,'Popen',spawn)
    with pytest.raises(subprocess.TimeoutExpired):process.run_adb([sys.executable,'-c','import time;time.sleep(30)'],.2)
    assert owned[0].poll() is not None


def test_real_browser_device_choice_actions_and_mobile(env,tmp_path):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    from test_web import _client
    client,runtime=_client(tmp_path);runtime.android=env[0]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                def route(r):
                    parsed=urlsplit(r.request.url)
                    response=client.request(r.request.method,parsed.path,content=r.request.post_data,headers={'Content-Type':'application/json'})
                    r.fulfill(status=response.status_code,body=response.content,headers={'Content-Type':response.headers.get('content-type','')})
                page.route('**/*',route);page.goto('http://127.0.0.1/android/')
                assert not env[1].calls
                page.locator('#detect').click()
                page.wait_for_function('document.querySelectorAll("#devices option").length===5')
                assert page.locator('#devices').input_value()==''
                page.locator('#devices').select_option('A');page.locator('#observe').click()
                page.locator('#elements option').first.wait_for(state='attached')
                page.locator('#long').click()
                page.locator('#status').get_by_text('已长按',exact=False).wait_for()
                assert len(writes(env[1]))==1
                page.locator('#devices').select_option('B')
                assert page.locator('#tree').inner_text()=='已切换设备，请重新观察。'
                assert page.locator('#screen').is_hidden()
                page.locator('#text').fill('不应在设备切换后直接输入');page.locator('#type').click()
                page.locator('#status').get_by_text('请先刷新树',exact=False).wait_for()
                assert len(writes(env[1]))==1
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(tmp_path/'android-mobile.png'),full_page=True)
                assert not errors
            finally:browser.close()
    finally:assert runtime.close()['complete'];client.close()
