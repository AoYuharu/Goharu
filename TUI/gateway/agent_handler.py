"""
Agent integration for Gateway

Connects the Agent system to the Gateway RPC server
"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime

from Agent.ActorAgent import ActorAgent
from Memory.MemoryManager import MemoryManager
from Tools.runtime import create_tool_runtime
from configurationLoader import config

from .server import get_gateway, emit_event


class AgentHandler:
    """Handles agent execution and emits events to TUI"""

    def __init__(self):
        self.runtime = None
        self.memory = None
        self.actor = None
        self.current_session_id = "default"
        self._initialized = False

    def initialize(self):
        """Initialize agent components"""
        if self._initialized:
            return

        emit_event("agent.initializing", {"timestamp": datetime.now().isoformat()})

        try:
            # Create tool runtime
            self.runtime = create_tool_runtime()
            emit_event("agent.runtime_ready", {"tools_count": len(self.runtime.list_tools())})

            # Create memory manager
            self.memory = MemoryManager()
            emit_event("agent.memory_ready", {})

            # Create agents
            self.actor = ActorAgent(self.runtime, self.memory)

            emit_event("agent.ready", {
                "model": config.get("model.large-language-model.model", "unknown"),
                "tools": [t["name"] for t in self.runtime.list_tools()]
            })

            self._initialized = True

        except Exception as e:
            emit_event("agent.error", {
                "message": str(e),
                "type": "initialization_error"
            })
            raise

    def send_message(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle user message

        Args:
            params: {"message": str, "session_id": str}

        Returns:
            {"status": "processing"}
        """
        if not self._initialized:
            self.initialize()

        message = params.get("message", "").strip()
        session_id = params.get("session_id", "default")

        if not message:
            return {"error": "Empty message"}

        self.current_session_id = session_id

        # Emit message received event
        emit_event("message.received", {
            "message": message,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat()
        })

        # Add to memory
        self.memory.append("user", message)

        # Start processing in background
        asyncio.create_task(self._process_message(message))

        return {"status": "processing"}

    async def _process_message(self, message: str):
        """Process message with agent (runs in background)"""
        try:
            emit_event("agent.thinking", {
                "message": "Processing your request...",
                "timestamp": datetime.now().isoformat()
            })

            max_depth = config.get("mcp.maxDepth", 10)

            for step in range(max_depth):
                # Check if we should stop
                emit_event("agent.step", {
                    "step": step + 1,
                    "max_steps": max_depth
                })

                # Actor act
                response = await self.actor.act()

                if not response:
                    break

                # Check if it's a tool call
                if response.get("type") == "tool_call":
                    tool_name = response.get("tool_name")
                    args = response.get("arguments", {})

                    # Emit tool call event
                    emit_event("tool.call", {
                        "tool": tool_name,
                        "arguments": args,
                        "step": step + 1
                    })

                    # Execute tool
                    result = await self.runtime.dispatch(tool_name, args)

                    # Emit tool result
                    emit_event("tool.result", {
                        "tool": tool_name,
                        "result": str(result)[:500],  # Truncate for display
                        "step": step + 1
                    })

                    # Add to memory
                    self.memory.append("tool_result", {
                        "tool": tool_name,
                        "result": result
                    })

                elif response.get("type") == "answer":
                    # Final answer
                    answer = response.get("content", "")

                    emit_event("message.complete", {
                        "answer": answer,
                        "session_id": self.current_session_id,
                        "timestamp": datetime.now().isoformat()
                    })

                    # Add to memory
                    self.memory.append("assistant", answer)
                    break

        except Exception as e:
            emit_event("agent.error", {
                "message": str(e),
                "type": "processing_error"
            })

    def get_history(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get conversation history

        Args:
            params: {"session_id": str, "limit": int}

        Returns:
            {"messages": [...]}
        """
        if not self._initialized:
            return {"messages": []}

        limit = params.get("limit", 50)
        messages = self.memory.get_context()[-limit:]

        return {
            "messages": [
                {
                    "role": msg.get("role"),
                    "content": msg.get("content", "")[:1000]  # Truncate
                }
                for msg in messages
            ]
        }

    def clear_session(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clear current session

        Args:
            params: {"session_id": str}

        Returns:
            {"status": "cleared"}
        """
        if self._initialized and self.memory:
            self.memory.clear_context()

        emit_event("session.cleared", {
            "session_id": params.get("session_id", "default"),
            "timestamp": datetime.now().isoformat()
        })

        return {"status": "cleared"}


# Global agent handler
_agent_handler: Optional[AgentHandler] = None


def get_agent_handler() -> AgentHandler:
    """Get or create the global agent handler"""
    global _agent_handler
    if _agent_handler is None:
        _agent_handler = AgentHandler()
    return _agent_handler


def register_agent_methods():
    """Register agent RPC methods with gateway"""
    gateway = get_gateway()
    handler = get_agent_handler()

    gateway.register_method("agent.send_message", handler.send_message)
    gateway.register_method("agent.get_history", handler.get_history)
    gateway.register_method("agent.clear_session", handler.clear_session)
