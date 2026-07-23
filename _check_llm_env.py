"""Check LLM env vars and run scan with timeout."""
import os, sys, signal, time
sys.stdout.reconfigure(encoding='utf-8')

# Check LLM config
keys = ['DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'QUALIBUG_LLM_BASE_URL', 
        'QUALIBUG_LLM_ENDPOINT', 'LLM_BASE_URL', 'QUALIBUG_LLM_MODEL']
print("LLM Environment:")
for k in keys:
    v = os.environ.get(k, "")
    print(f"  {k}: {'SET (' + v[:15] + '...)' if v else 'NOT SET'}")

# Check ReasoningConfig
sys.path.insert(0, '.')
from ai_test_asset_center.llm_reasoning import ReasoningConfig
config = ReasoningConfig.from_env()
print(f"\nReasoningConfig.enabled: {config.enabled}")
print(f"ReasoningConfig.timeout_seconds: {getattr(config, 'timeout_seconds', 'N/A')}")

# Check .env.local
from pathlib import Path
env_local = Path(".env.local")
if env_local.exists():
    text = env_local.read_text(encoding='utf-8', errors='replace')
    llm_lines = [l for l in text.splitlines() if any(k.lower() in l.lower() for k in ['api_key', 'llm', 'deepseek', 'openai', 'endpoint'])]
    print(f"\n.env.local LLM-related lines ({len(llm_lines)}):")
    for l in llm_lines[:10]:
        # Redact values
        if '=' in l:
            k, v = l.split('=', 1)
            print(f"  {k}={'***' if v.strip() else 'EMPTY'}")
        else:
            print(f"  {l}")
