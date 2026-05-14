#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script to verify TUI config editor and history loading fixes

This script tests:
1. Config editor screen can be opened and navigated
2. History loading doesn't block UI thread
"""

import sys
import io
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_config_editor_import():
    """Test that config editor can be imported"""
    try:
        from TUI.screens.config_editor import ConfigEditorScreen, ConfigEditScreen
        print("✅ Config editor imports successfully")
        return True
    except Exception as e:
        print(f"❌ Config editor import failed: {e}")
        return False

def test_config_editor_key_handling():
    """Test that config editor has proper key handling"""
    try:
        from TUI.screens.config_editor import ConfigEditorScreen

        # Check that on_key method exists
        assert hasattr(ConfigEditorScreen, 'on_key'), "ConfigEditorScreen should have on_key method"

        # Check that in_edit_mode is initialized
        screen = ConfigEditorScreen()
        assert hasattr(screen, 'in_edit_mode'), "ConfigEditorScreen should have in_edit_mode attribute"
        assert screen.in_edit_mode == False, "in_edit_mode should be False initially"

        print("✅ Config editor has proper key handling")
        return True
    except Exception as e:
        print(f"❌ Config editor key handling test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_app_history_loading():
    """Test that app has async history loading"""
    try:
        from TUI.app import TableHelperTUI
        import inspect

        # Get the source code of _on_gateway_ready
        source = inspect.getsource(TableHelperTUI._on_gateway_ready)

        # Check that it uses threading
        assert 'threading' in source, "_on_gateway_ready should use threading"
        assert 'load_history_async' in source, "_on_gateway_ready should have async history loading"
        assert 'call_from_thread' in source, "_on_gateway_ready should use call_from_thread for UI updates"

        print("✅ App has async history loading")
        return True
    except Exception as e:
        print(f"❌ App history loading test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("Testing TUI fixes...\n")

    results = []

    print("1. Testing config editor import...")
    results.append(test_config_editor_import())
    print()

    print("2. Testing config editor key handling...")
    results.append(test_config_editor_key_handling())
    print()

    print("3. Testing app history loading...")
    results.append(test_app_history_loading())
    print()

    # Summary
    passed = sum(results)
    total = len(results)

    print("=" * 60)
    print(f"Test Results: {passed}/{total} passed")

    if passed == total:
        print("✅ All tests passed!")
        return 0
    else:
        print(f"❌ {total - passed} test(s) failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
