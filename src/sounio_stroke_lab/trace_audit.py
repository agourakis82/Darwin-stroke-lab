from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager

from agents.tracing import get_trace_provider, set_trace_processors
from agents.tracing.processor_interface import TracingProcessor

from sounio_stroke_lab.schemas import TraceEvent


_TRACE_PROCESSOR_LOCK = threading.Lock()


class StorageTraceProcessor(TracingProcessor):
    def __init__(self, storage, run_id: str, trace_id: str):
        self.storage = storage
        self.run_id = run_id
        self.trace_id = trace_id

    def on_trace_start(self, trace) -> None:
        self._write_event("trace_start", trace.export() or {"trace_id": getattr(trace, "trace_id", self.trace_id)})

    def on_trace_end(self, trace) -> None:
        self._write_event("trace_end", trace.export() or {"trace_id": getattr(trace, "trace_id", self.trace_id)})

    def on_span_start(self, span) -> None:
        self._write_event("span_start", span.export() or {"span_id": getattr(span, "span_id", "")})

    def on_span_end(self, span) -> None:
        self._write_event("span_end", span.export() or {"span_id": getattr(span, "span_id", "")})

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None

    def _write_event(self, event_type: str, payload: dict) -> None:
        event = TraceEvent(
            event_id=uuid.uuid4().hex,
            run_id=self.run_id,
            trace_id=self.trace_id,
            event_type=event_type,
            payload=payload,
        )
        self.storage.save_trace_event(event)
        self.storage.append_artifact_text(
            f"{self.run_id}/traces/trace_events.jsonl",
            json.dumps(event.model_dump(mode="json"), ensure_ascii=True) + "\n",
        )


@contextmanager
def isolated_trace_processor(storage, run_id: str, trace_id: str):
    processor = StorageTraceProcessor(storage, run_id, trace_id)
    with _TRACE_PROCESSOR_LOCK:
        provider = get_trace_provider()
        previous_processors = list(getattr(provider._multi_processor, "_processors", ()))
        set_trace_processors(previous_processors + [processor])
        try:
            yield processor
        finally:
            try:
                processor.force_flush()
            finally:
                processor.shutdown()
                set_trace_processors(previous_processors)
