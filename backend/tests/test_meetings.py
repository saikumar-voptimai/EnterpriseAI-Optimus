"""Meeting evidence, review authority and idempotent personal/workspace publication."""

from datetime import timedelta
from sqlalchemy import select
import pytest
from app.models import User, Scope, Workspace, Membership, Note, Reminder, Document, utcnow
from app.models_connections import MeetingDistribution
from app.repositories.access import AuthorizationError, NotFound
from app.services.meetings import MeetingService, Minutes
from app.services.errors import ServiceError


def setup(db):
    owner = User(
        name="Owner", email="owner@meeting.test", password_hash="unused", active=True, clearance=3
    )
    attendee = User(
        name="Attendee",
        email="attendee@meeting.test",
        password_hash="unused",
        active=True,
        clearance=2,
    )
    outsider = User(
        name="Outsider",
        email="outsider@meeting.test",
        password_hash="unused",
        active=True,
        clearance=3,
        is_admin=True,
    )
    scope = Scope(name="Unit", kind="unit")
    db.add_all([owner, attendee, outsider, scope])
    db.flush()
    workspace = Workspace(
        name="Maintenance",
        scope_id=scope.id,
        created_by=owner.id,
        classification=2,
        external_ai_enabled=False,
    )
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=owner.id, role="manager"))
    db.flush()
    service = MeetingService(db)
    room = service.create(owner, title="Maintenance review", attendee_ids=[attendee.id])
    artifact = service.import_artifact(
        owner,
        room.id,
        kind="transcript",
        content="Owner: We agreed to inspect the bearing. Attendee will inspect the bearing tomorrow.",
        source_label="Supplied transcript",
    )
    db.commit()
    return owner, attendee, outsider, workspace, room, artifact


def test_quote_validation_and_owner_review_boundary(db):
    owner, attendee, outsider, workspace, room, artifact = setup(db)
    service = MeetingService(db)
    invalid = Minutes(
        summary="Review",
        decisions=[
            {
                "text": "Replace bearing",
                "evidence": [{"artifact_id": artifact.id, "quote": "Invented quotation"}],
            }
        ],
    )
    with pytest.raises(ServiceError, match="quote"):
        service.review(owner, room.id, minutes=invalid, workspace_ids=[])
    with pytest.raises(AuthorizationError):
        service.review(attendee, room.id, minutes=Minutes(summary="Review"), workspace_ids=[])
    with pytest.raises(NotFound):
        service.repo.meeting(outsider, room.id)
    assert service.list(outsider) == []


def test_publish_and_accept_are_idempotent_and_do_not_copy_private_artifact_ids(db):
    owner, attendee, _, workspace, room, artifact = setup(db)
    service = MeetingService(db)
    minutes = Minutes(
        summary="Inspect the bearing.",
        decisions=[
            {
                "text": "Inspect bearing",
                "evidence": [
                    {"artifact_id": artifact.id, "quote": "We agreed to inspect the bearing."}
                ],
            }
        ],
        actions=[
            {
                "text": "Inspect the bearing",
                "assignee_id": attendee.id,
                "due_at": utcnow() + timedelta(days=1),
                "evidence": [
                    {
                        "artifact_id": artifact.id,
                        "quote": "Attendee will inspect the bearing tomorrow.",
                    }
                ],
            }
        ],
    )
    service.review(owner, room.id, minutes=minutes, workspace_ids=[workspace.id])
    db.commit()
    service.publish(owner, room.id)
    db.commit()
    service.publish(owner, room.id)
    db.commit()
    docs = list(db.scalars(select(Document)))
    assert len(docs) == 1 and artifact.id not in docs[0].body and "Attendee" in docs[0].body
    first = service.accept(attendee, room.id)
    db.commit()
    second = service.accept(attendee, room.id)
    db.commit()
    assert first["note_id"] == second["note_id"] and second["already_accepted"]
    assert len(list(db.scalars(select(Note).where(Note.owner_id == attendee.id)))) == 1
    assert len(list(db.scalars(select(Reminder).where(Reminder.owner_id == attendee.id)))) == 1
    assert len(list(db.scalars(select(MeetingDistribution)))) == 2
    with pytest.raises(ServiceError, match="immutable"):
        service.import_artifact(
            owner, room.id, kind="notes", content="Late change", source_label="Late"
        )


def test_attendee_approval_required_before_personal_updates(db):
    owner, attendee, _, workspace, room, artifact = setup(db)
    service = MeetingService(db)
    with pytest.raises(ServiceError, match="publish"):
        service.accept(attendee, room.id)
    service.review(owner, room.id, minutes=Minutes(summary="Approved summary"), workspace_ids=[])
    service.publish(owner, room.id)
    db.commit()
    assert list(db.scalars(select(Note))) == []
    assert list(db.scalars(select(Reminder))) == []
    service.accept(attendee, room.id)
    db.commit()
    assert len(list(db.scalars(select(Note)))) == 1


def test_duplicate_artifact_does_not_invalidate_review(db):
    owner, _, _, _, room, artifact = setup(db)
    service = MeetingService(db)
    service.review(owner, room.id, minutes=Minutes(summary="Reviewed"), workspace_ids=[])
    db.commit()
    same = service.import_artifact(
        owner, room.id, kind="transcript", content=artifact.content, source_label="Duplicate upload"
    )
    assert same.id == artifact.id and room.status == "reviewed"


def test_long_transcript_extraction_checkpoints_each_chunk_and_retains_all_actions(db):
    import asyncio, json
    from types import SimpleNamespace
    from app.models_connections import MeetingExtraction

    owner, attendee, _, _, room, artifact = setup(db)
    service = MeetingService(db)
    # Ordinary hour-long transcript sized evidence spans multiple model contexts.
    text = (
        "The team reviewed normal operation and supporting evidence.\n" * 1200
    ) + "\nFinal decision: inspect bearing."
    service.import_artifact(
        owner, room.id, kind="transcript", content=text, source_label="Long transcript"
    )
    db.commit()
    calls = []

    class Runner:
        async def run_bundle(self, user_id, bundle, **kwargs):
            assert kwargs["tools_enabled"] is False
            calls.append(bundle.messages)
            if "segment" in bundle.messages[-1]["content"]:
                payload = json.loads(bundle.messages[-1]["content"])
                quote = payload["segment"][: min(80, len(payload["segment"]))]
                content = json.dumps(
                    {
                        "summary": "Evidence reviewed.",
                        "decisions": [
                            {
                                "text": "Decision " + str(len(calls)),
                                "evidence": [
                                    {"artifact_id": payload["artifact_id"], "quote": quote}
                                ],
                            }
                        ],
                        "actions": [],
                        "open_questions": [],
                    }
                )
            else:
                content = "Consolidated evidence review."
            return SimpleNamespace(content=content)

    service = MeetingService(db, runner=Runner())
    asyncio.run(service.draft(owner, room.id, external_ai_consent=True))
    db.commit()
    job = db.scalar(select(MeetingExtraction))
    total = len(job.chunks)
    assert total > 3 and job.cursor == 0
    asyncio.run(service.process_one())
    db.expire_all()
    job = db.get(MeetingExtraction, job.id)
    assert job.cursor == 1 and job.status == "queued" and len(job.partials) == 1
    # Recreating the service simulates worker restart; the completed chunk is not rerun.
    service = MeetingService(db, runner=Runner())
    for _ in range(total * 3 + 5):
        asyncio.run(service.process_one())
        db.expire_all()
        job = db.get(MeetingExtraction, job.id)
        if job.status == "succeeded":
            break
    assert job.status == "succeeded" and job.cursor == total
    db.refresh(room)
    assert room.status == "draft" and len(room.minutes["decisions"]) == total
    assert all(d["evidence"] for d in room.minutes["decisions"])
    assert len(calls) == total + (total - 1)


def test_cancelled_extraction_cannot_publish_late_model_result(db):
    import asyncio, json
    from types import SimpleNamespace
    from app.models_connections import MeetingExtraction

    owner, _, _, _, room, _ = setup(db)

    class CancellingRunner:
        async def run_bundle(self, *args, **kwargs):
            MeetingService(db).cancel_extraction(owner, room.id)
            db.commit()
            return SimpleNamespace(
                content=json.dumps(
                    {"summary": "Late result", "decisions": [], "actions": [], "open_questions": []}
                )
            )

    service = MeetingService(db, runner=CancellingRunner())
    asyncio.run(service.draft(owner, room.id, external_ai_consent=True))
    db.commit()
    asyncio.run(service.process_one())
    db.refresh(room)
    assert db.scalar(select(MeetingExtraction)).status == "cancelled"
    assert room.status == "open" and room.minutes == {}
