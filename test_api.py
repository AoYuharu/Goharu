import os
import asyncio
from dotenv import load_dotenv

load_dotenv('.env')

async def test_api():
    print('[Test] Testing MiniMax API connection...')
    
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print('[ERROR] API key not found')
        return
    
    print(f'[OK] API key loaded: {api_key[:20]}...')
    
    try:
        from anthropic import AsyncAnthropic
        
        client = AsyncAnthropic(
            api_key=api_key,
            base_url='https://api.minimaxi.com/anthropic'
        )
        
        print('[Test] Sending test message to API...')
        
        response = await client.messages.create(
            model='MiniMax-M2.7',
            max_tokens=100,
            messages=[
                {'role': 'user', 'content': '你好'}
            ]
        )
        
        print(f'[OK] API response received!')
        print(f'[OK] Response: {response.content[0].text}')
        
    except Exception as e:
        print(f'[ERROR] API call failed: {e}')
        import traceback
        traceback.print_exc()

asyncio.run(test_api())
