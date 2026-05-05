import sys
import os
import asyncio

sys.path.insert(0, '/root/TableHelper')
os.chdir('/root/TableHelper')

print('[Test] Starting Gateway test...')
print('[Test] Loading environment...')

# Load .env
from dotenv import load_dotenv
load_dotenv('.env')

api_key = os.getenv('ANTHROPIC_API_KEY')
if api_key:
    print(f'[OK] API Key loaded: {api_key[:20]}...')
else:
    print('[ERROR] API Key not found')
    sys.exit(1)

print('[Test] Loading configuration...')
from configurationLoader import Configuration
config = Configuration('config_server.yaml')
print(f'[OK] Config loaded')
print(f'[OK] QQ enabled: {config.get("gateway.platforms.qq.enabled")}')
print(f'[OK] LLM provider: {config.get("model.large-language-model.provider")}')

print('[Test] Creating Gateway instance...')
from Gateway.gateway_runner import GatewayRunner
gateway = GatewayRunner(config)
print('[OK] Gateway instance created')

print('[Test] Starting Gateway...')
async def test_start():
    try:
        await gateway.start()
        print('[OK] Gateway started successfully!')
        print('[INFO] Waiting 10 seconds to test stability...')
        await asyncio.sleep(10)
        print('[OK] Gateway is stable')
        await gateway.stop()
        print('[OK] Gateway stopped')
    except Exception as e:
        print(f'[ERROR] Gateway start failed: {e}')
        import traceback
        traceback.print_exc()

asyncio.run(test_start())
print('[Test] Test complete')
