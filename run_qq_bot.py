"""
QQ Bot Production Runner
Runs the QQ Bot continuously with automatic reconnection
"""

import asyncio
import signal
import sys
from test_qq_echo_standalone import SimpleQQAdapter, MessageEvent

# Global flag for graceful shutdown
shutdown_flag = False


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    global shutdown_flag
    print(f"\n[Signal] Received signal {signum}, shutting down gracefully...")
    shutdown_flag = True


async def main():
    print("=" * 60)
    print("QQ Bot Production Runner")
    print("=" * 60)
    print()

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    adapter = SimpleQQAdapter(
        app_id="102839705",
        client_secret="wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5"
    )

    async def echo_handler(event: MessageEvent):
        """Handle incoming messages with echo response"""
        print(f"\n[Received] From: {event.source.user_name} ({event.source.user_id})")
        print(f"[Received] Chat: {event.source.chat_id} ({event.source.chat_type})")
        print(f"[Received] Text: {event.text}")

        reply = f"Echo: {event.text}"
        is_group = event.source.chat_type == "group"

        print(f"[Sending] Reply to {event.source.chat_id}")
        result = await adapter.send(event.source.chat_id, reply, is_group)

        if result.success:
            print(f"[SUCCESS] Sent! ID: {result.message_id}")
        else:
            print(f"[FAIL] Error: {result.error}")

    adapter.set_message_handler(echo_handler)

    retry_count = 0
    max_retries = 5

    while not shutdown_flag and retry_count < max_retries:
        try:
            print(f"[Bot] Connecting... (attempt {retry_count + 1}/{max_retries})")
            success = await adapter.connect()

            if success:
                print()
                print("=" * 60)
                print("Bot is running!")
                print("Send messages to the bot to test functionality")
                print("Press Ctrl+C to stop")
                print("=" * 60)
                print()

                # Keep running until shutdown signal
                while not shutdown_flag:
                    await asyncio.sleep(1)

                print("\n[Bot] Shutdown requested")
                break
            else:
                print(f"[FAIL] Connection failed")
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = min(2 ** retry_count, 60)  # Exponential backoff, max 60s
                    print(f"[Bot] Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)

        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            retry_count += 1
            if retry_count < max_retries:
                wait_time = min(2 ** retry_count, 60)
                print(f"[Bot] Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)

        finally:
            await adapter.disconnect()

    if retry_count >= max_retries:
        print(f"\n[FAIL] Max retries ({max_retries}) reached, exiting")
        sys.exit(1)

    print("\n[Bot] Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Bot] Interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
