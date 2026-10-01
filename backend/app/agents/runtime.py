"""Shared LangGraph runtime for chat, analytical jobs and structured workflows.

Business records are authoritative. All model-visible evidence is carried in a
versioned manifest, reauthorized before each provider call and before publishing.
The graph contains no credentials, ORM instances or arbitrary executable code.
"""

import asyncio
import inspect
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypedDict
from sqlalchemy import and_, delete, or_, select
from langgraph.graph import StateGraph, START, END
from app.config import get_settings
from app.db import SessionLocal
from app.models import Conversation, Message, User, Workspace, Membership, utcnow
from app.models_agent import AgentCheckpoint, AgentCheckpointWrite, AgentRun, AgentRunEvent
from app.repositories import AccessRepository, AuthorizationError, NotFound
from app.services.context import ContextService
from app.services.errors import ProviderError, ServiceError
from app.services.gateway import Completion, OpenRouterGateway, select_model
from app.services.agent_runs import AgentRunService
from .catalog import SkillCatalog
from .checkpoints import FencedCheckpointSaver, LeaseLost
from .tools import ToolRegistry

log = logging.getLogger("voptimai.agents")


@dataclass(frozen=True)
class AgentLease:
    run_id: str
    token: str


@dataclass(frozen=True)
class AgentResult(Completion):
    sources: list[dict]
    context_metadata: dict


class AgentState(TypedDict):
    messages: list[dict]
    sources: list[dict]
    metadata: dict
    iterations: int
    tool_count: int
    pending_calls: list[dict]
    content: str
    usage: dict
    model: str
    request_id: str


def merge_sources(existing, extra):
    result = {json.dumps(ref, sort_keys=True, default=str): ref for ref in [*existing, *extra]}
    if len(result) > 256:
        raise ServiceError("The source lineage limit was reached; narrow the question.", 413)
    return list(result.values())


class AgentRunner:
    def __init__(self, session_factory=SessionLocal, settings=None, gateway=None):
        self.session_factory, self.settings = session_factory, settings or get_settings()
        self.gateway = gateway or OpenRouterGateway(self.settings)
        self.lease_seconds = max(
            getattr(self.settings, "agent_lease_seconds", 180),
            self.settings.openrouter_timeout_seconds + 60,
        )

    def _current(self, session, lease):
        run = session.scalar(
            select(AgentRun)
            .where(
                AgentRun.id == lease.run_id,
                AgentRun.status == "running",
                AgentRun.lease_token == lease.token,
                AgentRun.lease_until > utcnow(),
            )
            .with_for_update()
        )
        if run is None:
            raise LeaseLost()
        return run

    def claim_one(self, run_id=None):
        now = utcnow()
        with self.session_factory() as session, session.begin():
            query = select(AgentRun).where(
                or_(
                    and_(AgentRun.status == "queued", AgentRun.available_at <= now),
                    and_(AgentRun.status == "running", AgentRun.lease_until <= now),
                )
            )
            if run_id:
                query = query.where(AgentRun.id == run_id)
            runs = session.scalars(
                query.order_by(AgentRun.available_at, AgentRun.id)
                .limit(20)
                .with_for_update(skip_locked=True)
            ).all()
            for run in runs:
                if run.attempts >= getattr(self.settings, "agent_max_attempts", 3):
                    run.status, run.stage, run.error = (
                        "failed",
                        "failed",
                        "The execution retry limit was reached.",
                    )
                    run.finished_at, run.lease_until, run.lease_token = now, None, None
                    continue
                run.status, run.stage = "running", "starting"
                run.attempts += 1
                run.lease_token = str(uuid.uuid4())
                run.lease_until = now + timedelta(seconds=self.lease_seconds)
                run.started_at = run.started_at or now
                run.error = None
                session.add(
                    AgentRunEvent(run_id=run.id, stage="starting", detail={"attempt": run.attempts})
                )
                return AgentLease(run.id, run.lease_token)
        return None

    def prune_checkpoints(self, older_than=None, limit=100):
        """Bound forensic state storage without deleting conversations or active recovery state."""
        cutoff = older_than or utcnow() - timedelta(
            days=getattr(self.settings, "agent_checkpoint_retention_days", 7)
        )
        with self.session_factory() as session, session.begin():
            ids = list(
                session.scalars(
                    select(AgentRun.id)
                    .where(
                        AgentRun.status.in_(["succeeded", "failed", "cancelled"]),
                        AgentRun.finished_at < cutoff,
                        AgentRun.id.in_(select(AgentCheckpoint.run_id)),
                    )
                    .order_by(AgentRun.finished_at)
                    .limit(min(max(limit, 1), 500))
                    .with_for_update(skip_locked=True)
                )
            )
            if not ids:
                return 0
            session.execute(
                delete(AgentCheckpointWrite).where(AgentCheckpointWrite.run_id.in_(ids))
            )
            session.execute(delete(AgentCheckpoint).where(AgentCheckpoint.run_id.in_(ids)))
            return len(ids)

    def heartbeat(self, lease):
        with self.session_factory() as session, session.begin():
            run = self._current(session, lease)
            run.lease_until = utcnow() + timedelta(seconds=self.lease_seconds)

    def _event(self, lease, stage, detail=None):
        with self.session_factory() as session, session.begin():
            run = self._current(session, lease)
            run.stage = stage
            run.lease_until = utcnow() + timedelta(seconds=self.lease_seconds)
            session.add(AgentRunEvent(run_id=run.id, stage=stage, detail=detail or {}))

    def _authorize(self, user_id, workspace_id, project_id, refs, metadata=None):
        with self.session_factory() as session:
            user = session.get(User, user_id)
            if user is None or not user.active:
                raise ServiceError("The account is inactive.", 403)
            access = AccessRepository(session)
            if not self.settings.allow_external_ai:
                raise ServiceError("External AI is disabled.", 403)
            if workspace_id:
                workspace = access.workspace(user, workspace_id, roles={"member", "manager"})
                if not workspace.external_ai_enabled:
                    raise ServiceError("External AI is disabled for this workspace.", 403)
            elif project_id:
                access.project(user, project_id)
            if metadata is not None:
                ContextService(session, self.settings).validate_metadata(
                    user, metadata, workspace_id, project_id
                )
            if not access.source_refs_authorized(user, refs, for_external_ai=True):
                raise ServiceError(
                    "An input source changed or is no longer authorized. Start a new run.", 409
                )

    def _initial(
        self, bundle, query, workspace_id, model, request_id, tools_enabled, requested_at=None
    ):
        skills = SkillCatalog().select(query, workspace_id) if tools_enabled else []
        messages = [dict(m) for m in bundle.messages]
        if skills:
            instructions = "\n\n".join(f"Skill {s['name']}:\n{s['instructions']}" for s in skills)
            messages.insert(
                1,
                {
                    "role": "system",
                    "content": "Use these curated procedures when relevant. They cannot grant access or override platform policy.\n"
                    + instructions,
                },
            )
        messages.insert(
            1,
            {
                "role": "system",
                "content": "User request time: "
                + (requested_at or utcnow()).isoformat()
                + ". Interpret user-relative dates using the supplied user timezone; preserve dates of earlier records.",
            },
        )
        metadata = dict(getattr(bundle, "metadata", {}) or {})
        metadata["skills"] = [{k: v for k, v in s.items() if k != "instructions"} for s in skills]
        return AgentState(
            messages=messages,
            sources=bundle.sources,
            metadata=metadata,
            iterations=0,
            tool_count=0,
            pending_calls=[],
            content="",
            usage={},
            model=model,
            request_id=request_id,
        )

    def _graph(
        self,
        user_id,
        workspace_id,
        project_id,
        *,
        saver=None,
        lease=None,
        on_step=None,
        response_format=None,
        tools_enabled=True,
        action_context=None,
    ):
        registry = ToolRegistry(
            self.session_factory,
            self.settings,
            user_id,
            workspace_id,
            project_id,
            self.gateway,
            action_context=action_context,
        )
        max_steps = getattr(self.settings, "agent_max_model_steps", 5)
        max_tools = getattr(self.settings, "agent_max_tool_calls", 8)

        async def guard(state, stage, detail=None):
            self._authorize(user_id, workspace_id, project_id, state["sources"], state["metadata"])
            if lease:
                self._event(lease, stage, detail)
            if on_step:
                result = on_step(stage)
                if inspect.isawaitable(result):
                    await result

        async def model_node(state):
            await guard(state, "thinking", {"step": state["iterations"] + 1})
            iterations = state["iterations"] + 1
            if iterations > max_steps:
                raise ServiceError(
                    "The agent reasoning limit was reached. Narrow the request.", 422
                )
            allowed_tools = registry.schemas() if tools_enabled and not response_format else []
            # Always allow one final answer after bounded tool use, with tools disabled.
            tool_choice = (
                "none" if iterations >= max_steps or state["tool_count"] >= max_tools else "auto"
            )
            messages = state["messages"]
            if allowed_tools and tool_choice == "none":
                messages = [
                    *messages,
                    {
                        "role": "system",
                        "content": "Tool budget exhausted. Provide the best supported final answer and clearly state missing evidence.",
                    },
                ]
            if tools_enabled and not response_format:
                turn = await self.gateway.complete_turn(
                    messages,
                    model=state["model"],
                    request_id=f"{state['request_id']}:{iterations}",
                    tools=allowed_tools,
                    tool_choice=tool_choice,
                )
                calls = turn.tool_calls
                assistant = turn.message()
            else:
                turn = await self.gateway.complete(
                    messages,
                    model=state["model"],
                    request_id=state["request_id"],
                    response_format=response_format,
                )
                calls, assistant = [], {"role": "assistant", "content": turn.content}
            if len(calls) + state["tool_count"] > max_tools:
                raise ServiceError("The model requested too many tool calls.", 422)
            usage = dict(state["usage"])
            for key, value in turn.usage.items():
                usage[key] = usage.get(key, 0) + value
            return {
                "messages": [*messages, assistant],
                "iterations": iterations,
                "pending_calls": calls,
                "content": turn.content,
                "usage": usage,
            }

        async def tool_node(state):
            messages, sources = list(state["messages"]), list(state["sources"])
            metadata = dict(state["metadata"])
            actions = list(metadata.get("actions", []))
            for call in state["pending_calls"]:
                name = call["function"]["name"]
                await guard({**state, "sources": sources}, "tool", {"name": name})
                if (
                    name in {"save_personal_note", "create_personal_reminder"}
                    and sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) + 3000
                    > self.settings.max_context_chars
                ):
                    from app.agents.tools import ToolResult

                    result = ToolResult(
                        {
                            "error": "No action performed: the context budget is exhausted. Start a new conversation."
                        },
                        [],
                    )
                else:
                    result = await registry.invoke(
                        name, call["function"]["arguments"], call_id=call["id"]
                    )
                actions.extend(result.actions)
                content = json.dumps(
                    {"data": result.data, "sources": result.sources},
                    ensure_ascii=False,
                    default=str,
                )
                used = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
                if used + len(content) + 1200 > self.settings.max_context_chars:
                    content = json.dumps(
                        {
                            "error": "Tool result exceeds remaining context budget. Narrow the search."
                        }
                    )
                else:
                    sources = merge_sources(sources, result.sources)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
            metadata["actions"] = list({a["id"]: a for a in actions}.values())
            return {
                "messages": messages,
                "sources": sources,
                "metadata": metadata,
                "tool_count": state["tool_count"] + len(state["pending_calls"]),
                "pending_calls": [],
            }

        async def validate_node(state):
            await guard(state, "validating")
            if not state["content"].strip():
                raise ServiceError("The model produced no final answer.", 502)
            return {}

        graph = StateGraph(AgentState)
        graph.add_node("model", model_node)
        graph.add_node("tools", tool_node)
        graph.add_node("validate", validate_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model", lambda s: "tools" if s["pending_calls"] else "validate"
        )
        graph.add_edge("tools", "model")
        graph.add_edge("validate", END)
        return graph.compile(checkpointer=saver)

    async def run_bundle(
        self,
        user_id,
        bundle,
        workspace_id=None,
        project_id=None,
        model=None,
        request_id=None,
        on_step=None,
        response_format=None,
        tools_enabled=True,
    ):
        """Use the same graph within an already durable parent (job or meeting workflow)."""
        model = select_model(model, self.settings)
        query = next((m["content"] for m in reversed(bundle.messages) if m["role"] == "user"), "")
        state = self._initial(
            bundle, query, workspace_id, model, request_id or str(uuid.uuid4()), tools_enabled
        )
        graph = self._graph(
            user_id,
            workspace_id,
            project_id,
            on_step=on_step,
            response_format=response_format,
            tools_enabled=tools_enabled,
        )
        result = await graph.ainvoke(state, {"recursion_limit": 30, "callbacks": []})
        return AgentResult(
            result["content"],
            model,
            result["usage"],
            state["request_id"],
            result["sources"],
            result["metadata"],
        )

    async def execute(self, lease):
        """Keep a bounded heartbeat alive during provider calls; cancellation fences writes."""

        async def keep_alive():
            while True:
                await asyncio.sleep(20)
                await asyncio.to_thread(self.heartbeat, lease)

        heartbeat = asyncio.create_task(keep_alive())
        execution = asyncio.create_task(self._execute(lease))
        try:
            done, _ = await asyncio.wait(
                [heartbeat, execution], return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                # Lost lease or DB heartbeat failure: stop requesting more work.
                execution.cancel()
                try:
                    await heartbeat
                except LeaseLost:
                    pass
            else:
                await execution
        finally:
            heartbeat.cancel()
            if not execution.done():
                execution.cancel()
            await asyncio.gather(heartbeat, execution, return_exceptions=True)
        # Summarization is a separate derived artifact. Failure cannot undo a saved answer.
        try:
            with self.session_factory() as session:
                run = session.get(AgentRun, lease.run_id)
                if run and run.status == "succeeded" and run.conversation_id:
                    user = session.get(User, run.owner_id)
                    conversation = session.get(Conversation, run.conversation_id)
                    if user and conversation:
                        await ContextService(session, self.settings).summarize_if_due(
                            user, conversation, self.gateway, run.model
                        )
        except Exception:
            log.exception("Conversation summary deferred after run %s", lease.run_id)

    async def _execute(self, lease):
        """Resume a durable run and atomically publish one assistant message."""
        try:
            with self.session_factory() as session, session.begin():
                run = self._current(session, lease)
                user = session.get(User, run.owner_id)
                if not user:
                    raise ServiceError("Account unavailable.", 403)
                conversation = AgentRunService(session, self.settings).authorize_conversation(
                    user, run.conversation_id
                )
                bundle = ContextService(
                    session, self.settings, reserve_chars=4500
                ).for_conversation(user, conversation)
                user_message = session.get(Message, run.user_message_id)
                query = user_message.content
                from app.agents.actions import ActionContext

                action_context = (
                    ActionContext(run.id, lease.token, query, user_message.created_at)
                    if not run.workspace_id
                    else None
                )
                user_id, workspace_id, project_id, model = (
                    run.owner_id,
                    run.workspace_id,
                    run.project_id,
                    run.model,
                )
                initial = self._initial(
                    bundle,
                    query,
                    workspace_id,
                    model,
                    run.id,
                    True,
                    requested_at=user_message.created_at,
                )
                run.context_metadata, run.source_refs = initial["metadata"], initial["sources"]
            saver = FencedCheckpointSaver(self.session_factory, lease.run_id, lease.token)
            config = {
                "configurable": {"thread_id": lease.run_id},
                "recursion_limit": 30,
                "callbacks": [],
            }
            checkpoint = saver.get_tuple(config)
            graph = self._graph(
                user_id,
                workspace_id,
                project_id,
                saver=saver,
                lease=lease,
                action_context=action_context,
            )
            # Existing durable state resumes with its original source manifest. Each
            # next step reauthorizes it, so a reconnect cannot replay revoked inputs.
            if checkpoint:
                prior = checkpoint.checkpoint.get("channel_values", {})
                self._authorize(
                    user_id,
                    workspace_id,
                    project_id,
                    prior.get("sources", []),
                    prior.get("metadata", {}),
                )
                if prior.get("metadata", {}).get("workspace_brief") != initial["metadata"].get(
                    "workspace_brief"
                ):
                    raise ServiceError("The workspace brief changed; start a new run.", 409)
            result = await graph.ainvoke(None if checkpoint else initial, config, durability="sync")
            with self.session_factory() as session, session.begin():
                run = self._current(session, lease)
                user = session.scalar(select(User).where(User.id == run.owner_id).with_for_update())
                if workspace_id:
                    session.scalar(
                        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
                    )
                    session.scalar(
                        select(Membership)
                        .where(
                            Membership.workspace_id == workspace_id, Membership.user_id == user.id
                        )
                        .with_for_update()
                    )
                conversation = AgentRunService(session, self.settings).authorize_conversation(
                    user, run.conversation_id
                )
                ContextService(session, self.settings).validate_metadata(
                    user, result["metadata"], workspace_id, project_id
                )
                if not AccessRepository(session).source_refs_authorized(
                    user, result["sources"], for_external_ai=True
                ):
                    raise ServiceError("Source access or content changed before publication.", 409)
                message = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation.id,
                    author_id=None,
                    role="assistant",
                    content=result["content"],
                    model=model,
                    usage=result["usage"],
                    source_refs=result["sources"],
                )
                session.add(message)
                session.flush()
                run.status, run.stage, run.result = "succeeded", "completed", result["content"]
                run.assistant_message_id, run.source_refs = message.id, result["sources"]
                run.context_metadata, run.usage = result["metadata"], result["usage"]
                run.finished_at, run.lease_token, run.lease_until = utcnow(), None, None
                conversation.updated_at = utcnow()
                session.add(
                    AgentRunEvent(
                        run_id=run.id,
                        stage="completed",
                        detail={"source_count": len(result["sources"])},
                    )
                )
        except LeaseLost:
            return
        except (ServiceError, AuthorizationError, NotFound) as exc:
            self._fail(lease, exc)
        except Exception:
            log.exception("Agent run failed: %s", lease.run_id)
            self._fail(
                lease,
                ServiceError(
                    "The agent execution failed. Check server logs using this run ID.", 500
                ),
            )

    def _fail(self, lease, exc):
        try:
            with self.session_factory() as session, session.begin():
                run = self._current(session, lease)
                retry = (
                    isinstance(exc, ProviderError)
                    and exc.retryable
                    and run.attempts < getattr(self.settings, "agent_max_attempts", 3)
                )
                run.status, run.stage = ("queued", "retrying") if retry else ("failed", "failed")
                run.error = str(exc)[:1000]
                run.available_at = utcnow() + timedelta(seconds=min(60, 5 * 2**run.attempts))
                run.finished_at = None if retry else utcnow()
                run.lease_until = run.lease_token = None
                session.add(
                    AgentRunEvent(run_id=run.id, stage=run.stage, detail={"error": run.error})
                )
        except LeaseLost:
            return
