"""
broker.py
=========
In-memory message broker (pub/sub).

Changes from original:
- Added unsubscribe() to allow subscriptions to be cleaned up (fixes
  V-13: thinking stream subscription leak).
- Added running property for health check.
"""

from typing import Dict, List, Callable
from agents.utils.protocol import AgentMessage


class MessageBroker:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self._running = False

    async def start(self):
        """Initialises the broker."""
        self._running = True
        print("MessageBroker started.")

    async def stop(self):
        """Cleans up broker resources."""
        self._running = False
        print("MessageBroker stopped.")

    @property
    def running(self):
        return self._running

    def subscribe(self, topic: str, callback: Callable) -> None:
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        if callback not in self.subscribers[topic]:
            self.subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable) -> bool:
        """
        Remove a callback from a topic. Returns True if removed.
        Fixed V-13: allows thinking stream to clean up on client disconnect.
        """
        callbacks = self.subscribers.get(topic)
        if callbacks and callback in callbacks:
            callbacks.remove(callback)
            return True
        return False

    async def publish(self, topic: str, message: AgentMessage) -> None:
        if topic in self.subscribers:
            for callback in list(self.subscribers[topic]):  # copy to avoid mutation during iteration
                try:
                    await callback(message)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"⚠️ Broker callback error on topic '{topic}': {e}"
                    )


broker = MessageBroker()