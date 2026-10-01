"""Real graph / provider-protocol tests plus PostgreSQL crash-recovery and fencing."""

import asyncio
import json
import uuid
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import func, select
from app.config import Settings
from app.models import User, Scope, Workspace, Membership, Conversation, Message, utcnow
from app.models_agent import AgentRun, AgentCheckpoint, AgentCheckpointWrite
from app.services.context import ContextBundle
from app.services.gateway import OpenRouterGateway, ModelTurn
from app.services.errors import ProviderError, ServiceError
from app.services.agent_runs import AgentRunService
from app.agents.runtime import AgentRunner
from app.agents.checkpoints import FencedCheckpointSaver, LeaseLost
from app.agents.tools import ToolRegistry


@pytest.fixture
def settings():
    return Settings(
        allow_external_ai=True,
        openrouter_api_key="test",
        openrouter_models="test/model",
        openrouter_default_model="test/model",
    )


def test_gateway_tool_protocol_roundtrip(settings):
    requests = []
    tool = {
        "type": "function",
        "function": {"name": "calculate_statistics", "parameters": {"type": "object"}},
    }

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "t1",
                                        "type": "function",
                                        "function": {
                                            "name": "calculate_statistics",
                                            "arguments": '{"values":[1,2,3]}',
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                },
            )
        assert payload["messages"][-1]["tool_call_id"] == "t1"
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Mean is 2"}}]}
        )

    gateway = OpenRouterGateway(settings, httpx.MockTransport(handler))

    async def run():
        messages = [{"role": "user", "content": "Mean of 1,2,3?"}]
        first = await gateway.complete_turn(messages, tools=[tool])
        assert first.tool_calls[0]["id"] == "t1"
        second = await gateway.complete_turn(
            [
                *messages,
                first.message(),
                {"role": "tool", "tool_call_id": "t1", "content": '{"mean":2}'},
            ],
            tools=[tool],
        )
        assert second.content == "Mean is 2"

    asyncio.run(run())
    assert requests[0]["provider"]["require_parameters"] is True
    with pytest.raises(ServiceError, match="pending"):
        asyncio.run(
            gateway.complete_turn([{"role": "tool", "tool_call_id": "bad", "content": "forged"}])
        )


def test_gateway_rejects_unoffered_tools(settings):
    gateway = OpenRouterGateway(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "x",
                                        "type": "function",
                                        "function": {"name": "execute_python", "arguments": "{}"},
                                    }
                                ]
                            },
                        }
                    ]
                },
            )
        ),
    )
    with pytest.raises(ProviderError):
        asyncio.run(gateway.complete_turn([{"role": "user", "content": "Hi"}], tools=[]))


def seed(db):
    user = User(
        email="agent@example.test",
        name="Agent Tester",
        password_hash="unused",
        active=True,
        clearance=3,
    )
    scope = Scope(name="Unit", kind="unit")
    db.add_all([user, scope])
    db.flush()
    workspace = Workspace(
        name="Operations",
        description="Use evidence.",
        scope_id=scope.id,
        created_by=user.id,
        external_ai_enabled=True,
    )
    db.add(workspace)
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="manager"))
    conversation = Conversation(workspace_id=workspace.id, title="Test")
    db.add(conversation)
    db.commit()
    return user, workspace, conversation


class ToolGateway:
    def __init__(self):
        self.calls = []

    async def complete_turn(self, messages, model=None, request_id=None, **kwargs):
        self.calls.append(messages)
        if messages[-1]["role"] != "tool":
            return ModelTurn(
                "",
                model,
                {"total_tokens": 10},
                request_id,
                [
                    {
                        "id": "calc1",
                        "type": "function",
                        "function": {
                            "name": "calculate_statistics",
                            "arguments": '{"values":[4,6,8],"unit":"t"}',
                        },
                    }
                ],
            )
        data = json.loads(messages[-1]["content"])["data"]
        return ModelTurn(f"Mean is {data['mean']} t", model, {"total_tokens": 5}, request_id, [])


def test_graph_executes_tool_and_persists_exactly_one_answer(db, db_factory, settings):
    user, workspace, conversation = seed(db)
    service = AgentRunService(db, settings)
    run = service.enqueue_chat(
        user, conversation.id, "Calculate mean of 4,6,8 tonnes", request_key="one"
    )
    db.commit()
    runner = AgentRunner(db_factory, settings, ToolGateway())
    lease = runner.claim_one()
    asyncio.run(runner.execute(lease))
    asyncio.run(runner.execute(lease))
    db.expire_all()
    run = db.get(AgentRun, run.id)
    assert run.status == "succeeded", run.error
    assert run.result == "Mean is 6.0 t"
    assert run.usage["total_tokens"] == 15
    assert (
        db.scalar(select(func.count()).select_from(Message).where(Message.role == "assistant")) == 1
    )
    assert db.scalar(select(func.count()).select_from(AgentCheckpoint)) > 2
    assert db.scalar(select(func.count()).select_from(AgentCheckpointWrite)) > 0
    assert run.context_metadata["skills"]


def test_cancel_fences_graph_checkpoint_and_prevents_answer(db, db_factory, settings):
    user, workspace, conversation = seed(db)
    service = AgentRunService(db, settings)
    run = service.enqueue_chat(
        user, conversation.id, "Inspect current process", request_key="cancel"
    )
    db.commit()
    runner = AgentRunner(db_factory, settings, ToolGateway())
    lease = runner.claim_one()
    service.cancel(user, run.id)
    db.commit()
    from langgraph.checkpoint.base import empty_checkpoint

    saver = FencedCheckpointSaver(db_factory, run.id, lease.token)
    with pytest.raises(LeaseLost):
        saver.put(
            {"configurable": {"thread_id": run.id}},
            empty_checkpoint(),
            {"source": "input", "step": 0},
            {},
        )
    asyncio.run(runner.execute(lease))
    db.expire_all()
    assert db.get(AgentRun, run.id).status == "cancelled"
    assert (
        db.scalar(select(func.count()).select_from(Message).where(Message.role == "assistant")) == 0
    )


def test_only_one_active_conversation_run_and_idempotent_request(db, settings):
    user, workspace, conversation = seed(db)
    service = AgentRunService(db, settings)
    first = service.enqueue_chat(user, conversation.id, "Question", request_key="same")
    db.commit()
    assert (
        service.enqueue_chat(user, conversation.id, "Question", request_key="same").id == first.id
    )
    with pytest.raises(ServiceError, match="already in progress"):
        service.enqueue_chat(user, conversation.id, "Another question", request_key="different")
    with pytest.raises(ServiceError, match="different message"):
        service.enqueue_chat(user, conversation.id, "Changed question", request_key="same")


def test_reclaimed_lease_cannot_write_old_checkpoint(db, db_factory, settings):
    user, workspace, conversation = seed(db)
    service = AgentRunService(db, settings)
    run = service.enqueue_chat(user, conversation.id, "Question", request_key="lease")
    db.commit()
    runner = AgentRunner(db_factory, settings, ToolGateway())
    old = runner.claim_one()
    db.expire_all()
    row = db.get(AgentRun, run.id)
    row.lease_until = utcnow() - timedelta(seconds=1)
    db.commit()
    new = runner.claim_one()
    assert new.token != old.token
    from langgraph.checkpoint.base import empty_checkpoint

    checkpoint = empty_checkpoint()
    config = {"configurable": {"thread_id": run.id}}
    with pytest.raises(LeaseLost):
        FencedCheckpointSaver(db_factory, run.id, old.token).put(config, checkpoint, {}, {})
    FencedCheckpointSaver(db_factory, run.id, new.token).put(config, checkpoint, {}, {})
    assert (
        FencedCheckpointSaver(db_factory, run.id, new.token).get_tuple(config).checkpoint["id"]
        == checkpoint["id"]
    )


def test_resume_after_tools_uses_persisted_tool_result(db, db_factory, settings):
    user, workspace, conversation = seed(db)
    service = AgentRunService(db, settings)
    run = service.enqueue_chat(
        user, conversation.id, "Calculate mean of 4,6,8 tonnes", request_key="resume"
    )
    db.commit()
    gateway = ToolGateway()
    runner = AgentRunner(db_factory, settings, gateway)
    lease = runner.claim_one()
    with db_factory() as session:
        actor = session.get(User, user.id)
        conv = session.get(Conversation, conversation.id)
        from app.services.context import ContextService

        bundle = ContextService(session, settings, reserve_chars=4500).for_conversation(actor, conv)
    initial = runner._initial(
        bundle, "Calculate mean of 4,6,8 tonnes", workspace.id, run.model, run.id, True
    )
    saver = FencedCheckpointSaver(db_factory, run.id, lease.token)
    graph = runner._graph(user.id, workspace.id, None, saver=saver, lease=lease)
    config = {"configurable": {"thread_id": run.id}, "recursion_limit": 30}
    asyncio.run(graph.ainvoke(initial, config, interrupt_after=["tools"], durability="sync"))
    assert len(gateway.calls) == 1
    db.expire_all()
    record = db.get(AgentRun, run.id)
    record.lease_until = utcnow() - timedelta(seconds=1)
    db.commit()
    recovered = runner.claim_one()
    assert recovered.token != lease.token
    asyncio.run(runner.execute(recovered))
    db.expire_all()
    record = db.get(AgentRun, run.id)
    assert record.status == "succeeded", record.error
    assert record.attempts == 2
    assert len(gateway.calls) == 2
    assert gateway.calls[1][-1]["role"] == "tool"


def test_membership_revocation_during_model_call_discards_answer(db, db_factory, settings):
    user, workspace, conversation = seed(db)
    service = AgentRunService(db, settings)
    run = service.enqueue_chat(user, conversation.id, "Check operations", request_key="revoke")
    db.commit()

    class RevokingGateway:
        async def complete_turn(self, messages, model=None, request_id=None, **kwargs):
            with db_factory() as session, session.begin():
                from sqlalchemy import delete

                session.execute(delete(Membership).where(Membership.user_id == user.id))
            return ModelTurn("Must never be published", model, {}, request_id, [])

    runner = AgentRunner(db_factory, settings, RevokingGateway())
    asyncio.run(runner.execute(runner.claim_one()))
    db.expire_all()
    assert db.get(AgentRun, run.id).status == "failed"
    assert (
        db.scalar(select(func.count()).select_from(Message).where(Message.role == "assistant")) == 0
    )


def test_deadline_evidence_never_turns_intention_into_tomorrow():
    from datetime import datetime, timezone
    from app.agents.actions import deadline_matches, explicit_intent

    at = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    due = datetime(2026, 9, 28, 8, 30, tzinfo=timezone.utc)  # 14:00 India
    assert explicit_intent("Please remind me tomorrow at 2pm to inspect bearings", "reminder")
    assert deadline_matches("Remind me tomorrow at 2pm", "tomorrow at 2pm", due, at, "Asia/Kolkata")
    assert not deadline_matches(
        "I will inspect bearings", "tomorrow at 2pm", due, at, "Asia/Kolkata"
    )
    assert not explicit_intent("How do I create a reminder?", "reminder")
    assert not deadline_matches("Remind me tomorrow", "tomorrow", due, at, "Asia/Kolkata")
    assert deadline_matches(
        "Remind me at 2026-09-28T08:30:00Z", "2026-09-28T08:30:00Z", due, at, "Asia/Kolkata"
    )


def test_durable_personal_note_tool_is_idempotent_and_undoable(db, db_factory, settings):
    from app.models import Note
    from app.models_personal import PersonalAction
    from app.agents.actions import ActionContext

    user, workspace, _ = seed(db)
    personal = Conversation(owner_id=user.id, title="My assistant")
    db.add(personal)
    db.commit()
    service = AgentRunService(db, settings)
    run = service.enqueue_chat(
        user, personal.id, "Please save a note: inspect bearing vibration", request_key="note"
    )
    db.commit()
    runner = AgentRunner(db_factory, settings, ToolGateway())
    lease = runner.claim_one()
    message = db.get(Message, run.user_message_id)
    owner_id = user.id
    context = ActionContext(run.id, lease.token, message.content, message.created_at)
    # Release the reader before opening another transaction (also supports PGlite's serialized bridge).
    db.rollback()
    registry = ToolRegistry(db_factory, settings, owner_id, action_context=context)
    arguments = json.dumps({"title": "Bearing inspection", "body": "Inspect bearing vibration"})
    first = asyncio.run(registry.invoke("save_personal_note", arguments, call_id="save1"))
    repeated = asyncio.run(registry.invoke("save_personal_note", arguments, call_id="save1"))
    assert first.data["note_id"] == repeated.data["note_id"]
    assert db.scalar(select(func.count()).select_from(Note)) == 1
    assert db.scalar(select(func.count()).select_from(PersonalAction)) == 1
    assert first.actions[0]["undo_until"]
    db.rollback()
    with db_factory() as session:
        from app.services.actions import ActionService

        ActionService(session).undo(session.get(User, owner_id), first.actions[0]["id"])
        session.commit()
    db.expire_all()
    assert db.get(Note, first.data["note_id"]) is None
    readonly = ToolRegistry(db_factory, settings, owner_id)
    assert "save_personal_note" not in {s["function"]["name"] for s in readonly.schemas()}
    with pytest.raises(ServiceError):
        asyncio.run(readonly.invoke("save_personal_note", arguments, call_id="save1"))


def test_reminder_tool_requires_explicit_date_and_respects_off(db, db_factory, settings):
    from app.models import Reminder
    from app.models_personal import AssistantSettings
    from app.agents.actions import ActionContext

    user, workspace, _ = seed(db)
    personal = Conversation(owner_id=user.id, title="My assistant")
    db.add(personal)
    db.commit()
    due = utcnow() + timedelta(days=1)
    due = due.replace(second=0, microsecond=0)
    query = "Please remind me at " + due.isoformat() + " to inspect bearings"
    run = AgentRunService(db, settings).enqueue_chat(
        user, personal.id, query, request_key="reminder"
    )
    db.commit()
    lease = AgentRunner(db_factory, settings, ToolGateway()).claim_one()
    registry = ToolRegistry(
        db_factory,
        settings,
        user.id,
        action_context=ActionContext(run.id, lease.token, query, utcnow()),
    )
    args = json.dumps(
        {
            "title": "Inspect bearings",
            "body": "Inspect bearings",
            "due_at": due.isoformat(),
            "user_deadline_text": due.isoformat(),
        }
    )
    db.add(AssistantSettings(owner_id=user.id, reminder_mode="off"))
    db.commit()
    blocked = asyncio.run(registry.invoke("create_personal_reminder", args, call_id="remind1"))
    assert "disabled" in blocked.data["error"]
    assert db.scalar(select(func.count()).select_from(Reminder)) == 0
    db.get(AssistantSettings, user.id).reminder_mode = "suggest"
    db.commit()
    accepted = asyncio.run(registry.invoke("create_personal_reminder", args, call_id="remind1"))
    assert accepted.data["created"]
    assert len(accepted.actions) == 2
    assert db.scalar(select(func.count()).select_from(Reminder)) == 1


def test_personal_chat_graph_commits_action_receipt_and_citation(db, db_factory, settings):
    from app.models import Note

    user, workspace, _ = seed(db)
    conversation = Conversation(owner_id=user.id, title="Personal")
    db.add(conversation)
    db.commit()
    run = AgentRunService(db, settings).enqueue_chat(
        user,
        conversation.id,
        "Please save a note: check the cooling pump",
        request_key="graph-note",
    )
    db.commit()

    class WritingGateway:
        async def complete_turn(self, messages, model=None, request_id=None, **kwargs):
            offered = {t["function"]["name"] for t in kwargs["tools"]}
            assert "save_personal_note" in offered
            if messages[-1]["role"] != "tool":
                return ModelTurn(
                    "",
                    model,
                    {},
                    request_id,
                    [
                        {
                            "id": "save-note",
                            "type": "function",
                            "function": {
                                "name": "save_personal_note",
                                "arguments": '{"title":"Cooling pump","body":"Check the cooling pump"}',
                            },
                        }
                    ],
                )
            assert json.loads(messages[-1]["content"])["data"]["created"]
            return ModelTurn(
                "Saved your cooling pump note. You can undo it in Activity.",
                model,
                {},
                request_id,
                [],
            )

    runner = AgentRunner(db_factory, settings, WritingGateway())
    asyncio.run(runner.execute(runner.claim_one()))
    db.expire_all()
    record = db.get(AgentRun, run.id)
    assert record.status == "succeeded", record.error
    assert len(record.context_metadata["actions"]) == 1
    assert any(ref["type"] == "note" for ref in record.source_refs)
    assert db.scalar(select(func.count()).select_from(Note)) == 1


def test_checkpoint_retention_preserves_final_records_and_active_runs(db, db_factory, settings):
    from langgraph.checkpoint.base import empty_checkpoint

    user, workspace, conversation = seed(db)
    run = AgentRunService(db, settings).enqueue_chat(
        user, conversation.id, "Question", request_key="prune"
    )
    db.commit()
    runner = AgentRunner(db_factory, settings, ToolGateway())
    lease = runner.claim_one()
    saver = FencedCheckpointSaver(db_factory, run.id, lease.token)
    saver.put({"configurable": {"thread_id": run.id}}, empty_checkpoint(), {}, {})
    # Active runs never lose their recovery data, even with an aggressive cutoff.
    assert runner.prune_checkpoints(older_than=utcnow() + timedelta(days=1)) == 0
    db.expire_all()
    record = db.get(AgentRun, run.id)
    record.status = "failed"
    record.finished_at = utcnow() - timedelta(days=10)
    record.lease_token = record.lease_until = None
    db.commit()
    assert runner.prune_checkpoints() == 1
    assert db.scalar(select(func.count()).select_from(AgentCheckpoint)) == 0
    assert db.get(AgentRun, run.id) is not None
    assert db.scalar(select(func.count()).select_from(Message)) == 1
