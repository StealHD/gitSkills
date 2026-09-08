from __future__ import annotations

import json
import hashlib
from datetime import datetime
from typing import Any

from .common import redact_text
from .evidence import parse_timestamp, event_time, new_turn


def is_guardian(metadata: dict) -> bool:
    if not isinstance(metadata, dict):
        return False
    source = metadata.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    return metadata.get("thread_source") == "guardian_review" or (
        isinstance(subagent, dict) and subagent.get("other") == "guardian"
    )


class SessionParser:
    """Resumable JSONL parser. Only redacted turn data is persisted."""

    def __init__(self, timezone, state=None):
        self.timezone = timezone
        state = state or {}
        self.thread_id = state.get("thread_id", "unknown")
        self.cwd = state.get("cwd", "")
        self.turns = state.get("turns", {})
        self.order = state.get("order", [])
        self.current_turn_id = state.get("current_turn_id", "")
        self.call_to_turn = state.get("call_to_turn", {})
        self.metadata = state.get("metadata", {})
        self.line_number = state.get("line_number", 0)
        self.excluded = state.get("excluded", False)
        for turn in self.turns.values():
            turn["occurred_at"] = parse_timestamp(turn.get("occurred_at"), timezone)
            turn["pending_calls"] = {tool["call_id"]: tool for tool in turn["tool_evidence"]}

    def ensure_turn(self, turn_id, occurred_at):
        if turn_id not in self.turns:
            self.turns[turn_id] = new_turn(turn_id, occurred_at, self.cwd)
            self.order.append(turn_id)
        return self.turns[turn_id]

    @staticmethod
    def response_turn_id(payload):
        if payload.get("turn_id"):
            return str(payload["turn_id"])
        for key in ("internal_chat_message_metadata_passthrough", "metadata"):
            metadata = payload.get(key)
            if isinstance(metadata, dict) and metadata.get("turn_id"):
                return str(metadata["turn_id"])
        return ""

    @staticmethod
    def message_text(content):
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "\n".join(part['text'] for part in content if isinstance(part, dict)
                         and part.get('type') in {'text', 'input_text', 'output_text'}
                         and isinstance(part.get('text'), str))

    def user_message(self, message, timestamp, source, turn_id=""):
        message = redact_text(message)
        # Desktop setup messages share role=user with the actual request.
        if not message.strip() or message.lstrip().startswith((
            '<recommended_plugins>', '<environment_context>', '# AGENTS.md instructions',
            '<permissions instructions>', '<INSTRUCTIONS>',
        )):
            return
        turn_id = turn_id or self.current_turn_id
        if not turn_id:
            if source != 'event':
                return
            turn_id = f'turn-{self.line_number}'
            self.current_turn_id = turn_id
        turn = self.ensure_turn(turn_id, timestamp)
        turn['occurred_at'] = turn['occurred_at'] or timestamp
        # The same request can be emitted as response, completed item and legacy
        # event. Count mirrors per format, preserving repeated requests within
        # one format and deduplicating across incremental parser resumes.
        key = hashlib.sha256(message.encode()).hexdigest()
        counts = turn.setdefault('user_message_counts', {}).setdefault(key, {})
        seen = max(counts.values(), default=0)
        counts[source] = counts.get(source, 0) + 1
        if counts[source] > seen:
            turn['user_text'] = '\n'.join(part for part in (turn['user_text'], message) if part)

    def feed(self, line):
        self.line_number += 1
        if self.excluded:
            return
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid complete session JSON line {self.line_number}") from exc
        timestamp = parse_timestamp(item.get("timestamp"), self.timezone)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        item_type = item.get("type")

        if item_type == "session_meta":
            self.thread_id = str(payload.get("id") or self.thread_id)
            self.cwd = str(payload.get("cwd") or self.cwd)
            self.metadata = {key: payload.get(key) for key in ("source", "thread_source", "parent_thread_id")}
            self.excluded = is_guardian(self.metadata)
            return

        event_type = payload.get("type") if item_type == "event_msg" else ("turn_context" if item_type == "turn_context" else "")
        if event_type == "task_started":
            turn_id = str(payload.get("turn_id") or f"turn-{self.line_number}")
            occurred_at = event_time(payload, timestamp, self.timezone)
            self.ensure_turn(turn_id, occurred_at)
            self.current_turn_id = turn_id
            return

        if event_type == "turn_context":
            turn_id = str(payload.get("turn_id") or self.current_turn_id or f"turn-{self.line_number}")
            turn = self.ensure_turn(turn_id, timestamp)
            turn["cwd"] = str(payload.get("cwd") or turn.get("cwd") or self.cwd)
            self.current_turn_id = turn_id
            return

        if event_type == "user_message":
            self.user_message(payload.get('message') or '', timestamp, 'event', self.response_turn_id(payload))
            return

        if event_type == 'item_completed':
            completed = payload.get('item') or {}
            if isinstance(completed, dict) and completed.get('type') == 'UserMessage':
                self.user_message(self.message_text(completed.get('content')), timestamp, 'completed',
                                  self.response_turn_id(payload))
            return

        if event_type == "task_complete":
            turn_id = str(payload.get("turn_id") or self.current_turn_id or f"turn-{self.line_number}")
            turn = self.ensure_turn(turn_id, timestamp)
            turn["occurred_at"] = turn["occurred_at"] or timestamp
            turn["result_text"] = redact_text(payload.get("last_agent_message") or "")
            self.current_turn_id = turn_id
            return

        if item_type != "response_item":
            return
        response_type = str(payload.get("type") or "")
        if response_type == 'message' and payload.get('role') == 'user':
            self.user_message(self.message_text(payload.get('content')), timestamp, 'response',
                              self.response_turn_id(payload))
            return
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        explicit_turn_id = self.response_turn_id(payload)
        target_turn_id = explicit_turn_id or self.call_to_turn.get(call_id) or self.current_turn_id
        if not target_turn_id:
            return
        turn = self.ensure_turn(target_turn_id, timestamp)
        is_call = response_type.endswith("_call") and not response_type.endswith("_call_output")
        is_output = response_type.endswith("_output") and (
            "call" in response_type or response_type in {"web_search_output", "tool_search_output", "image_generation_output"}
        )
        if is_call:
            call_id = str(payload.get("call_id") or payload.get("id") or f"call-{self.line_number}")
            tool_name = str(payload.get("name") or response_type)
            input_value: Any = ""
            for key in ("arguments", "input", "action", "query", "search_query", "prompt"):
                if payload.get(key) is not None:
                    input_value = payload[key]
                    break
            call = {
                "tool_name": tool_name,
                "call_id": call_id,
                "input_text": redact_text(input_value),
                "output_text": "",
            }
            turn["pending_calls"][call_id] = call
            turn["tool_evidence"].append(call)
            self.call_to_turn[call_id] = target_turn_id
        elif is_output:
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            call = turn["pending_calls"].get(call_id)
            if call is None:
                call = {
                    "tool_name": "tool",
                    "call_id": call_id or f"call-{self.line_number}",
                    "input_text": "",
                    "output_text": "",
                }
                turn["tool_evidence"].append(call)
            output = payload.get("output")
            if output is None:
                output = payload.get("content") or ""
            call["output_text"] = redact_text(output)

    def dump(self):
        state = {key: getattr(self, key) for key in (
            "thread_id", "cwd", "order", "current_turn_id", "call_to_turn",
            "metadata", "line_number", "excluded")}
        state["turns"] = {
            key: {k: (v.isoformat() if isinstance(v, datetime) else v)
                  for k, v in turn.items() if k != "pending_calls"}
            for key, turn in self.turns.items()
        }
        return state

    def records(self, start, end):
        if self.excluded:
            return []
        records = []
        for turn_id in self.order:
            turn = self.turns[turn_id]
            occurred_at = turn["occurred_at"]
            if occurred_at is None or not start <= occurred_at < end:
                continue
            records.append({
                "thread_id": self.thread_id, "turn_id": turn_id,
                "occurred_at": occurred_at, "cwd": turn.get("cwd") or self.cwd,
                "user_text": turn["user_text"], "result_text": turn["result_text"],
                "tool_evidence": turn["tool_evidence"], "session_metadata": self.metadata,
            })
        return records
