"""Typed, audience-bound read tools. No arbitrary SQL, URLs or Python execution."""

import json
import math
import statistics
from dataclasses import dataclass, field as dataclass_field
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, or_
from app.models import User, Incident, Report, Workspace
from app.repositories import AccessRepository
from app.services.errors import ProviderError, ServiceError


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(Arguments):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=6, ge=1, le=12)


class IncidentArgs(Arguments):
    status: Literal["open", "acknowledged", "resolved", "all"] = "open"
    limit: int = Field(default=8, ge=1, le=20)


class ReportArgs(Arguments):
    query: str = Field(default="", max_length=300)
    limit: int = Field(default=5, ge=1, le=10)


class CalculateArgs(Arguments):
    values: list[float] = Field(min_length=1, max_length=500)
    reference: float | None = None
    unit: str = Field(default="", max_length=40)


class NoteArgs(Arguments):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=6000)


class ReminderArgs(NoteArgs):
    due_at: AwareDatetime
    user_deadline_text: str = Field(min_length=1, max_length=500)


class CalendarArgs(Arguments):
    start: AwareDatetime
    end: AwareDatetime
    limit: int = Field(default=15, ge=1, le=30)


class ConnectionsArgs(Arguments):
    pass


class DescribeArgs(Arguments):
    connection_id: str = Field(min_length=1, max_length=100)
    bucket_id: str = Field(min_length=1, max_length=200)
    measurement: str | None = Field(default=None, min_length=1, max_length=200)
    lookback_days: int = Field(default=365, ge=1, le=3650)


class SeriesArgs(Arguments):
    connection_id: str = Field(min_length=1, max_length=100)
    bucket_id: str = Field(min_length=1, max_length=200)
    measurement: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    stop: AwareDatetime
    limit: int = Field(default=100, ge=1, le=200)
    aggregate_minutes: int | None = Field(default=None, ge=1, le=1440)
    aggregation: Literal["mean", "sum", "last"] = "mean"
    tags: dict[str, str] = Field(default_factory=dict, max_length=10)


@dataclass(frozen=True)
class ToolResult:
    data: dict | list
    sources: list[dict]
    actions: list[dict] = dataclass_field(default_factory=list)


class ToolRegistry:
    definitions = {
        "save_personal_note": (
            "Save a personal note only when the current user explicitly asks to save, write or create a note. Returns an Undo action.",
            NoteArgs,
        ),
        "create_personal_reminder": (
            "Create a reminder only for an explicit user request containing a clear date and clock time. Quote the exact user deadline text; never invent a deadline. Returns Undo actions.",
            ReminderArgs,
        ),
        "knowledge_search": (
            "Search authorized knowledge documents and SOPs for the current audience.",
            SearchArgs,
        ),
        "list_incidents": (
            "Read current incidents in this workspace or approved summaries in personal context.",
            IncidentArgs,
        ),
        "find_reports": (
            "Find prior authorized reports for this workspace or your personal assistant.",
            ReportArgs,
        ),
        "calendar_events": (
            "Read your synchronized personal calendar events in an explicit timezone-aware window.",
            CalendarArgs,
        ),
        "list_data_connections": (
            "List authorized read-only InfluxDB connections with their enabled buckets (ID and name).",
            ConnectionsArgs,
        ),
        "describe_timeseries": (
            "Discover what an enabled InfluxDB bucket contains: without a measurement, its measurement names; with one, its field names, tag keys and latest reading time. Use it to find exact names and a window that has data before read_timeseries; never guess names.",
            DescribeArgs,
        ),
        "read_timeseries": (
            "Read a bounded measured series (at most 32 days) from an enabled InfluxDB bucket. Supply exact measurement, field and tags from describe_timeseries; never invent data.",
            SeriesArgs,
        ),
        "calculate_statistics": (
            "Calculate count, sum, mean, extrema and population standard deviation of numeric evidence.",
            CalculateArgs,
        ),
    }

    def __init__(
        self,
        session_factory,
        settings,
        user_id,
        workspace_id=None,
        project_id=None,
        gateway=None,
        action_context=None,
    ):
        self.session_factory, self.settings = session_factory, settings
        self.user_id, self.workspace_id, self.project_id = user_id, workspace_id, project_id
        self.gateway = gateway
        self.action_context = action_context

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": schema.model_json_schema(),
                },
            }
            for name, (description, schema) in self.definitions.items()
            if (name != "calendar_events" or not self.workspace_id)
            and (
                name not in {"save_personal_note", "create_personal_reminder"}
                or self.action_context is not None
                and not self.workspace_id
            )
        ]

    def _authorize(self, session):
        access = AccessRepository(session)
        user = session.get(User, self.user_id)
        if user is None or not user.active:
            raise ServiceError("The account is inactive.", 403)
        if self.workspace_id:
            workspace = access.workspace(user, self.workspace_id, roles={"member", "manager"})
            if not workspace.external_ai_enabled:
                raise ServiceError("External AI is disabled for this workspace.", 403)
        elif self.project_id:
            access.project(user, self.project_id)
        if not self.settings.allow_external_ai:
            raise ServiceError("External AI is disabled.", 403)
        return user, access

    async def invoke(self, name: str, arguments: str, call_id: str | None = None) -> ToolResult:
        if (
            name not in self.definitions
            or name == "calendar_events"
            and self.workspace_id
            or name in {"save_personal_note", "create_personal_reminder"}
            and (self.workspace_id or self.action_context is None)
        ):
            raise ServiceError("This tool is not enabled for the current run.", 403)
        try:
            params = self.definitions[name][1].model_validate_json(arguments)
        except ValidationError as exc:
            # Give the model a bounded repair hint; never run malformed arguments.
            return ToolResult(
                {
                    "error": "Invalid arguments for " + name,
                    "fields": sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]}),
                },
                [],
            )
        with self.session_factory() as session:
            user, access = self._authorize(session)
            if name in {"save_personal_note", "create_personal_reminder"}:
                from app.agents.actions import PersonalActionTools

                data, refs, actions = PersonalActionTools(session, self.action_context).execute(
                    user, name, params, call_id, self.project_id
                )
                return ToolResult(data, refs, actions)
            if name == "calendar_events":
                from app.services.calendar_sync import CalendarSyncService
                from app.models_connections import Connection

                events = [
                    e
                    for e in CalendarSyncService(session, self.settings).events(
                        user, params.start, params.end
                    )
                    if not e["cancelled"]
                ][: params.limit]
                data, sources = [], []
                for event in events:
                    connection = session.get(Connection, event["connection_id"])
                    data.append(
                        {
                            key: str(event[key]) if key in {"starts_at", "ends_at"} else event[key]
                            for key in ("id", "title", "starts_at", "ends_at", "cancelled", "busy")
                        }
                    )
                    data[-1]["calendar_synced_at"] = (
                        connection.last_synced_at.isoformat()
                        if connection and connection.last_synced_at
                        else None
                    )
                    sources.append(
                        {
                            "type": "calendar_event",
                            "id": event["id"],
                            "title": event["title"],
                            "version": event["updated_at"].isoformat(),
                        }
                    )
                return ToolResult(data, sources)
            if name in {"list_data_connections", "describe_timeseries", "read_timeseries"}:
                from app.services.connections import ConnectionService

                service = ConnectionService(session, self.settings)

                def permitted(connection):
                    if self.workspace_id:
                        return connection.workspace_id == self.workspace_id
                    if connection.workspace_id:
                        return access.workspace(user, connection.workspace_id).external_ai_enabled
                    return connection.owner_id == self.user_id

                if name == "list_data_connections":
                    connections = [
                        c
                        for c in service.repo.list(user, self.workspace_id)
                        if c.provider == "influxdb" and c.status == "connected" and permitted(c)
                    ]
                    listed = []
                    for c in connections:
                        try:
                            names = await service.bucket_names(user, c.id)
                        except (ProviderError, ServiceError):
                            names = {}
                        listed.append(
                            {
                                "id": c.id,
                                "name": c.name,
                                "buckets": [
                                    {"id": b, "name": names.get(b)}
                                    for b in c.config.get("bucket_ids", [])
                                ],
                            }
                        )
                    return ToolResult(
                        listed,
                        [
                            {
                                "type": "connection",
                                "id": c.id,
                                "title": c.name,
                                "version": c.updated_at.isoformat(),
                            }
                            for c in connections
                        ],
                    )
                connection = service.repo.get(user, params.connection_id)
                if not permitted(connection):
                    raise ServiceError(
                        "This connection is outside the current workspace audience.", 403
                    )
                reference = {
                    "type": "connection",
                    "id": connection.id,
                    "title": connection.name,
                    "version": connection.updated_at.isoformat(),
                    "read_only": True,
                }
                if name == "describe_timeseries":
                    description = await service.describe_series(
                        user,
                        params.connection_id,
                        bucket_id=params.bucket_id,
                        measurement=params.measurement,
                        lookback_days=params.lookback_days,
                    )
                    return ToolResult(description, [reference])
                rows = await service.query_series(
                    user,
                    params.connection_id,
                    bucket_id=params.bucket_id,
                    measurement=params.measurement,
                    field=params.field,
                    start=params.start,
                    stop=params.stop,
                    limit=params.limit,
                    aggregate_minutes=params.aggregate_minutes,
                    aggregation=params.aggregation,
                    tags=params.tags,
                )
                return ToolResult(
                    {
                        "readings": rows,
                        "window": {
                            "start": params.start.isoformat(),
                            "end": params.stop.isoformat(),
                        },
                        "measurement": params.measurement,
                        "field": params.field,
                        "aggregation": params.aggregation,
                        "aggregate_minutes": params.aggregate_minutes,
                        "tags": params.tags,
                    },
                    [reference],
                )
            if name == "calculate_statistics":
                values = params.values
                if (
                    any(not math.isfinite(v) or abs(v) > 1e100 for v in values)
                    or params.reference is not None
                    and not math.isfinite(params.reference)
                ):
                    return ToolResult(
                        {"error": "Numeric values must be finite and within supported range."}, []
                    )
                average = statistics.fmean(values)
                data = {
                    "count": len(values),
                    "sum": math.fsum(values),
                    "mean": average,
                    "min": min(values),
                    "max": max(values),
                    "population_stddev": statistics.pstdev(values),
                    "unit": params.unit,
                    "input_origin": "values supplied in the model tool call; verify against cited evidence",
                }
                if params.reference is not None:
                    data.update(
                        reference=params.reference,
                        difference=average - params.reference,
                        percent_change=(
                            (average - params.reference) / abs(params.reference) * 100
                            if params.reference
                            else None
                        ),
                    )
                return ToolResult(data, [])
            if name == "knowledge_search":
                from app.services.retrieval import RetrievalService

                records = await RetrievalService(session, self.settings).search_hybrid(
                    user,
                    params.query,
                    workspace_id=self.workspace_id,
                    project_id=self.project_id,
                    limit=params.limit,
                    gateway=self.gateway,
                )
            elif name == "list_incidents":
                if self.workspace_id:
                    query = select(Incident).where(Incident.workspace_id == self.workspace_id)
                    if params.status != "all":
                        query = query.where(Incident.status == params.status)
                    incidents = session.scalars(
                        query.order_by(Incident.updated_at.desc(), Incident.id).limit(params.limit)
                    ).all()
                    records = [
                        {
                            "type": "incident",
                            "id": i.id,
                            "title": i.title,
                            "version": i.updated_at.isoformat(),
                            "text": json.dumps(
                                {
                                    "summary": i.summary,
                                    "severity": i.severity,
                                    "status": i.status,
                                    "decision": i.decision,
                                    "resolution": i.resolution,
                                }
                            )[:4000],
                        }
                        for i in incidents
                    ]
                else:
                    query = access.publication_statement(user).where(
                        Workspace.external_ai_enabled.is_(True)
                    )
                    if params.status != "all":
                        query = query.where(Incident.status == params.status)
                    incidents = session.execute(
                        query.order_by(Incident.updated_at.desc()).limit(params.limit)
                    ).mappings()
                    records = [
                        {
                            "type": "incident",
                            "id": i["id"],
                            "title": i["title"],
                            "version": i["updated_at"].isoformat(),
                            "summary_only": True,
                            "text": str(i["summary"])[:4000],
                        }
                        for i in incidents
                    ]
            else:
                where = (
                    Report.workspace_id == self.workspace_id
                    if self.workspace_id
                    else or_(
                        (Report.owner_id == self.user_id) & Report.workspace_id.is_(None),
                        Report.workspace_id.in_(access.workspace_ids(user)),
                    )
                )
                query = select(Report).where(where)
                if params.query:
                    query = query.where(
                        or_(
                            Report.title.icontains(params.query, autoescape=True),
                            Report.body.icontains(params.query, autoescape=True),
                        )
                    )
                reports = session.scalars(
                    query.order_by(Report.created_at.desc()).limit(params.limit * 3)
                ).all()
                records = [
                    {
                        "type": "report",
                        "id": r.id,
                        "title": r.title,
                        "version": r.created_at.isoformat(),
                        "text": r.body[:4000],
                    }
                    for r in reports
                ]
            records = access.source_records_allowed(user, records, for_external_ai=True)[
                : params.limit
            ]
            refs = [{k: v for k, v in r.items() if k != "text"} for r in records]
            return ToolResult(records, refs)
