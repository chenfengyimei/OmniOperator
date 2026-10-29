"""全项目目录解析与所有消费者的集成，使用临时目录和假外部依赖。"""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from test_agent_storage import spaces, config_for
from omni.bootstrap import build_runtime
from omni.system.agent_spaces.inspection import load_runtime_paths


def organized(spaces):
    data=config_for(spaces,active='default')
    data['application']={'data_root':str(spaces.legacy.data/'organized'),
                         'workspace':'workspace','agents_root':'agents'}
    return data


def test_global_layout_freezes_all_consumers_and_is_read_only_until_start(spaces):
    data=organized(spaces);root=spaces.legacy.data/'organized'
    before=list(spaces.legacy.data.rglob('*'))
    preview=spaces.preview_application(data)
    assert list(spaces.legacy.data.rglob('*'))==before
    assert Path(preview['workspace'])==root/'workspace'
    spaces.save(data,revision='',acknowledge=True)
    paths=spaces.selected().paths
    assert paths.memory==root/'memory'/'memory.db'
    assert paths.resident==root/'memory'/'MEMORY.md'
    assert paths.history==root/'memory'/'history'
    assert paths.state==root/'runtime'/'state.db'
    assert paths.run==spaces.legacy.run and paths.service_logs==spaces.legacy.service_logs
    assert paths.env==spaces.legacy.env and paths.config==spaces.legacy.config
    runtime=build_runtime(config_path=spaces.legacy.config,data_dir=spaces.legacy.data,
                          directory_catalog=spaces.path,start_background_workers=False)
    try:
        assert runtime.paths==paths
        assert runtime.workspace.root==root/'workspace'
        assert runtime.command_jobs.root==root/'workspace'
        assert runtime.config.terminal.roots==[str(root/'workspace')]
        assert runtime.data_dir==spaces.legacy.data
        runtime.store.save_fact('全局根中的事实')
        assert paths.memory.exists() and paths.state.exists()
        assert not (spaces.legacy.data/'memory.db').exists()
        assert not (spaces.legacy.data/'state.db').exists()
        assert runtime.registry.get('peek_command').func({'command':'dir'}).status=='ok'
        inspected=load_runtime_paths(data_dir=spaces.legacy.data,config_path=spaces.legacy.config,
                                     directory_catalog=spaces.path)
        assert inspected==runtime.paths
    finally:
        assert runtime.close()['complete']


def test_old_client_cannot_silently_reset_global_paths(spaces):
    data=organized(spaces);snapshot=spaces.save(data,revision='',acknowledge=True)
    del data['application']
    with pytest.raises(ValueError,match='旧客户端'):spaces.preview(data)
    with pytest.raises(ValueError,match='旧客户端'):spaces.save(data,revision=snapshot['revision'],acknowledge=True)
    assert spaces.snapshot()==snapshot


@pytest.mark.parametrize('field',['data_root','workspace','agents_root'])
def test_explicit_global_paths_cannot_escape_injected_data_root(spaces,field):
    data=organized(spaces);data['application'][field]=str(spaces.legacy.data.parent/'escape')
    with pytest.raises(ValueError,match='隔离'):spaces.preview(data)


@pytest.mark.parametrize('target',['memory','runtime','private','logs','backups','agents','skills'])
def test_global_workspace_cannot_expose_private_or_other_agent_data(spaces,target):
    data=organized(spaces);data['application']['workspace']=target
    with pytest.raises(ValueError,match='覆盖'):spaces.preview(data)


def test_global_storage_cannot_cover_fixed_control_area(spaces):
    data=organized(spaces);data['application']['data_root']=str(spaces.legacy.data/'run'/'payload')
    with pytest.raises(ValueError,match='控制'):spaces.preview(data)


def test_default_files_cannot_expose_custom_memory_under_innocent_name(spaces):
    data=organized(spaces)
    data['agents'][0]['directories']={'files':'secrets','memory':'secrets/notes'}
    with pytest.raises(ValueError,match='覆盖'):spaces.preview(data)


def test_legacy_catalog_keeps_all_original_paths(spaces):
    chosen=spaces.selected()
    for field in ('memory','state','skills','resident','history','cache','logs','run','cookies','browser_profile'):
        assert getattr(chosen.paths,field)==getattr(spaces.legacy,field)
    assert not spaces.path.exists()


def test_asr_model_cache_identity_is_bound_to_selected_directory(tmp_path,monkeypatch):
    from omni.tools.bilibili import asr
    calls=[]
    def model(*args,**kwargs):
        result=object();calls.append((kwargs,result));return result
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=model))
    monkeypatch.setattr(asr,'_MODEL',None);monkeypatch.setattr(asr,'_MODEL_KEY',None)
    a,b=tmp_path/'writer'/'models',tmp_path/'research'/'models'
    first=asr._get_model('small','cpu','int8',download_root=a)
    assert asr._get_model('small','cpu','int8',download_root=a) is first
    assert asr._get_model('small','cpu','int8',download_root=b) is not first
    assert [row[0]['download_root'] for row in calls]==[str(a),str(b)]
    assert not a.exists() and not b.exists()


@pytest.mark.parametrize('fail',[False,True])
def test_asr_audio_temporary_files_follow_injected_cache_and_cleanup(tmp_path,monkeypatch,fail):
    from omni.tools.bilibili import asr
    from omni.tools.bilibili.tools import register_bilibili_tools
    from omni.tools.registry import ToolRegistry
    from test_bilibili_tools import FakeAPI
    seen=[];cache=tmp_path/'cache'/'asr'
    def transcribe(path,**kwargs):
        assert path.is_relative_to(cache/'audio') and path.exists()
        assert kwargs['download_root']==cache/'models'
        seen.append(path)
        if fail:raise RuntimeError('测试转写失败')
        return '隔离转写'
    monkeypatch.setattr(asr,'transcribe',transcribe)
    registry=ToolRegistry();register_bilibili_tools(registry,FakeAPI(),cache_root=cache)
    assert not cache.exists()
    result=registry.dispatch('bilibili_video_transcript',{'target':'BV1GJ411x7h7'})
    assert ('测试转写失败' if fail else '隔离转写') in result
    assert seen and not seen[0].parent.exists()


def test_env_file_follows_explicit_entry_config_and_respects_existing_value(tmp_path,monkeypatch):
    from omni.config import load_env
    key='OMNI_STORAGE_TEST_KEY'
    config=tmp_path/'alternate'/'config'/'config.yaml'
    config.parent.mkdir(parents=True)
    (config.parent.parent/'.env').write_text(key+'=alternate-value\n',encoding='utf-8')
    monkeypatch.setenv('OMNI_CONFIG_PATH',str(config));monkeypatch.delenv(key,raising=False)
    load_env()
    import os
    assert os.environ[key]=='alternate-value'
    monkeypatch.setenv(key,'explicit-value');load_env()
    assert os.environ[key]=='explicit-value'


def test_previous_extra_root_cannot_read_new_private_memory(spaces):
    destination=spaces.legacy.data/'organized'
    spaces.legacy.config.write_text('review:\n  enabled: false\nfiles:\n  extra_roots:\n    - '+destination.as_posix()+'\n',encoding='utf-8')
    spaces.save(organized(spaces),revision='',acknowledge=True)
    runtime=build_runtime(config_path=spaces.legacy.config,data_dir=spaces.legacy.data,
                          directory_catalog=spaces.path,start_background_workers=False)
    try:
        for path in (runtime.paths.resident,runtime.paths.history/'snapshot.txt',runtime.paths.data/'plugins'/'community.json'):
            with pytest.raises(ValueError,match='私有数据'):runtime.workspace.resolve(str(path))
        assert runtime.workspace.resolve(str(runtime.paths.files/'result.txt'))
    finally:
        assert runtime.close()['complete']


def test_narrower_extra_root_cannot_bypass_documents_read_only(tmp_path):
    from omni.tools.files.paths import Workspace
    documents=tmp_path/'documents';nested=documents/'course'
    workspace=Workspace(root=tmp_path/'workspace',extra_roots=[documents,nested],read_only_roots=[documents])
    with pytest.raises(ValueError,match='只读'):workspace.resolve(str(nested/'notes.txt'),for_write=True)
    assert workspace.resolve(str(nested/'notes.txt'))
