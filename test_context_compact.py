"""
Verify ContextCompactor base functionality (no LLM call)
"""
import sys
sys.path.insert(0, '.')

from Agent.ContextCompactor import ContextCompactor
from Agent.TokenEstimator import TokenEstimator
from Prompting.PromptAssembler import PromptAssembler

# 1. TokenEstimator
print("=" * 60)
print("1. TokenEstimator test")
estimator = TokenEstimator()
msgs = [
    {"role": "user", "content": "Write a Python function"},
    {"role": "assistant", "content": "Here is a function:\ndef hello():\n    return 'Hello World'\n"},
    {"role": "user", "content": "Add type hints"},
]
for msg in msgs:
    est = estimator.estimate(str(msg))
    print(f"  {msg['role']}: {est} tokens")
total = estimator.estimate(str(msgs))
print(f"  Total: {total} tokens")
assert total > 0, "Token estimation should return > 0"

# 2. PromptAssembler
print("\n" + "=" * 60)
print("2. build_context_compact_document test")
assembler = PromptAssembler()
doc = assembler.build_context_compact_document(
    [{"role": "user", "content": "Test question"}, {"role": "assistant", "content": "Test answer"}],
    system_prompt="You are a test assistant"
)
assert doc is not None
sections = doc.all_sections()
print(f"  Sections: {len(sections)}")
for s in sections:
    print(f"  - [{s.kind}] {s.title}")
assert len(sections) > 0
print("  OK")

# 3. ContextCompactor instantiation
print("\n" + "=" * 60)
print("3. ContextCompactor instantiation")
compactor = ContextCompactor()
assert compactor is not None
assert compactor.token_estimator is not None
print("  OK")

# 4. compact() prompt construction (no LLM)
print("\n" + "=" * 60)
print("4. compact() prompt construction")
messages = [
    {"role": "system", "content": "You are an assistant"},
    {"role": "user", "content": "Q1"},
    {"role": "assistant", "content": "A1"},
    {"role": "user", "content": "Q2"},
    {"role": "assistant", "content": "A2"},
]
doc = compactor.prompt_assembler.build_context_compact_document(
    [m for m in messages if m["role"] != "system"],
    system_prompt="You are an assistant"
)
prompt_msgs = compactor.prompt_renderer.render_document(doc)
assert len(prompt_msgs) > 0
print(f"  Prompt messages: {len(prompt_msgs)}")
print(f"  First role: {prompt_msgs[0]['role']}")
print(f"  Last role: {prompt_msgs[-1]['role']}")
print("  OK")

print("\n" + "=" * 60)
print("[PASS] All base tests passed")
