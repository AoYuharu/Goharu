import sys
import os
import asyncio

sys.path.insert(0, '/root/TableHelper')
os.chdir('/root/TableHelper')

print('[Test] Starting Gateway test with fixed config...')

from dotenv import load_dotenv
load_dotenv('.env')

from configurationLoader import Configuration
config = Configuration('config_server.yaml')

# Get gateway config dict
gateway_config = config.get('gateway', {})
print(f'[OK] Gateway config type: {type(gateway_config)}')
print(f'[OK] Platforms in config: {list(gateway_config.get("platforms", {}).keys())}')

from Gateway.gateway_runner import GatewayRunner
gateway = GatewayRunner(gateway_config)

async def test_start():
    try:
        await gateway.start()
        print('[OK] Gateway started!')
        print(f'[OK] Connected platforms: {gateway.adapter_manager.get_connected_platforms()}')
        await asyncio.sleep(5)
        await gateway.stop()
    except Exception as e:
        print(f'[ERROR] {e}')
        import traceback
        traceback.print_exc()

asyncio.run(test_start())
