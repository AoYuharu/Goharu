import sys
import os
import asyncio

sys.path.insert(0, '/root/TableHelper')
os.chdir('/root/TableHelper')

from dotenv import load_dotenv
load_dotenv('.env')

async def test_agent():
    print('[Test] Testing Agent directly...')
    
    from configurationLoader import Configuration
    from Agent.ActorAgent import ActorAgent
    from Agent.ReflectionAgent import ReflectionAgent
    from Memory.MemoryManager import MemoryManager
    from Tools.runtime import create_tool_runtime
    
    config = Configuration('config_server.yaml')
    
    # Initialize components
    memory_manager = MemoryManager()
    runtime = create_tool_runtime(config.get('tools.runtime', 'in_process'))
    await runtime.initialize()
    
    actor = ActorAgent(runtime, memory_manager)
    reflector = ReflectionAgent()
    
    print('[Test] Components initialized')
    
    # Import run_agent
    from main import run_agent
    
    print('[Test] Calling run_agent with "你好"...')
    
    try:
        result = await run_agent(actor, reflector, '你好', memory_manager)
        print(f'[OK] Result: {result}')
    except Exception as e:
        print(f'[ERROR] {e}')
        import traceback
        traceback.print_exc()
    finally:
        await runtime.close()

asyncio.run(test_agent())
