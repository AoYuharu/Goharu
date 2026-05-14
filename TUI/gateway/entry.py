"""
Gateway entry point

Starts the JSON-RPC server and registers all handlers
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from TUI.gateway.server import get_gateway
from TUI.gateway.agent_handler import register_agent_methods


def main():
    """Main entry point for gateway"""
    # Get gateway instance
    gateway = get_gateway()

    # Register all RPC methods
    register_agent_methods()

    # Start the event loop
    try:
        gateway.run()
    except KeyboardInterrupt:
        pass
    finally:
        gateway.stop()


if __name__ == "__main__":
    main()
