from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessagesTypeAdapter,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolReturnPart,
)

from ..backend.marketplace import MarketplaceClient
from ..backend.reporting import ReportingClient
from ..db import Store
from ..events import EventBus
from ..integration.webhooks import WebhookEmitter, parse_tool_payload
from ..memory.journal import JournalWriter
from ..memory.learnings import LearningStore
from ..settings import Settings
from ..skills.loader import SkillLibrary
from .core import build_agent
from .deps import AgentDeps
from .guards import Policy

logger = logging.getLogger(__name__)

WRITE_TOOLS = {
    "propose_campaign",
    "update_campaign_budget",
    "pause_campaign",
    "resume_campaign",
}


class RunInProgressError(RuntimeError):
    pass


def _preview(value: Any, limit: int = 1500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"… ({len(text)} karakter)"


def _normalize_args(args: Any) -> dict:
    if args is None:
        return {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"args": parsed}
        except json.JSONDecodeError:
            return {"raw": args}
    return args


class AgentRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        store: Store,
        bus: EventBus,
        marketplace: MarketplaceClient,
        reporting: ReportingClient,
        policy: Policy,
        journal: JournalWriter,
        learnings: LearningStore,
        skills: SkillLibrary,
        webhooks: WebhookEmitter | None = None,
        model_override: Any = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._bus = bus
        self._marketplace = marketplace
        self._reporting = reporting
        self._policy = policy
        self._journal = journal
        self._learnings = learnings
        self._skills = skills
        self._webhooks = webhooks or WebhookEmitter("")
        self._model_override = model_override
        self._agent: Agent[AgentDeps] | None = None
        self._tasks: dict[str, set[asyncio.Task]] = defaultdict(set)

    async def setup(self) -> None:
        self._agent = await build_agent(self._settings)

    @property
    def agent(self) -> Agent[AgentDeps]:
        if self._agent is None:
            raise RuntimeError("AgentRunner.setup() cagrilmadi")
        return self._agent

    # ------------------------------------------------------------------ public API

    async def start_user_run(self, conversation_id: str, text: str) -> str:
        conversation = await self._store.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"Konusma bulunamadi: {conversation_id}")
        if not conversation["title"]:
            first_line = text.strip().splitlines()[0] if text.strip() else "Yeni sohbet"
            await self._store.set_conversation_title(conversation_id, first_line[:80])
        run_id = await self._start_run(conversation_id, text, trigger="user")
        await self._store.audit(
            "user_message", conversation_id=conversation_id, run_id=run_id, payload={"text": text}
        )
        await self._bus.publish(
            conversation_id, {"type": "user_message", "text": text, "run_id": run_id}
        )
        return run_id

    async def start_scheduled_run(self, conversation_id: str, prompt: str) -> str:
        run_id = await self._start_run(conversation_id, prompt, trigger="scheduled")
        await self._store.audit(
            "scheduled_trigger",
            conversation_id=conversation_id,
            run_id=run_id,
            payload={"prompt": prompt},
        )
        return run_id

    async def start_signal_run(self, conversation_id: str, prompt: str) -> str:
        run_id = await self._start_run(conversation_id, prompt, trigger="signal")
        await self._store.audit(
            "signal_trigger",
            conversation_id=conversation_id,
            run_id=run_id,
            payload={"prompt": prompt},
        )
        await self._bus.publish(
            conversation_id, {"type": "user_message", "text": prompt, "run_id": run_id}
        )
        return run_id

    async def _start_run(self, conversation_id: str, prompt: str, *, trigger: str) -> str:
        if await self._store.active_run(conversation_id):
            raise RunInProgressError("Bu konusmada zaten aktif bir calisma var")
        run_id = await self._store.create_run(conversation_id, trigger)
        self._spawn(conversation_id, run_id, user_prompt=prompt)
        return run_id

    async def resolve_approval(self, approval_id: str, *, approved: bool, note: str | None = None) -> dict:
        approval = await self._store.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Onay kaydi bulunamadi: {approval_id}")
        if approval["status"] != "pending":
            return {"resolved": False, "resumed": False, "status": approval["status"]}

        status = "approved" if approved else "denied"
        await self._store.resolve_approval(approval_id, status, note=note)
        conversation_id = approval["conversation_id"]
        run_id = approval["run_id"]
        await self._store.audit(
            "approval",
            conversation_id=conversation_id,
            run_id=run_id,
            name=approval["tool_name"],
            payload={"approval_id": approval_id, "status": status, "note": note},
        )
        await self._bus.publish(
            conversation_id,
            {"type": "approval_resolved", "approval_id": approval_id, "status": status, "note": note},
        )
        await self._webhooks.emit(
            "approval_resolved",
            {
                "conversation_id": conversation_id,
                "run_id": run_id,
                "approval_id": approval_id,
                "tool_name": approval["tool_name"],
                "status": status,
                "note": note,
            },
        )

        pending = await self._store.pending_approvals_for_run(run_id)
        if pending:
            return {"resolved": True, "resumed": False, "status": status}

        approvals = await self._store.approvals_for_run(run_id)
        deferred = DeferredToolResults()
        for item in approvals:
            if item["status"] == "approved":
                deferred.approvals[item["id"]] = ToolApproved()
            else:
                deferred.approvals[item["id"]] = ToolDenied(
                    message=item.get("note") or "Kullanici bu aksiyonu reddetti"
                )
        await self._store.mark_run_running(run_id)
        self._spawn(conversation_id, run_id, user_prompt=None, deferred=deferred)
        return {"resolved": True, "resumed": True, "status": status}

    async def wait_idle(self, conversation_id: str, timeout: float = 60.0) -> None:
        while True:
            tasks = [task for task in self._tasks.get(conversation_id, set()) if not task.done()]
            if not tasks:
                return
            await asyncio.wait(tasks, timeout=timeout)

    # ------------------------------------------------------------------ internals

    def _deps(self, conversation_id: str, run_id: str, trigger: str) -> AgentDeps:
        return AgentDeps(
            store=self._store,
            marketplace=self._marketplace,
            reporting=self._reporting,
            policy=self._policy,
            settings=self._settings,
            conversation_id=conversation_id,
            run_id=run_id,
            trigger=trigger,
            journal=self._journal,
            learnings=self._learnings,
            skills=self._skills,
        )

    def _spawn(
        self,
        conversation_id: str,
        run_id: str,
        *,
        user_prompt: str | None = None,
        deferred: DeferredToolResults | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._execute(conversation_id, run_id, user_prompt=user_prompt, deferred=deferred)
        )
        self._tasks[conversation_id].add(task)

        def _cleanup(finished: asyncio.Task) -> None:
            self._tasks[conversation_id].discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.error("run gorevi hata ile bitti: %s", finished.exception())

        task.add_done_callback(_cleanup)

    async def _execute(
        self,
        conversation_id: str,
        run_id: str,
        *,
        user_prompt: str | None,
        deferred: DeferredToolResults | None,
    ) -> None:
        run = await self._store.get_run(run_id)
        trigger = run["trigger"] if run else "user"
        deps = self._deps(conversation_id, run_id, trigger)
        await self._bus.publish(conversation_id, {"type": "run_started", "run_id": run_id, "trigger": trigger})

        history = None
        if user_prompt is None:
            raw_history = await self._store.get_history(conversation_id)
            if raw_history:
                history = ModelMessagesTypeAdapter.validate_json(raw_history)

        run_kwargs: dict[str, Any] = {
            "message_history": history,
            "deferred_tool_results": deferred,
            "deps": deps,
            "conversation_id": conversation_id,
        }
        if deferred is None:
            # Onay sonrasi devam kosusu ayri bir Pydantic AI run'idir; ayni run_id'yi
            # tekrar kullanmak message_history ile cakistigi icin yalnizca ilk kosuda gecirilir.
            run_kwargs["run_id"] = run_id
        if self._model_override is not None:
            run_kwargs["model"] = self._model_override

        result = None
        try:
            async with self.agent.run_stream_events(user_prompt, **run_kwargs) as events:
                async for event in events:
                    result = await self._handle_event(conversation_id, run_id, event, result)
            if result is None:
                raise RuntimeError("Ajan sonucu alinamadi")
            await self._finalize(conversation_id, run_id, trigger, result)
        except Exception as exc:  # noqa: BLE001 - arka plan gorevi; hatayi kullaniciya tasiyoruz
            logger.exception("Run basarisiz: %s", run_id)
            await self._store.finish_run(run_id, "error", error=str(exc))
            await self._store.audit(
                "error", conversation_id=conversation_id, run_id=run_id, payload={"error": str(exc)}
            )
            await self._bus.publish(conversation_id, {"type": "error", "message": str(exc)})
            await self._bus.publish(conversation_id, {"type": "run_finished", "run_id": run_id, "status": "error"})
            await self._webhooks.emit(
                "run_finished",
                {
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "trigger": trigger,
                    "status": "error",
                    "error": str(exc),
                },
            )

    async def _handle_event(self, conversation_id: str, run_id: str, event: Any, result: Any) -> Any:
        if isinstance(event, PartStartEvent):
            part = event.part
            if isinstance(part, TextPart) and part.content:
                await self._bus.publish(conversation_id, {"type": "text_delta", "content": part.content})
            elif isinstance(part, ThinkingPart) and part.content:
                await self._bus.publish(conversation_id, {"type": "thinking_delta", "content": part.content})
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and delta.content_delta:
                await self._bus.publish(conversation_id, {"type": "text_delta", "content": delta.content_delta})
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                await self._bus.publish(conversation_id, {"type": "thinking_delta", "content": delta.content_delta})
        elif isinstance(event, FunctionToolCallEvent):
            call = event.part
            await self._store.audit(
                "tool_call",
                conversation_id=conversation_id,
                run_id=run_id,
                name=call.tool_name,
                payload={"tool_call_id": call.tool_call_id, "args": call.args},
            )
            await self._bus.publish(
                conversation_id,
                {
                    "type": "tool_call",
                    "tool_call_id": call.tool_call_id,
                    "name": call.tool_name,
                    "args": _normalize_args(call.args),
                },
            )
        elif isinstance(event, FunctionToolResultEvent):
            part = event.part
            status = "ok" if isinstance(part, ToolReturnPart) else "retry"
            name = getattr(part, "tool_name", "")
            preview = _preview(part.content)
            await self._store.audit(
                "tool_result",
                conversation_id=conversation_id,
                run_id=run_id,
                name=name,
                payload={"tool_call_id": part.tool_call_id, "status": status, "result": preview},
            )
            await self._bus.publish(
                conversation_id,
                {
                    "type": "tool_result",
                    "tool_call_id": part.tool_call_id,
                    "name": name,
                    "status": status,
                    "result": preview,
                },
            )
            if status == "ok" and name in WRITE_TOOLS:
                await self._webhooks.emit(
                    "campaign_action",
                    {
                        "conversation_id": conversation_id,
                        "run_id": run_id,
                        "action": name,
                        "result": parse_tool_payload(part.content),
                    },
                )
        elif isinstance(event, AgentRunResultEvent):
            return event.result
        return result

    async def _finalize(self, conversation_id: str, run_id: str, trigger: str, result: Any) -> None:
        await self._store.save_history(conversation_id, ModelMessagesTypeAdapter.dump_json(result.all_messages()))
        await self._store.touch_conversation(conversation_id)

        output = result.output
        if isinstance(output, DeferredToolRequests):
            items = []
            for call in output.approvals:
                metadata = (output.metadata or {}).get(call.tool_call_id) or {}
                items.append(
                    {
                        "id": call.tool_call_id,
                        "tool_name": call.tool_name,
                        "args": _normalize_args(call.args),
                        "reason": metadata.get("reason"),
                    }
                )
            await self._store.add_approvals(conversation_id, run_id, items)
            await self._store.finish_run(run_id, "waiting_approval")
            for item in items:
                await self._store.audit(
                    "approval_request",
                    conversation_id=conversation_id,
                    run_id=run_id,
                    name=item["tool_name"],
                    payload={"approval_id": item["id"], "args": item["args"], "reason": item["reason"]},
                )
                await self._bus.publish(conversation_id, {"type": "approval_required", **item})
                await self._webhooks.emit(
                    "approval_required",
                    {
                        "conversation_id": conversation_id,
                        "run_id": run_id,
                        "approval_id": item["id"],
                        "tool_name": item["tool_name"],
                        "args": item["args"],
                        "reason": item["reason"],
                    },
                )
            await self._bus.publish(
                conversation_id, {"type": "run_finished", "run_id": run_id, "status": "waiting_approval"}
            )
            await self._webhooks.emit(
                "run_finished",
                {
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "trigger": trigger,
                    "status": "waiting_approval",
                },
            )
        else:
            await self._store.finish_run(run_id, "done")
            await self._bus.publish(conversation_id, {"type": "run_finished", "run_id": run_id, "status": "done"})
            await self._webhooks.emit(
                "run_finished",
                {
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "trigger": trigger,
                    "status": "done",
                },
            )
