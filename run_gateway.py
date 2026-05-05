"""
Gateway Runner for QQ Bot with Agent Integration
Runs the complete Gateway system with intelligent Agent responses
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Gateway.gateway_runner import GatewayRunner
from configurationLoader import Configuration


async def main():
    print("=" * 60)
    print("QQ Bot Gateway with Agent System")
    print("=" * 60)
    print()

    # Load configuration
    config_path = os.path.join(os.path.dirname(__file__), "config_server.yaml")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")

    print(f"[Config] Loading from: {config_path}")
    config = Configuration(config_path)

    # Check for API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[WARNING] ANTHROPIC_API_KEY not set in environment")
        print("[WARNING] Please set it with: export ANTHROPIC_API_KEY=your_key")
        print()

    # Create and start Gateway (pass gateway config dict, not entire config object)
    gateway_config = config.get("gateway", {})
    gateway = GatewayRunner(gateway_config)

    try:
        print("[Gateway] Starting...")
        await gateway.start()
        print("[Gateway] Started successfully!")
        print()
        print("=" * 60)
        print("Bot is now running with Agent system!")
        print("Send messages to the bot to chat with AI")
        print("Press Ctrl+C to stop")
        print("=" * 60)
        print()

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\n[Gateway] Shutdown requested")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[Gateway] Stopping...")
        await gateway.stop()
        print("[Gateway] Stopped")


if __name__ == "__main__":
    asyncio.run(main())
