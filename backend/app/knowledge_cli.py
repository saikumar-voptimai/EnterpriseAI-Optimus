"""Knowledge maintenance.

python -m app.knowledge_cli backfill      Upgrade v1 extracted documents.
python -m app.knowledge_cli qdrant-sync   Rebuild the Qdrant index from PostgreSQL vectors.
"""

import argparse
import asyncio
import json
from sqlalchemy import func, select
from app.config import get_settings
from app.db import SessionLocal
from app.models import Document, Membership, User, Workspace
from app.models_knowledge import DocumentRevision, KnowledgeChunk
from app.services.ingestion import IngestionService


def backfill(limit=0, dry_run=False):
    counts = {"upgraded": 0, "unavailable_authority": 0, "failed": 0}
    predicate = (
        ~select(DocumentRevision.id).where(DocumentRevision.document_id == Document.id).exists()
    )
    if dry_run:
        with SessionLocal() as session:
            return {
                "eligible_documents": session.scalar(
                    select(func.count()).select_from(Document).where(predicate)
                ),
                "dry_run": True,
            }
    last_id, scanned = None, 0
    while not limit or scanned < limit:
        with SessionLocal() as session:
            statement = (
                select(Document.id)
                .where(predicate)
                .order_by(Document.id)
                .limit(min(100, limit - scanned) if limit else 100)
            )
            if last_id:
                statement = statement.where(Document.id > last_id)
            identifiers = list(session.scalars(statement))
        if not identifiers:
            break
        for identifier in identifiers:
            last_id, scanned = identifier, scanned + 1
            with SessionLocal() as session:
                document = session.get(Document, identifier)
                if document is None:
                    continue
                if document.owner_id:
                    user = session.get(User, document.owner_id)
                else:
                    user = session.scalar(
                        select(User)
                        .join(Membership, Membership.user_id == User.id)
                        .join(Workspace, Workspace.id == Membership.workspace_id)
                        .where(
                            Membership.workspace_id == document.workspace_id,
                            Membership.role.in_(["member", "manager"]),
                            User.active.is_(True),
                            User.clearance >= Workspace.classification,
                        )
                        .order_by(User.id)
                        .limit(1)
                    )
                if user is None or not user.active:
                    counts["unavailable_authority"] += 1
                    continue
                try:
                    IngestionService(session).backfill(user, identifier)
                    session.commit()
                    counts["upgraded"] += 1
                except Exception:
                    session.rollback()
                    counts["failed"] += 1
                    # Do not print source contents or provider details in admin logs.
    return counts


def qdrant_sync(limit=0, dry_run=False):
    """Copy current-revision vectors for the configured profile into Qdrant."""
    from app.services.vector_index import QdrantIndex, audience_key

    settings = get_settings()
    revisions = (
        select(
            DocumentRevision.id,
            Document.id,
            Document.workspace_id,
            Document.owner_id,
            Document.project_id,
        )
        .join(Document, Document.id == DocumentRevision.document_id)
        .where(
            DocumentRevision.is_current.is_(True),
            DocumentRevision.embedding_model == settings.embedding_model,
            DocumentRevision.embedding_dimensions == settings.embedding_dimensions,
        )
        .order_by(DocumentRevision.id)
    )
    if limit:
        revisions = revisions.limit(limit)
    with SessionLocal() as session:
        rows = list(session.execute(revisions))
    if dry_run:
        return {"eligible_revisions": len(rows), "dry_run": True}
    index, counts = QdrantIndex(settings), {"revisions": 0, "points": 0}
    for revision_id, document_id, workspace_id, owner_id, project_id in rows:
        with SessionLocal() as session:
            points = [
                (chunk_id, [float(value) for value in embedding])
                for chunk_id, embedding in session.execute(
                    select(KnowledgeChunk.id, KnowledgeChunk.embedding).where(
                        KnowledgeChunk.revision_id == revision_id,
                        KnowledgeChunk.embedding.is_not(None),
                    )
                )
            ]
        for start in range(0, len(points), 64):
            asyncio.run(
                index.upsert_revision(
                    document_id=document_id,
                    revision_id=revision_id,
                    audience=audience_key(workspace_id, owner_id),
                    project_id=None if workspace_id else project_id,
                    points=points[start : start + 64],
                )
            )
        counts["revisions"] += 1
        counts["points"] += len(points)
    return counts


def main():
    parser = argparse.ArgumentParser(description="Knowledge maintenance")
    parser.add_argument("command", choices=["backfill", "qdrant-sync"])
    parser.add_argument("--limit", type=int, default=0, help="Maximum documents, 0 means all")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    if arguments.limit < 0:
        parser.error("--limit cannot be negative")
    command = qdrant_sync if arguments.command == "qdrant-sync" else backfill
    result = command(arguments.limit, arguments.dry_run)
    print(json.dumps(result))
    if result.get("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
