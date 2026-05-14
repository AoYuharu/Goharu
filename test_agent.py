import sys
sys.path.insert(0, r'E:\TableHelper')

print("Starting test...")

try:
    from Agent.SubAgent import SubAgent
    from Tools.registry import registry
    
    print("Imports OK")
    
    # 创建实例
    agent = SubAgent('Explore', 'test', 'test_id', registry, None)
    print("Agent created")
    
    # 检查方法
    print("Has _should_use_native_tools:", hasattr(agent, '_should_use_native_tools'))
    
    # 列出所有 _should 开头的方法
    should_methods = [m for m in dir(agent) if '_should' in m]
    print("Methods with _should:", should_methods)
    
    # 列出以 _hard 开头的所有方法
    hard_methods = [m for m in dir(agent) if '_hard' in m]
    print("Methods with _hard:", hard_methods)
    
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print("Done")
