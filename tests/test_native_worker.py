"""CPU-only native worker audit and production launcher refusal tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from plat_harness.errors import HarnessError
from plat_harness.native_qwen_worker import audit_model


class Parameter:
    def __init__(self, device='cuda:0', dtype='BF16'):
        self.device,self.dtype=device,dtype
        self.frozen=False
    def numel(self):
        return 123
    def requires_grad_(self,value):
        self.frozen=not value


def fake_model(device='cuda:0',dtype='BF16'):
    p=Parameter(device,dtype)
    cls=type('Qwen3_5MoeForConditionalGeneration',(),{})
    model=cls()
    model.config=SimpleNamespace(model_type='qwen3_5_moe')
    model.named_parameters=lambda:[('EXPLICIT_FAKE_PARAMETER',p)]
    model.named_buffers=lambda:[]
    return model,p


def test_audit_policy_with_fake_parameter():
    model,p=fake_model()
    info=audit_model(model,{},SimpleNamespace(bfloat16='BF16',float32='FP32'))
    assert info['parameter_counts']=={'cuda:0/BF16':123} and p.frozen


@pytest.mark.parametrize('device,dtype', [('cpu','BF16'),('meta','BF16'),('disk','BF16'),('cuda:1','BF16'),('cuda:0','FP32'),('cuda:0','INT4')])
def test_placement_never_silently_offloads_or_quantizes(device,dtype):
    model,_=fake_model(device,dtype)
    with pytest.raises(HarnessError):
        audit_model(model,{},SimpleNamespace(bfloat16='BF16',float32='FP32'))


@pytest.mark.parametrize('field',['missing_keys','unexpected_keys','mismatched_keys','error_msgs'])
def test_full_weight_loading_gate(field):
    model,_=fake_model()
    with pytest.raises(HarnessError):
        audit_model(model,{field:['BAD_FAKE_KEY']},SimpleNamespace(bfloat16='BF16',float32='FP32'))


def test_native_trial_real_entrypoint_blocks_before_supervisor_without_approval(tmp_path):
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,'CUDA_VISIBLE_DEVICES':'','PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
         'PYTHONPATH':str(root/'harness'/'src')}
    output=tmp_path/'blocked_trial'
    command=[sys.executable,'-B','-m','plat_harness.native_trial','--authorization',str(tmp_path/'missing.json'),
             '--authorization-sha256','0'*64,'--manifest',str(tmp_path/'missing_manifest.json'),'--run-dir',str(output)]
    proc=subprocess.run(command,env=env,capture_output=True,timeout=5)
    assert proc.returncode==2,proc.stderr
    record=json.loads((output/'trial_exit.json').read_text())
    assert record['actual_launcher_exit']==2
    assert record['actual_supervisor_exit'] is None and record['model_load_attempted'] is False
    assert not (output/'supervisor').exists()


def test_native_worker_help_does_not_import_ml_runtime():
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,'CUDA_VISIBLE_DEVICES':'','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root/'harness'/'src')}
    command=[sys.executable,'-B','-c','import sys; import plat_harness.native_qwen_worker; assert "torch" not in sys.modules; print("NO_MODEL_IMPORT")']
    proc=subprocess.run(command,env=env,capture_output=True,timeout=5)
    assert proc.returncode==0 and proc.stdout.strip()==b'NO_MODEL_IMPORT'
