"""Relational constraints and authorization boundaries on a migrated PostgreSQL DB.

The shared ``db`` fixture is a fresh Session, with migrations applied and tables
emptied between tests. Constraint failures are caught inside PostgreSQL so these
checks also work with the development PGlite socket test adapter.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from app.models import (
    Base,
    Conversation,
    Document,
    Incident,
    Membership,
    Note,
    Report,
    Scope,
    ScopeGrant,
    Subscription,
    User,
    Workspace,
)
from app.repositories import AccessRepository, AuthorizationError, NotFound, PrivateRepository


def user(db, name, *, admin=False, clearance=3):
    value = User(
        email=f"{name}@example.test",
        name=name,
        password_hash="test-hash",
        active=True,
        is_admin=admin,
        clearance=clearance,
    )
    db.add(value)
    db.flush()
    return value


def scope(db, name, *, parent=None):
    value = Scope(name=name, kind="test", parent_id=parent.id if parent else None)
    db.add(value)
    db.flush()
    return value


def workspace(db, owner, area, name, *, classification=2):
    value = Workspace(
        name=name, scope_id=area.id, created_by=owner.id, classification=classification
    )
    db.add(value)
    db.flush()
    return value


def assert_database_rejects(db, statement, expected_error):
    """Fail unless this SQL statement raises exactly the expected PostgreSQL error."""
    assert expected_error in {"foreign_key_violation", "check_violation", "unique_violation"}
    db.execute(
        text(
            f"DO $$ BEGIN BEGIN {statement}; "
            "RAISE EXCEPTION 'Expected constraint rejection'; "
            f"EXCEPTION WHEN {expected_error} THEN NULL; END; END $$"
        )
    )


def test_migration_matches_current_metadata(db):
    assert compare_metadata(MigrationContext.configure(db.connection()), Base.metadata) == []


def test_admin_cannot_read_private_context_or_unjoined_workspace(db):
    owner = user(db, "owner")
    admin = user(db, "admin", admin=True)
    area = scope(db, "Area")
    shared = workspace(db, owner, area, "Shared")
    db.add(Membership(workspace_id=shared.id, user_id=owner.id, role="manager"))
    private = PrivateRepository(db)
    project = private.create_project(owner, name="Owner's project")
    note = private.create_note(owner, project_id=project.id, body="Never published")
    memory = private.create_memory(owner, note_id=note.id, text="Private derived context")
    reminder = private.create_reminder(
        owner, note_id=note.id, title="Private reminder", due_at=datetime.now(timezone.utc)
    )
    document = Document(
        owner_id=owner.id,
        title="Personal file",
        filename="personal.txt",
        media_type="text/plain",
        content_hash="a" * 64,
        body="Private text",
        created_by=owner.id,
    )
    conversation = Conversation(owner_id=owner.id, title="Private conversation")
    report = Report(owner_id=owner.id, title="Private report", body="Private results")
    db.add_all([document, conversation, report])
    db.flush()
    access = AccessRepository(db)
    for getter, identifier in [
        (access.project, project.id),
        (access.note, note.id),
        (access.memory, memory.id),
        (access.reminder, reminder.id),
        (access.document, document.id),
        (access.conversation, conversation.id),
        (access.report, report.id),
        (access.workspace, shared.id),
    ]:
        with pytest.raises(NotFound):
            getter(admin, identifier)
    assert access.accessible_workspaces(admin) == []
    assert access.note(owner, note.id).body == "Never published"
    private.forget_memory(owner, memory.id)
    assert private.memories(owner) == []
    assert access.note(owner, note.id).body == "Never published"


def test_membership_role_and_clearance_are_required_together(db):
    owner = user(db, "owner")
    viewer = user(db, "viewer")
    area = scope(db, "Area")
    shared = workspace(db, owner, area, "Restricted", classification=3)
    db.add(Membership(workspace_id=shared.id, user_id=viewer.id, role="viewer"))
    db.flush()
    access = AccessRepository(db)
    assert access.workspace(viewer, shared.id).id == shared.id
    with pytest.raises(AuthorizationError):
        access.workspace(viewer, shared.id, roles=("member", "manager"))
    viewer.clearance = 2
    db.flush()
    with pytest.raises(NotFound):
        access.workspace(viewer, shared.id)
    assert access.accessible_workspaces(viewer) == []
    viewer.active = False
    with pytest.raises(AuthorizationError):
        access.accessible_workspaces(viewer)


def test_review_grants_limit_depth_require_true_flag_and_clearance(db):
    owner = user(db, "owner")
    reviewer = user(db, "reviewer")
    subscriber = user(db, "subscriber")
    admin = user(db, "admin", admin=True)
    root = scope(db, "Root")
    child = scope(db, "Child", parent=root)
    grandchild = scope(db, "Grandchild", parent=child)
    workspaces = [
        workspace(db, owner, area, f"Workspace {i}")
        for i, area in enumerate([root, child, grandchild])
    ]
    grant = ScopeGrant(user_id=reviewer.id, scope_id=root.id, can_review=False)
    db.add_all([grant, Subscription(user_id=subscriber.id, scope_id=root.id)])
    incidents = [
        Incident(
            workspace_id=shared.id,
            title=f"Incident {i}",
            summary="Selected public summary",
            severity="critical",
            published=True,
            assignee_id=owner.id,
            reported_by=owner.id,
            decision="Check equipment",
            resolution="Checked",
            request_id=str(uuid4()),
        )
        for i, shared in enumerate(workspaces)
    ]
    db.add_all(incidents)
    db.flush()
    access = AccessRepository(db)
    assert access.visible_publications(reviewer) == []
    assert access.reviewable_scopes(reviewer) == []
    assert access.visible_publications(subscriber) == []
    assert access.visible_publications(admin) == []
    grant.can_review = True
    db.flush()
    assert {p["id"] for p in access.visible_publications(reviewer)} == {
        incidents[0].id,
        incidents[1].id,
    }
    assert {s.id for s in access.reviewable_scopes(reviewer)} == {root.id, child.id}
    with pytest.raises(NotFound):
        access.incident(reviewer, incidents[0].id)
    incidents[2].escalated = True
    db.flush()
    publications = access.visible_publications(reviewer)
    assert {p["id"] for p in publications} == {item.id for item in incidents}
    assert publications[0]["assignee_id"] == owner.id
    assert publications[0]["decision"] == "Check equipment"
    assert publications[0]["resolution"] == "Checked"
    for publication in publications:
        assert not {"reported_by", "request_id", "note_id", "detail", "events"}.intersection(
            publication
        )
    reviewer.clearance = 1
    db.flush()
    assert access.visible_publications(reviewer) == []
    reviewer.clearance = 3
    grant.can_review = False
    db.flush()
    assert access.visible_publications(reviewer) == []


def test_composite_foreign_keys_prevent_cross_owner_context(db):
    owner = user(db, "owner")
    other = user(db, "other")
    project = PrivateRepository(db).create_project(owner, name="Private")
    note = PrivateRepository(db).create_note(owner, body="Private", project_id=project.id)
    invalid = [
        f"INSERT INTO notes(owner_id,project_id,body) VALUES ('{other.id}','{project.id}','invalid')",
        f"INSERT INTO memories(owner_id,note_id,text) VALUES ('{other.id}','{note.id}','invalid')",
        f"INSERT INTO reminders(owner_id,note_id,title,due_at) VALUES ('{other.id}','{note.id}','invalid',now())",
        f"INSERT INTO conversations(owner_id,project_id,title) VALUES ('{other.id}','{project.id}','invalid')",
        f"INSERT INTO documents(owner_id,project_id,title,filename,media_type,content_hash,body,created_by) "
        f"VALUES ('{other.id}','{project.id}','invalid','invalid.txt','text/plain','{'a' * 64}','invalid','{other.id}')",
    ]
    for statement in invalid:
        assert_database_rejects(db, statement, "foreign_key_violation")


def test_resource_audiences_require_exactly_one_owner_or_workspace(db):
    owner = user(db, "owner")
    area = scope(db, "Area")
    shared = workspace(db, owner, area, "Shared")
    project = PrivateRepository(db).create_project(owner, name="Private")
    invalid = [
        "INSERT INTO conversations(title) VALUES ('invalid')",
        f"INSERT INTO conversations(owner_id,workspace_id,title) VALUES ('{owner.id}','{shared.id}','invalid')",
        f"INSERT INTO conversations(workspace_id,project_id,title) VALUES ('{shared.id}','{project.id}','invalid')",
        "INSERT INTO reports(title,body) VALUES ('invalid','invalid')",
        f"INSERT INTO reports(owner_id,workspace_id,title,body) VALUES ('{owner.id}','{shared.id}','invalid','invalid')",
        "INSERT INTO documents(title,filename,media_type,content_hash,body,created_by) "
        f"VALUES ('invalid','invalid.txt','text/plain','{'a' * 64}','invalid','{owner.id}')",
        "INSERT INTO documents(owner_id,workspace_id,title,filename,media_type,content_hash,body,created_by) "
        f"VALUES ('{owner.id}','{shared.id}','invalid','invalid.txt','text/plain','{'a' * 64}','invalid','{owner.id}')",
    ]
    for statement in invalid:
        assert_database_rejects(db, statement, "check_violation")


def test_report_provenance_blocks_replay_after_workspace_egress_or_access_revocation(db):
    from app.repositories import WorkspaceRepository

    owner = user(db, "owner")
    area = scope(db, "Area")
    shared = workspace(db, owner, area, "Shared")
    shared.external_ai_enabled = True
    membership = Membership(workspace_id=shared.id, user_id=owner.id, role="manager")
    document = Document(
        workspace_id=shared.id,
        title="Shared input",
        filename="input.txt",
        media_type="text/plain",
        content_hash="b" * 64,
        body="Workspace facts",
        created_by=owner.id,
    )
    db.add_all([membership, document])
    db.flush()
    report = Report(
        owner_id=owner.id,
        title="Personal derived report",
        body="Derived workspace facts",
        source_refs=[
            {"type": "document", "id": document.id, "version": document.updated_at.isoformat()}
        ],
    )
    db.add(report)
    db.flush()
    access = AccessRepository(db)
    ref = {"type": "report", "id": report.id, "version": report.created_at.isoformat()}
    assert access.report(owner, report.id).id == report.id
    assert access.source_refs_authorized(owner, [ref], for_external_ai=True)
    shared.external_ai_enabled = False
    db.flush()
    assert access.report(owner, report.id).id == report.id  # Local read remains authorized.
    assert not access.source_refs_authorized(owner, [ref], for_external_ai=True)
    shared.external_ai_enabled = True
    db.delete(membership)
    db.flush()
    with pytest.raises(NotFound):
        access.report(owner, report.id)
    assert not access.source_refs_authorized(owner, [ref])
    assert WorkspaceRepository(db).reports(owner) == []


def test_forgotten_memory_hides_assistant_messages_and_derived_reports(db):
    from app.models import Message
    from app.repositories import WorkspaceRepository

    owner = user(db, "owner")
    private = PrivateRepository(db)
    note = private.create_note(owner, body="An observation")
    memory = private.create_memory(owner, note_id=note.id, text="Derived memory")
    conversation = Conversation(owner_id=owner.id, title="Private chat")
    db.add(conversation)
    db.flush()
    refs = [{"type": "memory", "id": memory.id, "version": memory.updated_at.isoformat()}]
    assistant = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="Memory-based answer",
        source_refs=refs,
    )
    report = Report(
        owner_id=owner.id, title="Derived report", body="Memory-based report", source_refs=refs
    )
    db.add_all([assistant, report])
    db.flush()
    assert WorkspaceRepository(db).messages(owner, conversation.id) == [assistant]
    private.forget_memory(owner, memory.id)
    assert WorkspaceRepository(db).messages(owner, conversation.id) == []
    with pytest.raises(NotFound):
        AccessRepository(db).report(owner, report.id)


def test_source_provenance_is_versioned_bounded_and_fail_closed(db):
    owner = user(db, "owner")
    note = PrivateRepository(db).create_note(owner, body="First version")
    refs = [{"type": "note", "id": note.id, "version": note.updated_at.isoformat()}]
    access = AccessRepository(db)
    assert access.source_refs_authorized(owner, refs, for_external_ai=True)
    note.body = "Changed version"
    db.flush()
    assert access.source_refs_authorized(owner, refs)
    assert not access.source_refs_authorized(owner, refs, for_external_ai=True)
    assert not access.source_refs_authorized(owner, [{"type": "note", "id": "invalid"}])
    assert not access.source_refs_authorized(owner, [{"type": "unknown", "id": note.id}])
    assert not access.source_refs_authorized(owner, {"type": "note", "id": note.id})
    assert not access.source_refs_authorized(owner, [{"type": "note", "id": note.id}] * 513)
    first = Report(owner_id=owner.id, title="First", body="First", source_refs=[])
    second = Report(owner_id=owner.id, title="Second", body="Second", source_refs=[])
    db.add_all([first, second])
    db.flush()
    first.source_refs = [{"type": "report", "id": second.id}]
    second.source_refs = [{"type": "report", "id": first.id}]
    db.flush()
    with pytest.raises(NotFound):
        access.report(owner, first.id)
    assert not access.source_refs_authorized(owner, [{"type": "report", "id": first.id}])
    child = None
    for i in range(17):
        report = Report(
            owner_id=owner.id,
            title=f"Depth {i}",
            body="Derived",
            source_refs=[{"type": "report", "id": child.id}] if child else [],
        )
        db.add(report)
        db.flush()
        child = report
    assert not access.source_refs_authorized(owner, [{"type": "report", "id": child.id}])


def test_summary_source_provenance_does_not_upgrade_raw_incident_access(db):
    owner = user(db, "owner")
    reviewer = user(db, "reviewer")
    area = scope(db, "Area")
    shared = workspace(db, owner, area, "Shared")
    shared.external_ai_enabled = True
    grant = ScopeGrant(user_id=reviewer.id, scope_id=area.id, can_review=True)
    incident = Incident(
        workspace_id=shared.id,
        title="Shared summary",
        summary="Published excerpt",
        severity="high",
        published=True,
        reported_by=owner.id,
        request_id="summary-1",
    )
    db.add_all([grant, incident])
    db.flush()
    access = AccessRepository(db)
    summary_ref = {
        "type": "incident",
        "id": incident.id,
        "summary_only": True,
        "version": incident.updated_at.isoformat(),
    }
    assert access.source_refs_authorized(reviewer, [summary_ref], for_external_ai=True)
    assert not access.source_refs_authorized(reviewer, [{"type": "incident", "id": incident.id}])
    shared.external_ai_enabled = False
    db.flush()
    assert not access.source_refs_authorized(reviewer, [summary_ref], for_external_ai=True)
    grant.can_review = False
    db.flush()
    assert not access.source_refs_authorized(reviewer, [summary_ref])
