"""Real subprocess/group cleanup tests with EXPLICIT CPU fake workers.

The fake's token counts and latencies are test transport fields, not measurements
of Qwen. No torch/Transformers import occurs in these tests.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

from plat_harness.errors import HarnessError
from plat_harness.native_gate import LIMITS, authorize, hash_file, read_private
from plat_harness.native_qwen import MODEL_ID, REVISION, NativeModel, dumps
from plat_harness.native_supervisor import control, memory_check, snapshot
from plat_harness.tool_loop import LoopConfig, OpsMetricExecutor, SYSTEM_PROMPT, run_question, tool_schema

FAKE = r'''
import json, os, signal, subprocess, sys, time
from plat_harness.native_qwen import MODEL_ID, REVISION
mode = sys.argv[1]
if mode in ('load_hang', 'load_fork'):
    if mode == 'load_fork':
        child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
        print('FAKE_GRANDCHILD=' + str(child.pid), file=sys.stderr, flush=True)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
if mode == 'crash':
    sys.exit(17)
if mode == 'flood':
    print('x' * 131073, flush=True)
    time.sleep(60)
print(json.dumps({'type':'ready','model_id':MODEL_ID,'revision':REVISION, 'audit':{'FAKE_WORKER':True}}), flush=True)
for line in sys.stdin:
    req=json.loads(line)
    if mode in ('generation_hang', 'generation_fork'):
        if mode == 'generation_fork':
            subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
    if mode == 'badjson':
        print('{"type":"result","type":"error"}', flush=True)
        continue
    if mode == 'unsolicited':
        print('{}\n{}', flush=True)
        continue
    tool_messages=[m for m in req['messages'] if m['role']=='tool']
    if tool_messages:
        text=json.dumps({'answer_from':[tool_messages[-1]['tool_call_id']]})
    else:
        args={'metric_id':'physical_occupancy','context':'ops_actuals','asset_or_deal_id':'example_property'}
        text='<tool_call>\n<function=get_certified_metric>\n'+''.join('<parameter='+k+'>\n'+v+'\n</parameter>\n' for k,v in args.items())+'</function>\n</tool_call>'
    if mode == 'truncated':
        text=text[:-3]
    print(json.dumps({'type':'result','id':req['id'],'model_id':MODEL_ID,'revision':REVISION,
        'text':text,'finish_reason':'stop','prompt_tokens':600,'generated_tokens':80,'generation_s':0.01}), flush=True)
if mode == 'exit_nonzero':
    sys.exit(23)
'''

DRIVER = '''
import os, sys
from pathlib import Path
from plat_harness.tool_loop import SYSTEM_PROMPT, tool_schema
from plat_harness.native_supervisor import Store, supervise
os.umask(0o077)
store=Store(Path(sys.argv[1]))
scope={'system_prompt':SYSTEM_PROMPT,'tools':tool_schema(('example_property',)),
       'questions':['What is the latest available physical occupancy for example_property?', 'Second synthetic question']} if sys.argv[8]=='scope' else None
try:
    code=supervise(store, fixture_manifest=scope, fixture_worker=[sys.executable,'-B','-c',sys.argv[2],sys.argv[3]],
                   load_s=float(sys.argv[4]),generation_s=float(sys.argv[5]),whole_s=float(sys.argv[6]),max_generations=int(sys.argv[7]))
finally:
    store.close()
sys.exit(code)
'''


@pytest.fixture
def launched(tmp_path):
    procs = []
    def start(mode='normal', load_s=3, generation_s=2, whole_s=10, max_generations=4, ready=True, scope=False):
        root = tmp_path / ('run' + str(len(procs)))
        env = {**os.environ, 'CUDA_VISIBLE_DEVICES':'', 'PYTHONDONTWRITEBYTECODE':'1',
               'PYTHONPATH':str(Path(__file__).resolve().parents[1] / 'harness' / 'src')}
        p = subprocess.Popen([sys.executable,'-B','-c',DRIVER,str(root),FAKE,mode,str(load_s),str(generation_s),str(whole_s),str(max_generations),'scope' if scope else 'none'], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        procs.append((p,root))
        deadline = time.monotonic()+5
        target = root / ('ready.json' if ready else 'startup.json')
        while not target.exists():
            if p.poll() is not None or time.monotonic()>deadline:
                out,err=p.communicate(timeout=3)
                pytest.fail(f'fake supervisor startup failed {p.returncode}: {out!r} {err!r}')
            time.sleep(.01)
        info=json.loads(target.read_text())
        return p,root,info['endpoint']
    yield start
    for p,root in procs:
        if p.poll() is None:
            p.terminate()
        out,err=p.communicate(timeout=8)
        assert not err, err.decode()
        evidence=json.loads((root/'exit.json').read_text())
        assert evidence['cleanup']['cleanup_verified'], evidence
        assert evidence['cleanup']['remaining_group']==[]
        assert evidence['supervisor_exit_code']==p.returncode
        pid=json.loads((root/'startup.json').read_text())['worker_pid']
        assert not snapshot(pid)['group']


def messages():
    return [{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':'What is the latest available physical occupancy for example_property?'}]


def wait_exit(p,root):
    p.wait(timeout=8)
    return json.loads((root/'exit.json').read_text())


def test_native_model_real_interface_two_turns_and_success_exit(launched):
    p,root,endpoint=launched()
    model=NativeModel(endpoint, timeout_s=2)
    msg=messages()
    turn=model.complete(msg,tool_schema(('example_property',)))
    assert turn.finish_reason=='tool_calls'
    call=turn.tool_calls[0]
    msg += [{'role':'assistant','content':None,'tool_calls':[call]},
            {'role':'tool','tool_call_id':call['id'],'content':dumps({'answer_from':call['id']})}]
    final=model.complete(msg,tool_schema(('example_property',)))
    assert json.loads(final.content)=={'answer_from':[call['id']]}
    control(endpoint)
    result=wait_exit(p,root)
    assert result['status']=='COMPLETED' and result['actual_child_exit']==0
    assert result['generations']==2 and result['fixture_worker'] is True
    assert len(list(root.glob('request-*.json')))==2
    assert len(list(root.glob('response-*.json')))==2
    assert all((f.stat().st_mode & 0o777)==0o600 for f in root.iterdir() if f.is_file())


def test_existing_tool_loop_unchanged_with_real_deterministic_tool(launched,tmp_path):
    p,root,endpoint=launched()
    repo=Path(__file__).resolve().parents[1]
    sample=repo/'samples'
    config=LoopConfig(approved_roots=(sample,tmp_path),subjects=('example_property',),
                      run_dir=tmp_path/'tool-loop',max_steps=2,model_timeout_s=3,total_timeout_s=15)
    model=NativeModel(endpoint, timeout_s=2)
    result=run_question(messages()[1]['content'],model,config,
                        OpsMetricExecutor(config.approved_roots,ops_root=sample/'ops'))
    control(endpoint)
    exit_record=wait_exit(p,root)
    assert result['status']=='answered', result
    assert result['rendering']=='host_artifact_only'
    assert result['answers'][0]['citation']['sha256']
    assert exit_record['generations']==2 and exit_record['actual_child_exit']==0


@pytest.mark.parametrize('mode', ['load_hang','load_fork'])
def test_load_timeout_kills_whole_group_even_ignoring_term(launched,mode):
    p,root,_=launched(mode,load_s=.25,ready=False)
    result=wait_exit(p,root)
    assert result['status']=='NATIVE_LOAD_TIMEOUT'
    assert result['actual_child_exit'] in (-signal.SIGTERM,-signal.SIGKILL)
    if mode=='load_fork':
        assert 'SIGKILL' in result['cleanup']['signals']
        assert result['cleanup']['reaped_descendants']


@pytest.mark.parametrize('mode', ['generation_hang','generation_fork'])
def test_generation_timeout_kills_group(launched,mode):
    p,root,endpoint=launched(mode,generation_s=.25)
    with pytest.raises(HarnessError):
        NativeModel(endpoint,timeout_s=2).complete(messages(),tool_schema(('example_property',)))
    result=wait_exit(p,root)
    assert result['status']=='NATIVE_GENERATION_TIMEOUT'
    if mode=='generation_fork':
        assert result['cleanup']['reaped_descendants']


def test_client_disconnect_cancels_worker(launched):
    p,root,endpoint=launched('generation_fork',generation_s=5)
    with pytest.raises(HarnessError):
        NativeModel(endpoint,timeout_s=.15).complete(messages(),tool_schema(('example_property',)))
    assert wait_exit(p,root)['status']=='NATIVE_CLIENT_CANCELLED'


def test_signal_cancellation_idle(launched):
    p,root,_=launched()
    p.send_signal(signal.SIGTERM)
    assert wait_exit(p,root)['status']=='NATIVE_CANCELLED'


def test_control_cancellation(launched):
    p,root,endpoint=launched()
    control(endpoint,'cancel')
    assert wait_exit(p,root)['status']=='NATIVE_CANCELLED'


def test_whole_lifetime_is_bounded_even_idle(launched):
    p,root,_=launched(whole_s=.3)
    assert wait_exit(p,root)['status']=='NATIVE_WHOLE_TIMEOUT'


@pytest.mark.parametrize('mode,expected', [('badjson','NATIVE_JSON'),('unsolicited','NATIVE_PROTOCOL'),('truncated','NATIVE_CALLS')])
def test_malformed_worker_results_are_archived_and_fail_closed(launched,mode,expected):
    p,root,endpoint=launched(mode)
    with pytest.raises(HarnessError):
        NativeModel(endpoint,timeout_s=2).complete(messages(),tool_schema(('example_property',)))
    assert wait_exit(p,root)['status']==expected
    assert (root/'worker.stdout.jsonl').stat().st_size>0


def test_count_bound(launched):
    p,root,endpoint=launched(max_generations=1)
    model=NativeModel(endpoint,timeout_s=2)
    model.complete(messages(),tool_schema(('example_property',)))
    with pytest.raises(HarnessError):
        model.complete(messages(),tool_schema(('example_property',)))
    assert wait_exit(p,root)['status']=='NATIVE_GENERATION_LIMIT'


def test_nonzero_actual_exit_not_relabelled_success(launched):
    p,root,endpoint=launched('exit_nonzero')
    control(endpoint)
    result=wait_exit(p,root)
    assert result['actual_child_exit']==23 and result['supervisor_exit_code']==1


def test_exclusive_run_outputs(tmp_path):
    from plat_harness.native_supervisor import Store
    root=tmp_path/'evidence'
    store=Store(root)
    store.write('result.json',{'a':1})
    with pytest.raises(FileExistsError):
        store.write('result.json',{'a':2})
    store.close()
    with pytest.raises(FileExistsError):
        Store(root)
    assert json.loads((root/'result.json').read_text())=={'a':1}


def test_read_private_hash_and_symlink_gate(tmp_path):
    p=tmp_path/'auth.json'
    p.write_text('{"approved":false}')
    p.chmod(0o600)
    digest,_=hash_file(p)
    assert read_private(p,digest)[0]['approved'] is False
    with pytest.raises(HarnessError):
        read_private(p,'0'*64)
    link=tmp_path/'link'
    link.symlink_to(p)
    with pytest.raises(OSError):
        read_private(link,digest)


def test_missing_approval_blocks_before_any_model_import(tmp_path):
    with pytest.raises((HarnessError,FileNotFoundError)):
        authorize(tmp_path/'missing','0'*64,tmp_path/'manifest',tmp_path/'run')
    assert 'torch' not in sys.modules


@pytest.mark.parametrize('field,delta,expected', [('MemAvailable',-100*1024**3,'NATIVE_MEMORY_FLOOR'),
                                                ('SwapFree',-2*1024**3,'NATIVE_SWAP_LIMIT')])
def test_memory_stops(field,delta,expected):
    base={'mem':{'MemAvailable':120*1024**3,'SwapTotal':16*1024**3,'SwapFree':16*1024**3},'group_rss':0,'group_swap':0}
    changed=json.loads(json.dumps(base))
    changed['mem'][field]+=delta
    with pytest.raises(HarnessError) as exc:
        memory_check(changed,base)
    assert exc.value.code==expected


@pytest.mark.parametrize('mode,expected', [('crash','NATIVE_WORKER_EXIT'),('flood','NATIVE_OUTPUT_LIMIT')])
def test_actual_abnormal_load_exit_and_line_bound(launched,mode,expected):
    p,root,_=launched(mode,ready=False)
    record=wait_exit(p,root)
    assert record['status']==expected
    if mode=='crash':
        assert record['actual_child_exit']==17


def test_forked_existing_loop_timeout_cancels_resident_worker(launched,tmp_path):
    p,root,endpoint=launched('generation_fork',generation_s=5)
    config=LoopConfig(approved_roots=(tmp_path,),subjects=('example_property',),run_dir=tmp_path/'loop-timeout',
                      max_steps=2,model_timeout_s=.12,total_timeout_s=2)
    result=run_question(messages()[1]['content'],NativeModel(endpoint,timeout_s=2),config)
    assert result['status']=='refused' and result['error']=='TIMEOUT'
    assert wait_exit(p,root)['status']=='NATIVE_CLIENT_CANCELLED'


def test_scoped_protocol_gate_and_exact_ledger(launched,tmp_path):
    p,root,endpoint=launched(scope=True)
    repo=Path(__file__).resolve().parents[1]
    config=LoopConfig(approved_roots=(repo/'samples',tmp_path),subjects=('example_property',),run_dir=tmp_path/'scoped-loop',
                      max_steps=2,model_timeout_s=3,total_timeout_s=10)
    result=run_question(messages()[1]['content'],NativeModel(endpoint,timeout_s=2),config,
                        OpsMetricExecutor(config.approved_roots,ops_root=repo/'samples'/'ops'))
    assert result['status']=='answered', result
    assert json.loads((root/'protocol_pass.json').read_text())['status']=='TWO_TURN_PROTOCOL_PASSED'
    control(endpoint)
    assert wait_exit(p,root)['actual_child_exit']==0


@pytest.mark.parametrize('question,expected', [('Second synthetic question','NATIVE_PROTOCOL_GATE'),
                                              ('Unapproved question','NATIVE_UNAUTHORIZED')])
def test_scoped_request_cannot_skip_protocol_or_change_question(launched,question,expected):
    p,root,endpoint=launched(scope=True)
    msg=messages()
    msg[1]['content']=question
    with pytest.raises(HarnessError):
        NativeModel(endpoint,timeout_s=2).complete(msg,tool_schema(('example_property',)))
    assert wait_exit(p,root)['status']==expected


def test_aggregate_rss_stop():
    base={'mem':{'MemAvailable':120*1024**3,'SwapTotal':16*1024**3,'SwapFree':16*1024**3},'group_rss':0,'group_swap':0}
    with pytest.raises(HarnessError) as exc:
        memory_check({**base,'group_rss':91*1024**3},base)
    assert exc.value.code=='NATIVE_WORKLOAD_LIMIT'
