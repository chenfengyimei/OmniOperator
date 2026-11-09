"""Pricing imports stay lightweight while local discovery, configured prices and unknown costs keep their contract."""

import subprocess
import sys


def test_pricing_import_defers_sdk_until_discovery_and_preserves_fault_semantics():
    code = """
import os,sys
from types import SimpleNamespace
from omni.config import LLM,ModelCapability
from omni.llm import gateway,embedding,usage

assert 'litellm' not in sys.modules
configured=LLM(capabilities={'fixture':ModelCapability(input_usd_per_m=2,output_usd_per_m=5)})
price=usage.resolve_price(configured,'fixture')
assert price.source=='config' and price.known
assert price.input_per_token==2/usage.MICRO and price.output_per_token==5/usage.MICRO
assert 'litellm' not in sys.modules

calls=[]
def lookup(**kwargs):
    assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP']=='True'
    calls.append(kwargs)
    return 2,5
sdk=SimpleNamespace(cost_per_token=lookup)
sys.modules['litellm']=sdk
price=usage.resolve_price(LLM(),'fixture/vendor')
assert price.source=='litellm' and price.known
assert price.input_per_token==2/usage.MICRO and price.output_per_token==5/usage.MICRO
assert calls==[{'model':'fixture/vendor','prompt_tokens':usage.MICRO,'completion_tokens':usage.MICRO}]

def unavailable(**kwargs):
    raise ValueError('private-provider-failure-canary')
sdk.cost_per_token=unavailable
price=usage.resolve_price(LLM(),'fixture/unknown')
assert price.source=='unknown' and not price.known and price.cost_usd(10,10) is None
sdk.cost_per_token=lambda **kwargs:(0,0)
assert usage.resolve_price(LLM(),'fixture/not-free').source=='unknown'
assert usage.resolve_price(LLM(),'ollama/fixture').source=='local_free'
print('deferred pricing verified without a real provider SDK')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "deferred pricing verified" in result.stdout
