import os
import asyncio
from dotenv import load_dotenv

load_dotenv('.env')

async def test_api():
    print('[Test] Testing MiniMax API...')
    
    api_key = os.getenv('ANTHROPIC_API_KEY')
    
    try:
        from anthropic import AsyncAnthropic
        
        client = AsyncAnthropic(
            api_key=api_key,
            base_url='https://api.minimaxi.com/anthropic'
        )
        
        print('[Test] Sending message...')
        
        response = await client.messages.create(
            model='MiniMax-M2.7',
            max_tokens=100,
            messages=[
                {'role': 'user', 'content': '你好，请简单介绍一下你自己'}
            ]
        )
        
        print(f'[OK] Response received!')
        print(f'[DEBUG] Response type: {type(response)}')
        print(f'[DEBUG] Content type: {type(response.content)}')
        print(f'[DEBUG] Content length: {len(response.content)}')
        
        for i, block in enumerate(response.content):
            print(f'[DEBUG] Block {i} type: {type(block).__name__}')
            print(f'[DEBUG] Block {i} attributes: {dir(block)}')
            
            # Try different ways to get text
            if hasattr(block, 'text'):
                print(f'[OK] Block {i} text: {block.text}')
            elif hasattr(block, 'thinking'):
                print(f'[OK] Block {i} thinking: {block.thinking}')
            else:
                print(f'[DEBUG] Block {i} content: {block}')
        
    except Exception as e:
        print(f'[ERROR] {e}')
        import traceback
        traceback.print_exc()

asyncio.run(test_api())
