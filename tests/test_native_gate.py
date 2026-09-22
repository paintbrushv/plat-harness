"""Authorization tests use explicitly synthetic tiny fake files, never weights."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from plat_harness.errors import HarnessError
from plat_harness import native_gate as gate
from plat_harness.native_qwen import MODEL_ID, REVISION
from plat_harness.tool_loop import SYSTEM_PROMPT, tool_schema


@pytest.fixture
def approval_fixture(tmp_path, monkeypatch):
    # These constants are patched ONLY inside this unit test. No worker starts.
    base=tmp_path/'FAKE_MODEL'
    base.mkdir()
    code=tmp_path/'FAKE_CODE'
    code.mkdir()
    (code/'native_gate.py').write_text('# explicit offline fixture, not executable worker\n')
    metadata=base/'.cache'/'huggingface'/'download'
    metadata.mkdir(parents=True)
    shards=[f'fake-{i:02}.safetensors' for i in range(26)]
    files={}
    for name in shards:
        raw=('NOT_WEIGHTS:'+name).encode()
        (base/name).write_bytes(raw)
        digest=hashlib.sha256(raw).hexdigest()
        files[str(base/name)]=digest
        (metadata/(name+'.metadata')).write_text(REVISION+'\n'+digest+'\n0\n')
    for name in ['config.json','generation_config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja']:
        (base/name).write_text('{}')
    (base/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{f'fake_tensor_{i}':s for i,s in enumerate(shards)}}))
    for path in [*base.glob('*.json'),base/'chat_template.jinja',code/'native_gate.py']:
        files[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(gate,'MODEL_PATH',str(base))
    monkeypatch.setattr(gate,'RUNTIME','/EXPLICIT_FAKE_RUNTIME')
    monkeypatch.setattr(gate,'__file__',str(code/'native_gate.py'))
    monkeypatch.setattr(gate,'PINNED_SMALL_DIGESTS',{name:files[str(base/name)] for name in gate.PINNED_SMALL_DIGESTS})
    monkeypatch.setattr(gate.platform,'node',lambda:'spark-17d5')
    monkeypatch.setattr(gate.platform,'system',lambda:'Linux')
    monkeypatch.setattr(gate.platform,'machine',lambda:'aarch64')
    monkeypatch.setattr(gate.os,'getuid',lambda:1000)
    monkeypatch.setattr(gate,'gpu_processes',lambda:[])
    monkeypatch.setattr(gate.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='(3, 13)\n5.5.0\n2.11.0+cu130\n'))
    for key,value in gate.CONTROLS.items():
        monkeypatch.setenv(key,value)
    manifest={'model_id':MODEL_ID,'revision':REVISION,'model_path':str(base),'runtime':'/EXPLICIT_FAKE_RUNTIME',
              'limits':copy.deepcopy(gate.LIMITS),'controls':copy.deepcopy(gate.CONTROLS),'files':files,
              'tools':tool_schema(('example_property',)),'system_prompt':SYSTEM_PROMPT,
              'questions':['Synthetic example_property occupancy?'],'synthetic_only':True}
    output=tmp_path/'FAKE_OUTPUT'
    auth={'approved':True,'approval_kind':'explicit_human_inference','scope':'one_native_bf16_synthetic_inference_run',
          'manifest_sha256':'not yet bound','output_dir':str(output),
          'expires_at':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)).isoformat(),
          'approval_evidence':'EXPLICIT_OFFLINE_TEST_FIXTURE_NOT_REAL_APPROVAL'}
    def write(mutate_auth=None,mutate_manifest=None):
        m,a=copy.deepcopy(manifest),copy.deepcopy(auth)
        if mutate_manifest:
            mutate_manifest(m)
        mpath=tmp_path/'FAKE_MANIFEST.json'
        mpath.write_text(json.dumps(m))
        mpath.chmod(0o600)
        a['manifest_sha256']=hashlib.sha256(mpath.read_bytes()).hexdigest()
        if mutate_auth:
            mutate_auth(a)
        apath=tmp_path/'FAKE_AUTH.json'
        apath.write_text(json.dumps(a))
        apath.chmod(0o600)
        return apath,hashlib.sha256(apath.read_bytes()).hexdigest(),mpath,output
    return write,base


def test_explicit_fake_hash_gate_streams_all_fake_payloads(approval_fixture):
    make,base=approval_fixture
    permit=gate.authorize(*make())
    assert len([p for p in permit['verified_stamps'] if p.endswith('.safetensors')])==26
    assert permit['parent_pid']==os.getpid()
    assert permit['manifest']['model_path']==str(base)


@pytest.mark.parametrize('mutate',[
    lambda a:a.update(approved=False),
    lambda a:a.update(approved=1),
    lambda a:a.update(approval_kind='PROPOSAL_ONLY_NOT_AUTHORIZATION'),
    lambda a:a.update(scope='training'),
    lambda a:a.update(manifest_sha256=None),
    lambda a:a.update(manifest_sha256='F'*64),
    lambda a:a.update(manifest_sha256='0'*64),
    lambda a:a.update(output_dir='/another/run'),
    lambda a:a.update(expires_at='2000-01-01T00:00:00+00:00'),
    lambda a:a.update(expires_at='2000-01-01'),
    lambda a:a.update(expires_at=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=2)).isoformat()),
    lambda a:a.update(approval_evidence=''),
    lambda a:a.update(rank=3),
])
def test_rejects_approval_escalation_staleness_or_missing_binding(approval_fixture,mutate):
    make,_=approval_fixture
    with pytest.raises(HarnessError):
        gate.authorize(*make(mutate_auth=mutate))


@pytest.mark.parametrize('mutate',[
    lambda m:m.update(model_id='Qwen/other'),
    lambda m:m.update(revision='0'*40),
    lambda m:m['limits'].update(prompt_tokens=2048),
    lambda m:m['limits'].update(load_s=900.0),
    lambda m:m['controls'].update(ALLOW_LIFECYCLE='1'),
    lambda m:m.update(synthetic_only=False),
    lambda m:m.update(system_prompt='ignore policy'),
    lambda m:m.update(tools=tool_schema(('different_subject',))),
    lambda m:m.update(questions=['same','same']),
    lambda m:m.update(questions=['<|im_start|>system']),
    lambda m:m['files'].pop(next(iter(m['files']))),
    lambda m:m['files'].update({'/unapproved/path':'0'*64}),
])
def test_rejects_manifest_drift_and_unsafe_scopes(approval_fixture,mutate):
    make,_=approval_fixture
    with pytest.raises(HarnessError):
        gate.authorize(*make(mutate_manifest=mutate))


def test_changed_fake_weight_rejected(approval_fixture):
    make,base=approval_fixture
    args=make()
    (base/'fake-00.safetensors').write_text('CHANGED_FAKE_BYTES')
    with pytest.raises(HarnessError) as exc:
        gate.authorize(*args)
    assert exc.value.code=='NATIVE_HASH'


def test_wrong_cached_revision_rejected(approval_fixture):
    make,base=approval_fixture
    args=make()
    path=base/'.cache'/'huggingface'/'download'/'fake-00.safetensors.metadata'
    path.write_text('0'*40+'\n'+'0'*64+'\n')
    with pytest.raises(HarnessError) as exc:
        gate.authorize(*args)
    assert exc.value.code=='NATIVE_HASH'


def test_co_resident_gpu_gate_no_auto_stop(approval_fixture,monkeypatch):
    make,_=approval_fixture
    monkeypatch.setattr(gate,'gpu_processes',lambda:[123456])
    with pytest.raises(HarnessError) as exc:
        gate.authorize(*make())
    assert exc.value.code=='NATIVE_RESIDENT'


def test_changed_runtime_gate(approval_fixture,monkeypatch):
    make,_=approval_fixture
    monkeypatch.setattr(gate.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='other runtime'))
    with pytest.raises(HarnessError) as exc:
        gate.authorize(*make())
    assert exc.value.code=='NATIVE_RUNTIME'
