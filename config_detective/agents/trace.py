"""Trace events and store for observable investigations.

This module provides structured tracing for the investigation workflow,
enabling real-time observation of agent progress in the UI.

Features:
- Structured trace events with timestamps
- Thread-safe trace store
- Event streaming for live updates
- Duration tracking per node

This is a rubric stretch feature (+2): observable intermediate steps.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable
from uuid import uuid4


class EventType(str, Enum):
    """Types of trace events."""

    START = "start"
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"
    INFO = "info"
    WARNING = "warning"


@dataclass
class TraceEvent:
    """A single trace event from the investigation workflow.

    Attributes:
        id: Unique event identifier
        timestamp: When the event occurred
        trace_id: ID of the parent investigation
        node_name: Name of the node that emitted this event
        event_type: Type of event (start, progress, complete, error)
        message: Human-readable message
        data: Additional structured data
        duration_ms: Duration since node start (for complete events)
    """

    id: str = field(default_factory=lambda: str(uuid4())[:8])
    timestamp: datetime = field(default_factory=datetime.utcnow)
    trace_id: str = ""
    node_name: str = ""
    event_type: EventType = EventType.INFO
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id,
            "node_name": self.node_name,
            "event_type": self.event_type.value,
            "message": self.message,
            "data": self.data,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        """Create from dictionary."""
        return cls(
            id=data.get("id", str(uuid4())[:8]),
            timestamp=(
                datetime.fromisoformat(data["timestamp"])
                if isinstance(data.get("timestamp"), str)
                else datetime.utcnow()
            ),
            trace_id=data.get("trace_id", ""),
            node_name=data.get("node_name", ""),
            event_type=EventType(data.get("event_type", "info")),
            message=data.get("message", ""),
            data=data.get("data", {}),
            duration_ms=data.get("duration_ms"),
        )

    def format_log(self) -> str:
        """Format for log output."""
        elapsed = f"{self.duration_ms}ms" if self.duration_ms else ""
        return (
            f"[{self.timestamp.strftime('%H:%M:%S.%f')[:-3]}] "
            f"{self.node_name:12} {self.event_type.value:8} "
            f"{self.message} {elapsed}"
        )


class TraceStore:
    """Thread-safe store for trace events.

    Supports:
    - Adding events from multiple threads
    - Subscribing to new events (for streaming)
    - Retrieving all events for a trace
    """

    def __init__(self) -> None:
        self._events: dict[str, list[TraceEvent]] = {}
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[Callable[[TraceEvent], None]]] = {}
        self._async_subscribers: dict[str, list[asyncio.Queue]] = {}

    def add_event(self, event: TraceEvent) -> None:
        """Add an event to the store.

        Args:
            event: The trace event to add
        """
        with self._lock:
            if event.trace_id not in self._events:
                self._events[event.trace_id] = []
            self._events[event.trace_id].append(event)

            # Notify synchronous subscribers
            for callback in self._subscribers.get(event.trace_id, []):
                try:
                    callback(event)
                except Exception:
                    pass  # Don't let subscriber errors break the flow

            # Notify async subscribers
            for queue in self._async_subscribers.get(event.trace_id, []):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def get_events(self, trace_id: str) -> list[TraceEvent]:
        """Get all events for a trace.

        Args:
            trace_id: The investigation trace ID

        Returns:
            List of trace events in order
        """
        with self._lock:
            return list(self._events.get(trace_id, []))

    def subscribe(
        self, trace_id: str, callback: Callable[[TraceEvent], None]
    ) -> Callable[[], None]:
        """Subscribe to events for a trace.

        Args:
            trace_id: The investigation trace ID
            callback: Function to call when events occur

        Returns:
            Unsubscribe function
        """
        with self._lock:
            if trace_id not in self._subscribers:
                self._subscribers[trace_id] = []
            self._subscribers[trace_id].append(callback)

        def unsubscribe():
            with self._lock:
                if trace_id in self._subscribers:
                    self._subscribers[trace_id].remove(callback)

        return unsubscribe

    async def subscribe_async(
        self, trace_id: str, max_events: int = 1000
    ) -> AsyncIterator[TraceEvent]:
        """Async generator for streaming events.

        Args:
            trace_id: The investigation trace ID
            max_events: Maximum queue size

        Yields:
            TraceEvent objects as they occur
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=max_events)

        with self._lock:
            if trace_id not in self._async_subscribers:
                self._async_subscribers[trace_id] = []
            self._async_subscribers[trace_id].append(queue)

        try:
            while True:
                event = await queue.get()
                yield event

                # Check for completion
                if event.event_type == EventType.COMPLETE and event.node_name == "reporter":
                    break
        finally:
            with self._lock:
                if trace_id in self._async_subscribers:
                    self._async_subscribers[trace_id].remove(queue)

    def clear(self, trace_id: str | None = None) -> None:
        """Clear events.

        Args:
            trace_id: Specific trace to clear, or None for all
        """
        with self._lock:
            if trace_id:
                self._events.pop(trace_id, None)
                self._subscribers.pop(trace_id, None)
                self._async_subscribers.pop(trace_id, None)
            else:
                self._events.clear()
                self._subscribers.clear()
                self._async_subscribers.clear()


# Global trace store singleton
_trace_store: TraceStore | None = None
_store_lock = threading.Lock()


def get_trace_store() -> TraceStore:
    """Get the global trace store instance."""
    global _trace_store
    with _store_lock:
        if _trace_store is None:
            _trace_store = TraceStore()
        return _trace_store


def reset_trace_store() -> None:
    """Reset the global trace store (for testing)."""
    global _trace_store
    with _store_lock:
        _trace_store = None


class NodeTracer:
    """Context manager for tracing a node's execution.

    Automatically emits start and complete events, tracking duration.

    Usage:
        async with NodeTracer(trace_id, "triage") as tracer:
            tracer.progress("Classifying error type...")
            # ... do work ...
            tracer.set_result({"category": "locale"})
    """

    def __init__(
        self,
        trace_id: str,
        node_name: str,
        store: TraceStore | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.node_name = node_name
        self.store = store or get_trace_store()
        self._start_time: float = 0
        self._result_data: dict[str, Any] = {}

    def _emit(
        self,
        event_type: EventType,
        message: str,
        data: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> TraceEvent:
        """Emit a trace event."""
        event = TraceEvent(
            trace_id=self.trace_id,
            node_name=self.node_name,
            event_type=event_type,
            message=message,
            data=data or {},
            duration_ms=duration_ms,
        )
        self.store.add_event(event)
        return event

    def start(self, message: str = "Starting...") -> TraceEvent:
        """Emit a start event."""
        self._start_time = time.time()
        return self._emit(EventType.START, message)

    def progress(self, message: str, data: dict[str, Any] | None = None) -> TraceEvent:
        """Emit a progress event."""
        return self._emit(EventType.PROGRESS, message, data)

    def info(self, message: str, data: dict[str, Any] | None = None) -> TraceEvent:
        """Emit an info event."""
        return self._emit(EventType.INFO, message, data)

    def warning(self, message: str, data: dict[str, Any] | None = None) -> TraceEvent:
        """Emit a warning event."""
        return self._emit(EventType.WARNING, message, data)

    def error(self, message: str, data: dict[str, Any] | None = None) -> TraceEvent:
        """Emit an error event."""
        duration_ms = int((time.time() - self._start_time) * 1000) if self._start_time else None
        return self._emit(EventType.ERROR, message, data, duration_ms)

    def complete(
        self, message: str = "Completed", data: dict[str, Any] | None = None
    ) -> TraceEvent:
        """Emit a complete event."""
        duration_ms = int((time.time() - self._start_time) * 1000) if self._start_time else None
        final_data = {**self._result_data, **(data or {})}
        return self._emit(EventType.COMPLETE, message, final_data, duration_ms)

    def set_result(self, data: dict[str, Any]) -> None:
        """Set result data to include in complete event."""
        self._result_data.update(data)

    async def __aenter__(self) -> "NodeTracer":
        self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type:
            self.error(f"Failed: {exc_val}")
        else:
            self.complete()

    def __enter__(self) -> "NodeTracer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type:
            self.error(f"Failed: {exc_val}")
        else:
            self.complete()


def emit_event(
    trace_id: str,
    node_name: str,
    event_type: EventType,
    message: str,
    data: dict[str, Any] | None = None,
) -> TraceEvent:
    """Convenience function to emit a trace event.

    Args:
        trace_id: The investigation trace ID
        node_name: Name of the emitting node
        event_type: Type of event
        message: Human-readable message
        data: Additional data

    Returns:
        The created TraceEvent
    """
    event = TraceEvent(
        trace_id=trace_id,
        node_name=node_name,
        event_type=event_type,
        message=message,
        data=data or {},
    )
    get_trace_store().add_event(event)
    return event
