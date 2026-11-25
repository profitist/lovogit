import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from secrets import token_hex

from langchain_core.callbacks import BaseCallbackHandler

from lovogit.app.project_update.artifacts import json_value, write_json
from lovogit.cfg.project_update import ProjectUpdateSettings


class TraceBudgetExceeded(RuntimeError):
    pass


class RunTrace:
    def __init__(self, folder, limits: ProjectUpdateSettings, *, secrets=()):
        self._folder = folder
        self._limits = limits
        self._secrets = tuple(value for value in secrets if value)
        self._trace_id = token_hex(16)
        self._spans = {}
        self._parents = {}
        self._active = ContextVar(f"trace-{self._trace_id}", default=None)
        self._bytes = 0
        self._truncated = False
        self.save()

    def protect(self, *secrets):
        self._secrets += tuple(value for value in secrets if value)

    @contextmanager
    def observe(self, name, *, input=None):
        span_id = token_hex(8)
        self.record("start", span_id, name=name, input=input)
        token = self._active.set(span_id)
        output = {}
        try:
            yield output
        except BaseException as error:
            self.record("end", span_id, error_type=type(error).__name__)
            raise
        else:
            self.record("end", span_id, output=output)
        finally:
            self._active.reset(token)

    def record(self, event, span_id, **data):
        span_id = str(span_id)
        if event == "link":
            parent = str(data.get("parent_id") or "")
            self._parents[span_id] = (
                self._parents.get(parent, parent) or self._active.get()
            )
            return
        if event == "start":
            parent = (
                str(data.pop("parent_id", None) or self._active.get() or "") or None
            )
            parent = self._parents.get(parent, parent)
            if parent not in self._spans:
                parent = self._active.get()
            self._spans[span_id] = {
                "id": token_hex(8),
                "parent_key": parent,
                "name": data.pop("name", "operation"),
                "kind": data.pop("kind", "span"),
                "start_ns": time.time_ns(),
            }
        span = self._spans.get(span_id)
        if span is None:
            return
        if event == "end":
            span["end_ns"] = time.time_ns()
        for key, value in data.items():
            text = json.dumps(json_value(value), ensure_ascii=False)
            for secret in self._secrets:
                text = text.replace(secret, "[redacted]")
            available = max(0, self._limits.UPDATE_TRACE_BYTES - self._bytes)
            encoded = text.encode("utf-8")
            char_limit = (
                self._limits.UPDATE_TOOL_CHARS if span["kind"] == "tool" else len(text)
            )
            if len(encoded) > available or len(text) > char_limit:
                prefix = encoded[:available].decode("utf-8", errors="ignore")[
                    :char_limit
                ]
                span[key] = {
                    "truncated": True,
                    "original_bytes": len(encoded),
                    "original_chars": len(text),
                    "text": prefix,
                    "reason": "trace_data_limit",
                }
                self._truncated = True
                self._bytes += len(prefix.encode("utf-8"))
            else:
                span[key] = json.loads(text)
                self._bytes += len(encoded)
        self.save()

    def check_budget(self):
        if self._truncated or self._bytes >= self._limits.UPDATE_TRACE_BYTES:
            raise TraceBudgetExceeded(
                "Manual analysis exhausted model/tool trace budget"
            )

    def save(self):
        spans = []
        for key, span in self._spans.items():
            parent = self._spans.get(span["parent_key"])
            spans.append(
                {**span, "key": key, "parent_id": parent["id"] if parent else None}
            )
        write_json(
            self._folder / "trace.json",
            {
                "trace_id": self._trace_id,
                "run_id": self._folder.name,
                "truncated": self._truncated,
                "spans": spans,
            },
        )
        otlp_spans = []
        for span in spans:
            if "end_ns" not in span:
                continue
            attrs = {
                "langfuse.trace.name": "lovogit-project-update",
                "langfuse.trace.metadata.run_id": self._folder.name,
                "langfuse.observation.type": span["kind"],
            }
            for field, attribute in (
                ("input", "input"),
                ("output", "output"),
                ("usage", "usage_details"),
                ("model", "model.name"),
            ):
                if field in span:
                    attrs[f"langfuse.observation.{attribute}"] = (
                        span[field]
                        if isinstance(span[field], str)
                        else json.dumps(span[field], ensure_ascii=False)
                    )
            if span.get("error_type"):
                attrs["langfuse.observation.level"] = "ERROR"
                attrs["langfuse.observation.status_message"] = str(span["error_type"])
            item = {
                "traceId": self._trace_id,
                "spanId": span["id"],
                "name": span["name"],
                "kind": 1,
                "startTimeUnixNano": str(span["start_ns"]),
                "endTimeUnixNano": str(span["end_ns"]),
                "attributes": [
                    {"key": k, "value": {"stringValue": v}} for k, v in attrs.items()
                ],
                "status": {"code": 2 if span.get("error_type") else 1},
            }
            if span["parent_id"]:
                item["parentSpanId"] = span["parent_id"]
            otlp_spans.append(item)
        write_json(
            self._folder / "langfuse-otel.json",
            {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "lovogit"},
                                }
                            ]
                        },
                        "scopeSpans": [
                            {
                                "scope": {
                                    "name": "lovogit.project-update",
                                    "version": "1",
                                },
                                "spans": otlp_spans,
                            }
                        ],
                    }
                ]
            },
        )


class TraceCallbacks(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, trace: RunTrace):
        self._trace = trace

    def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
    ):
        self._trace.record("link", run_id, parent_id=parent_run_id)

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ):
        self._trace.check_budget()
        params = kwargs.get("invocation_params", {})
        self._trace.record(
            "start",
            run_id,
            parent_id=parent_run_id,
            name="model",
            kind="generation",
            input=messages,
            model=params.get("model"),
        )

    def on_llm_end(self, response, *, run_id, **kwargs):
        usage = {}
        if response.generations and response.generations[0]:
            message = response.generations[0][0].message
            usage = getattr(message, "usage_metadata", None) or {}
        usage = {
            out: usage[key]
            for out, key in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("total", "total_tokens"),
            )
            if key in usage
        }
        self._trace.record("end", run_id, output=response, usage=usage)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._trace.record("end", run_id, error_type=type(error).__name__)

    def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs
    ):
        self._trace.check_budget()
        self._trace.record(
            "start",
            run_id,
            parent_id=parent_run_id,
            name=serialized.get("name", "tool"),
            kind="tool",
            input=input_str,
        )

    def on_tool_end(self, output, *, run_id, **kwargs):
        self._trace.record("end", run_id, output=output)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._trace.record("end", run_id, error_type=type(error).__name__)
