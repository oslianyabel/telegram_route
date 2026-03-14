import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Message:
    user_number: str
    content: str
    message_id: str | None = None


class MessageQueue:
    """Async message queue processor for handling messages per user sequentially."""

    def __init__(self) -> None:
        # Queue per user: {user_number: asyncio.Queue[Message]}
        self.user_queues: dict[str, asyncio.Queue[Message]] = {}
        # Track if a user is currently being processed
        self.processing: dict[str, bool] = {}
        # Task per user to keep track of processing
        self.tasks: dict[str, asyncio.Task] = {}

    async def enqueue(self, message: Message) -> None:
        """Add a message to the queue for the user."""
        user_number = message.user_number

        # Create queue if it doesn't exist
        if user_number not in self.user_queues:
            self.user_queues[user_number] = asyncio.Queue()
            self.processing[user_number] = False

        await self.user_queues[user_number].put(message)
        logger.debug(
            f"Message enqueued for {user_number}, queue size: {self.user_queues[user_number].qsize()}"
        )

    async def process_queue(
        self, user_number: str, handler: Callable[[Message], Coroutine[Any, Any, None]]
    ) -> None:
        """Process messages from the queue for a specific user sequentially."""
        while True:
            try:
                queue = self.user_queues.get(user_number)
                if not queue:
                    break

                # Block until message is available or timeout
                try:
                    message = queue.get_nowait()
                except asyncio.QueueEmpty:
                    # Queue is empty; wait a bit and retry or break
                    await asyncio.sleep(0.1)
                    if queue.empty():
                        break
                    continue

                self.processing[user_number] = True
                logger.info(
                    f"Processing message for {user_number}: {message.content[:50]}"
                )

                try:
                    await handler(message)
                except Exception as exc:
                    logger.error(f"Error processing message for {user_number}: {exc}")
                finally:
                    self.processing[user_number] = False

            except Exception as e:
                logger.error(f"Error in process_queue for {user_number}: {e}")
                break

    async def start_processing(
        self, user_number: str, handler: Callable[[Message], Coroutine[Any, Any, None]]
    ) -> None:
        """Start processing messages for a user if not already running."""
        if user_number not in self.tasks or self.tasks[user_number].done():
            task = asyncio.create_task(self.process_queue(user_number, handler))
            self.tasks[user_number] = task
            logger.debug(f"Started processing task for {user_number}")

    def is_processing(self, user_number: str) -> bool:
        """Check if a user's message is currently being processed."""
        return self.processing.get(user_number, False)

    def queue_size(self, user_number: str) -> int:
        """Get the queue size for a user."""
        if user_number not in self.user_queues:
            return 0
        return self.user_queues[user_number].qsize()

    async def cleanup(self, user_number: str) -> None:
        """Clean up queue and task for a user."""
        if user_number in self.user_queues:
            # Clear remaining messages
            while not self.user_queues[user_number].empty():
                try:
                    self.user_queues[user_number].get_nowait()
                except asyncio.QueueEmpty:
                    break
            del self.user_queues[user_number]

        if user_number in self.processing:
            del self.processing[user_number]

        if user_number in self.tasks:
            task = self.tasks[user_number]
            if not task.done():
                task.cancel()
            del self.tasks[user_number]

        logger.debug(f"Cleaned up queue for {user_number}")


# Global instance
message_queue = MessageQueue()
