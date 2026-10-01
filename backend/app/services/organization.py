"""Reversible weekly filing using validated capture labels and stable folder IDs."""

from datetime import timezone
from sqlalchemy import select

from app.models import Note, Project
from app.models_personal import NotePlacement, OrganizationChange, PersonalCapture, ProjectFolder
from app.repositories.personal_assistant import PersonalAssistantRepository
from app.services.actions import ActionService
from app.services.errors import ServiceError


def topic_name(value: str | None) -> str:
    return (
        " ".join((value or "Notes").replace("/", " ").replace("\\", " ").split())[:120] or "Notes"
    )


class OrganizationService:
    def __init__(self, db):
        self.db = db
        self.repo = PersonalAssistantRepository(db)

    def create_folder(self, user, *, project_id, name, parent_id=None):
        self.repo.lock_owner(user)
        project = self.repo.access.project(user, project_id)
        if project.archived:
            raise ServiceError("Choose an active project.", 422)
        if parent_id:
            parent = self.repo.folder(user, parent_id)
            if parent.project_id != project_id:
                raise ServiceError("A parent folder must belong to the same project.", 422)
            depth = 1
            while parent.parent_id:
                depth += 1
                if depth >= 8:
                    raise ServiceError("Folders support at most eight levels.", 422)
                parent = self.repo.folder(user, parent.parent_id)
        name = topic_name(name)
        folder = self.db.scalar(
            select(ProjectFolder).where(
                ProjectFolder.owner_id == user.id,
                ProjectFolder.project_id == project_id,
                ProjectFolder.parent_id == parent_id,
                ProjectFolder.name == name,
            )
        )
        if folder:
            return folder
        folder = ProjectFolder(
            owner_id=user.id, project_id=project_id, name=name, parent_id=parent_id
        )
        self.db.add(folder)
        self.db.flush()
        return folder

    def preview(self, user, *, project_id=None, limit=500):
        self.repo.lock_owner(user)
        if project_id:
            self.repo.access.project(user, project_id)
        # Existing user filing is respected. Weekly organization deals with the inbox.
        query = (
            select(Note, PersonalCapture)
            .outerjoin(PersonalCapture, PersonalCapture.note_id == Note.id)
            .outerjoin(NotePlacement, NotePlacement.note_id == Note.id)
            .where(Note.owner_id == user.id, NotePlacement.note_id.is_(None))
        )
        if project_id:
            query = query.where(Note.project_id == project_id)
        rows = self.db.execute(
            query.order_by(Note.created_at, Note.id).limit(min(limit, 500))
        ).all()
        moves = []
        for note, capture in rows:
            project = self.db.get(Project, note.project_id) if note.project_id else None
            if project and project.archived:
                continue
            proposed = (capture.classification or {}).get("folder_name") if capture else None
            moves.append(
                {
                    "note_id": note.id,
                    "title": note.title,
                    "before_project_id": note.project_id,
                    "before_folder_id": None,
                    "note_version": note.updated_at.astimezone(timezone.utc).isoformat(),
                    "folder_name": topic_name(proposed or note.kind.title()),
                    "project_id": note.project_id,
                    "folder_id": None,
                }
            )
        change = OrganizationChange(owner_id=user.id, status="preview", moves=moves)
        self.db.add(change)
        self.db.flush()
        return change

    def apply(self, user, change_id):
        self.repo.lock_owner(user)
        change = self.repo.owned(OrganizationChange, user, change_id, lock=True)
        if change.status == "applied":
            return change
        if change.status == "undone":
            raise ServiceError("Create a new preview after undoing an organization.", 409)
        notes = {}
        for move in change.moves:
            note = self.repo.access.note(user, move["note_id"])
            if note.updated_at.astimezone(timezone.utc).isoformat() != move[
                "note_version"
            ] or self.db.get(NotePlacement, note.id):
                raise ServiceError(
                    "Notes changed after this preview. Create a fresh organization preview.", 409
                )
            notes[note.id] = note
        applied = []
        for move in change.moves:
            note = notes[move["note_id"]]
            project_id = note.project_id
            if not project_id:
                inbox = self.db.scalar(
                    select(Project)
                    .where(
                        Project.owner_id == user.id,
                        Project.name == "Inbox",
                        Project.archived.is_(False),
                    )
                    .order_by(Project.created_at)
                    .limit(1)
                )
                if inbox is None:
                    inbox = Project(
                        owner_id=user.id, name="Inbox", goal="Captured thoughts and daily work."
                    )
                    self.db.add(inbox)
                    self.db.flush()
                project_id = inbox.id
            folder = self.create_folder(user, project_id=project_id, name=move["folder_name"])
            self.db.add(
                NotePlacement(
                    note_id=note.id, owner_id=user.id, project_id=project_id, folder_id=folder.id
                )
            )
            note.project_id = project_id
            applied.append({**move, "project_id": project_id, "folder_id": folder.id})
        change.moves, change.status = applied, "applied"
        action = ActionService(self.db)._record(
            user,
            request_id=f"organization:{change.id}",
            kind="organize_notes",
            resource_id=change.id,
            payload={"count": len(applied)},
        )
        change.action_id = action.id
        self.db.flush()
        return change

    def _undo_moves(self, user, change_id):
        change = self.repo.owned(OrganizationChange, user, change_id, lock=True)
        if change.status == "undone":
            return change
        if change.status != "applied":
            raise ServiceError("Only an applied organization can be undone.", 409)
        current = []
        for move in change.moves:
            note = self.repo.access.note(user, move["note_id"])
            placement = self.db.get(NotePlacement, note.id)
            if (
                note.project_id != move["project_id"]
                or not placement
                or placement.folder_id != move["folder_id"]
            ):
                raise ServiceError(
                    "A note was filed elsewhere after this organization. Review it before undoing.",
                    409,
                )
            current.append((move, note, placement))
        for move, note, placement in current:
            self.db.delete(placement)
            note.project_id = move["before_project_id"]
        change.status = "undone"
        self.db.flush()
        return change

    def undo(self, user, change_id):
        change = self.repo.owned(OrganizationChange, user, change_id)
        if not change.action_id:
            raise ServiceError("This organization has not been applied.", 409)
        ActionService(self.db).undo(user, change.action_id)
        return change
