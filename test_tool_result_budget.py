"""
Unit tests for ToolResultBudget.

Verifies that:
- Small results are left unchanged
- Single oversized result is saved to cache and replaced with placeholder
- Batch oversized total causes ALL results to be saved and replaced
- Placeholder contains file path and usage instructions
- Cache files actually exist and contain the original content
"""

import os
import tempfile
import shutil

from Agent.ToolResultBudget import ToolResultBudget


def _make_result(tool_name, text):
    return {"tool_name": tool_name, "result_text": text}


def test_small_results_unchanged():
    """Results under the limit should not be modified."""
    results = [
        _make_result("toolA", "small result"),
        _make_result("toolB", "another small result"),
    ]
    changed = ToolResultBudget.apply(
        results, max_single_tokens=10000, max_batch_tokens=50000
    )
    assert not changed
    assert results[0]["result_text"] == "small result"
    assert results[1]["result_text"] == "another small result"


def test_single_oversized_saved():
    """A single result over the limit should be saved to cache and replaced."""
    tmpdir = tempfile.mkdtemp(prefix="tool_budget_test_")
    try:
        huge_text = "x" * 50000  # ~16k tokens (3 chars/token)
        results = [_make_result("search", huge_text)]

        changed = ToolResultBudget.apply(
            results, cache_dir=tmpdir, max_single_tokens=1000, max_batch_tokens=0
        )
        assert changed
        result_text = results[0]["result_text"]
        assert "[TRUNCATED]" in result_text
        assert "Full output saved to:" in result_text
        assert tmpdir in result_text

        # Check cache file exists and contains the original content
        files = os.listdir(tmpdir)
        assert len(files) == 1
        saved_path = os.path.join(tmpdir, files[0])
        with open(saved_path, "r", encoding="utf-8") as f:
            saved_content = f.read()
        assert saved_content == huge_text
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_batch_total_oversized():
    """When batch total exceeds limit, ALL results should be saved."""
    tmpdir = tempfile.mkdtemp(prefix="tool_budget_test_")
    try:
        results = [
            _make_result("toolA", "A" * 10000),  # ~3.3k tokens
            _make_result("toolB", "B" * 10000),  # ~3.3k tokens
            _make_result("toolC", "C" * 10000),  # ~3.3k tokens
        ]
        changed = ToolResultBudget.apply(
            results, cache_dir=tmpdir,
            max_single_tokens=50000,  # individually ok
            max_batch_tokens=1000,    # but batch total exceeds
        )
        assert changed
        # All should be replaced
        for r in results:
            assert "[TRUNCATED]" in r["result_text"]
            assert "Full output saved to:" in r["result_text"]

        # All 3 cache files should exist
        files = os.listdir(tmpdir)
        assert len(files) == 3
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_placeholder_format():
    """Placeholder should contain file path and Read usage instructions."""
    tmpdir = tempfile.mkdtemp(prefix="tool_budget_test_")
    try:
        results = [_make_result("grep", "BIG" * 5000)]
        ToolResultBudget.apply(
            results, cache_dir=tmpdir, max_single_tokens=100, max_batch_tokens=0,
        )
        text = results[0]["result_text"]
        assert "[TRUNCATED]" in text
        assert "Full output saved to:" in text
        assert "Read(file_path=" in text
        assert "Use Read" in text or "first lines" in text
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_results_with_errors_skipped():
    """Results marked with 'error' key should not be budgeted."""
    results = [
        {"tool_name": "bad_tool", "result_text": "BIG" * 5000, "error": "oops"},
        _make_result("good_tool", "small result"),
    ]
    changed = ToolResultBudget.apply(
        results, max_single_tokens=100, max_batch_tokens=0,
    )
    # Error result should be skipped, good result is small so no change
    assert not changed


def test_empty_results():
    """Empty results list should return False."""
    changed = ToolResultBudget.apply([])
    assert not changed


def test_zero_limits_disabled():
    """max_single_tokens=0 and max_batch_tokens=0 should disable budget."""
    results = [_make_result("toolA", "BIG" * 10000)]
    changed = ToolResultBudget.apply(results, max_single_tokens=0, max_batch_tokens=0)
    assert not changed


def test_cache_dir_created():
    """Cache directory should be created automatically if it doesn't exist."""
    tmpdir = tempfile.mkdtemp(prefix="parent_")
    cache_dir = os.path.join(tmpdir, "new_subdir", "tool_cache")
    try:
        results = [_make_result("toolA", "BIG" * 5000)]
        ToolResultBudget.apply(
            results, cache_dir=cache_dir, max_single_tokens=100, max_batch_tokens=0,
        )
        assert os.path.isdir(cache_dir), f"Cache dir should be created: {cache_dir}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    print("=== ToolResultBudget unit tests ===\n")

    tests = [
        ("small results unchanged", test_small_results_unchanged),
        ("single oversized saved to cache", test_single_oversized_saved),
        ("batch total oversized saves all", test_batch_total_oversized),
        ("placeholder format correct", test_placeholder_format),
        ("error results skipped", test_results_with_errors_skipped),
        ("empty results unchanged", test_empty_results),
        ("zero limits disabled", test_zero_limits_disabled),
        ("cache dir auto-created", test_cache_dir_created),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        exit(1)
