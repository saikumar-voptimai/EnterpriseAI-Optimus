"""SQLAlchemy LangGraph saver: every write fences against the current run lease.

The graph contains JSON-compatible state only. DB sessions are short-lived and
never held during provider calls. Reclaimed runs resume their latest checkpoint;
a stale executor cannot mutate either checkpoint rows or pending writes.
"""

import asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from app.models import utcnow
from app.models_agent import AgentCheckpoint, AgentCheckpointWrite, AgentRun
from app.services.errors import ServiceError


class LeaseLost(ServiceError):
    def __init__(self):
        super().__init__("The agent run was cancelled or its execution lease was superseded.", 409)


class FencedCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, session_factory, run_id: str, lease_token: str):
        super().__init__()
        self.session_factory, self.run_id, self.lease_token = session_factory, run_id, lease_token

    def _config(self, checkpoint_id=None, namespace=""):
        config = {"configurable": {"thread_id": self.run_id, "checkpoint_ns": namespace}}
        if checkpoint_id:
            config["configurable"]["checkpoint_id"] = checkpoint_id
        return config

    def _identity(self, config):
        values = config["configurable"]
        if values.get("thread_id") != self.run_id:
            raise LeaseLost()
        namespace = values.get("checkpoint_ns", "")
        if len(namespace) > 200:
            raise ServiceError("Checkpoint namespace is too long.")
        return namespace, values.get("checkpoint_id")

    def _fence(self, session):
        current = session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.id == self.run_id,
                AgentRun.status == "running",
                AgentRun.lease_token == self.lease_token,
                AgentRun.lease_until > utcnow(),
            )
            .with_for_update()
        )
        if not current:
            raise LeaseLost()

    def get_tuple(self, config):
        namespace, checkpoint_id = self._identity(config)
        with self.session_factory() as session:
            query = select(AgentCheckpoint).where(
                AgentCheckpoint.run_id == self.run_id, AgentCheckpoint.namespace == namespace
            )
            if checkpoint_id:
                query = query.where(AgentCheckpoint.checkpoint_id == checkpoint_id)
            row = session.scalar(query.order_by(AgentCheckpoint.checkpoint_id.desc()).limit(1))
            if row is None:
                return None
            writes = session.scalars(
                select(AgentCheckpointWrite)
                .where(
                    AgentCheckpointWrite.run_id == self.run_id,
                    AgentCheckpointWrite.namespace == namespace,
                    AgentCheckpointWrite.checkpoint_id == row.checkpoint_id,
                )
                .order_by(AgentCheckpointWrite.task_id, AgentCheckpointWrite.index)
            ).all()
            return CheckpointTuple(
                self._config(row.checkpoint_id, namespace),
                self.serde.loads_typed((row.payload_type, row.payload)),
                row.metadata_json,
                self._config(row.parent_id, namespace) if row.parent_id else None,
                [
                    (w.task_id, w.channel, self.serde.loads_typed((w.payload_type, w.payload)))
                    for w in writes
                ],
            )

    def put(self, config, checkpoint, metadata, new_versions):
        namespace, parent_id = self._identity(config)
        payload_type, payload = self.serde.dumps_typed(checkpoint)
        if len(payload) > 2_000_000:
            raise ServiceError("Agent checkpoint exceeded its bounded size.", 413)
        # LangGraph metadata may include runtime-only values; retain stable fields.
        safe_metadata = {k: metadata[k] for k in ("source", "step", "parents") if k in metadata}
        values = dict(
            run_id=self.run_id,
            namespace=namespace,
            checkpoint_id=checkpoint["id"],
            parent_id=parent_id,
            payload_type=payload_type,
            payload=payload,
            metadata_json=safe_metadata,
        )
        with self.session_factory() as session, session.begin():
            self._fence(session)
            session.execute(insert(AgentCheckpoint).values(**values).on_conflict_do_nothing())
        return self._config(checkpoint["id"], namespace)

    def put_writes(self, config, writes, task_id, task_path=""):
        namespace, checkpoint_id = self._identity(config)
        with self.session_factory() as session, session.begin():
            self._fence(session)
            for idx, (channel, value) in enumerate(writes):
                payload_type, payload = self.serde.dumps_typed(value)
                if len(payload) > 2_000_000:
                    raise ServiceError("Agent checkpoint write exceeded its bounded size.", 413)
                values = dict(
                    run_id=self.run_id,
                    namespace=namespace,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    index=WRITES_IDX_MAP.get(channel, idx),
                    channel=channel,
                    payload_type=payload_type,
                    payload=payload,
                )
                statement = insert(AgentCheckpointWrite).values(**values)
                if channel in WRITES_IDX_MAP:
                    statement = statement.on_conflict_do_update(
                        index_elements=["run_id", "namespace", "checkpoint_id", "task_id", "index"],
                        set_={"channel": channel, "payload_type": payload_type, "payload": payload},
                    )
                else:
                    statement = statement.on_conflict_do_nothing()
                session.execute(statement)

    def list(self, config, *, filter=None, before=None, limit=None):
        namespace, _ = self._identity(config or self._config())
        with self.session_factory() as session:
            query = select(AgentCheckpoint.checkpoint_id).where(
                AgentCheckpoint.run_id == self.run_id, AgentCheckpoint.namespace == namespace
            )
            if before:
                query = query.where(
                    AgentCheckpoint.checkpoint_id < before["configurable"]["checkpoint_id"]
                )
            ids = session.scalars(
                query.order_by(AgentCheckpoint.checkpoint_id.desc()).limit(min(limit or 100, 100))
            ).all()
        for checkpoint_id in ids:
            row = self.get_tuple(self._config(checkpoint_id, namespace))
            if row and (not filter or all(row.metadata.get(k) == v for k, v in filter.items())):
                yield row

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            yield row
