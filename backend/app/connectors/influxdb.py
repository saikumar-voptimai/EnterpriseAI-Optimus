"""InfluxDB v2 read-only discovery and generated bounded Flux queries.

No arbitrary Flux, write endpoint, command, URL, or PLC-write path is exposed.
The operator must additionally issue a read-only bucket token at the source.
"""

import csv
import io
import json
import math
from datetime import datetime
from app.connectors.base import ConnectorHTTP, validate_url
from app.services.errors import ServiceError, ProviderError


class InfluxAdapter:
    def __init__(self, config, credentials, http=None):
        self.config, self.credentials, self.http = config, credentials, http or ConnectorHTTP()
        self.url = validate_url(
            config["url"],
            allow_private=config.get("allow_private_network", False),
            allow_query=False,
        )
        self.headers = {"Authorization": "Token " + credentials["token"]}

    async def resources(self):
        try:
            orgs = await self.http.json(
                "GET", self.url + "/api/v2/orgs", headers=self.headers, params={"limit": 100}
            )
        except ProviderError:
            orgs = {}
        params = {"limit": 100}
        if self.config.get("org_id"):
            params["orgID"] = self.config["org_id"]
        buckets = [
            {"id": i["id"], "name": i["name"], "org_id": i["orgID"]}
            for i in (
                await self.http.json(
                    "GET", self.url + "/api/v2/buckets", headers=self.headers, params=params
                )
            ).get("buckets", [])
        ]
        organizations = {i["id"]: i["name"] for i in orgs.get("orgs", [])}
        # Read tokens scoped to buckets usually cannot list organizations; each
        # bucket still names its organization, which is all a query needs.
        for bucket in buckets:
            organizations.setdefault(bucket["org_id"], f"Organization {bucket['org_id']}")
        return {
            "organizations": [{"id": k, "name": v} for k, v in organizations.items()],
            "buckets": buckets,
        }

    def _enabled_bucket(self, bucket_id):
        if bucket_id not in self.config.get("bucket_ids", []):
            raise ServiceError("This bucket is not enabled for this connection.", 403)
        if not self.config.get("org_id"):
            raise ServiceError("Choose an InfluxDB organization first.", 422)

    async def bucket_names(self):
        """Names of the enabled buckets, keyed by ID."""
        buckets = await self.http.json(
            "GET",
            self.url + "/api/v2/buckets",
            headers=self.headers,
            params={"limit": 100, "orgID": self.config.get("org_id", "")},
        )
        enabled = set(self.config.get("bucket_ids", []))
        return {i["id"]: i["name"] for i in buckets.get("buckets", []) if i["id"] in enabled}

    async def _flux_values(self, flux, limit):
        raw = await self.http.request(
            "POST",
            self.url + "/api/v2/query",
            headers={**self.headers, "Accept": "application/csv"},
            params={"orgID": self.config["org_id"]},
            json={"query": flux, "type": "flux", "dialect": {"annotations": []}},
        )
        reader = csv.DictReader(
            line
            for line in io.StringIO(raw.decode("utf-8"))
            if line.strip() and not line.startswith("#")
        )
        values = [row.get("_value") or row.get("_time") for row in reader]
        return [v for v in values if v and v not in ("_value", "_time")][:limit]

    async def describe(self, *, bucket_id, measurement=None, lookback_days=365):
        """List measurements, or one measurement's fields, tag keys and latest reading.

        Uses InfluxDB schema metadata queries generated here; no caller-supplied Flux.
        """
        self._enabled_bucket(bucket_id)
        if not 1 <= int(lookback_days) <= 3650:
            raise ServiceError("Choose a lookback between 1 and 3650 days.", 422)
        if measurement is not None and (
            not isinstance(measurement, str)
            or not 1 <= len(measurement) <= 200
            or "${" in measurement
        ):
            raise ServiceError("Invalid measurement name.", 422)
        name = (await self.bucket_names()).get(bucket_id)
        if not name:
            raise ServiceError("This bucket is not available to the configured token.", 404)
        q = lambda value: json.dumps(value, ensure_ascii=False)
        start = f"-{int(lookback_days)}d"
        schema = 'import "influxdata/influxdb/schema"\n'
        result = {"bucket_id": bucket_id, "bucket": name, "lookback_days": int(lookback_days)}
        if measurement is None:
            result["measurements"] = await self._flux_values(
                schema + f"schema.measurements(bucket: {q(name)}, start: {start})", 200
            )
            return result
        args = f"bucket: {q(name)}, measurement: {q(measurement)}, start: {start}"
        fields = await self._flux_values(schema + f"schema.measurementFieldKeys({args})", 300)
        tags = await self._flux_values(schema + f"schema.measurementTagKeys({args})", 60)
        result.update(
            measurement=measurement,
            fields=fields,
            tag_keys=[t for t in tags if not t.startswith("_")][:50],
            latest_reading_at=None,
        )
        if fields:
            try:
                latest = await self._flux_values(
                    f"from(bucketID: {q(bucket_id)}) |> range(start: {start}) "
                    f"|> filter(fn: (r) => r._measurement == {q(measurement)} and r._field == {q(fields[0])}) "
                    '|> last() |> keep(columns: ["_time"])',
                    1,
                )
                result["latest_reading_at"] = latest[0] if latest else None
            except ProviderError:
                pass  # Recency is a hint; the field list is still valid.
        return result

    async def query_series(
        self,
        *,
        bucket_id,
        measurement,
        field,
        start,
        stop,
        limit=1000,
        aggregate_minutes=None,
        aggregation="mean",
        tags=None,
    ):
        self._enabled_bucket(bucket_id)
        if (
            start.tzinfo is None
            or stop.tzinfo is None
            or start >= stop
            or (stop - start).total_seconds() > 32 * 86400
        ):
            raise ServiceError("Choose an aware time range of at most 32 days.", 422)
        if not 1 <= limit <= 5000 or any(
            not isinstance(v, str) or not 1 <= len(v) <= 200 for v in [measurement, field]
        ):
            raise ServiceError("Invalid series query limits.", 422)
        tags = tags or {}
        if (
            not isinstance(tags, dict)
            or len(tags) > 10
            or any(
                not isinstance(k, str)
                or not isinstance(v, str)
                or not 1 <= len(k) <= 100
                or len(v) > 200
                or k.startswith("_")
                for k, v in tags.items()
            )
        ):
            raise ServiceError("Provide at most ten named tag filters.", 422)
        if aggregation not in {"mean", "sum", "last"}:
            raise ServiceError("Aggregation must be mean, sum or last.", 422)
        if any(
            "${" in value for value in [bucket_id, measurement, field, *tags.keys(), *tags.values()]
        ):
            raise ServiceError("Flux interpolation is not permitted in resource names.", 422)
        q = lambda value: json.dumps(value, ensure_ascii=False)
        flux = (
            f"from(bucketID: {q(bucket_id)}) |> range(start: time(v: {q(start.isoformat())}), stop: time(v: {q(stop.isoformat())})) "
            f"|> filter(fn: (r) => r._measurement == {q(measurement)} and r._field == {q(field)}) "
        )
        for key, value in sorted(tags.items()):
            flux += f"|> filter(fn: (r) => r[{q(key)}] == {q(value)}) "
        if aggregate_minutes is not None:
            if not 1 <= aggregate_minutes <= 1440:
                raise ServiceError("Aggregation must be between 1 and 1440 minutes.", 422)
            offset = int(start.timestamp()) % (int(aggregate_minutes) * 60)
            flux += f'|> aggregateWindow(every: {int(aggregate_minutes)}m, offset: {offset}s, fn: {aggregation}, createEmpty: false, timeSrc: "_start") '
        # Group and sort ensure the limit bounds the whole result, not each tag table.
        flux += f'|> group() |> sort(columns: ["_time"]) |> limit(n: {limit+1}) |> keep(columns: ["_time", "_value"])'
        raw = await self.http.request(
            "POST",
            self.url + "/api/v2/query",
            headers={**self.headers, "Accept": "application/csv"},
            params={"orgID": self.config["org_id"]},
            json={"query": flux, "type": "flux", "dialect": {"annotations": []}},
        )
        reader = csv.DictReader(
            line for line in io.StringIO(raw.decode("utf-8")) if not line.startswith("#")
        )
        if reader.fieldnames and not {"_time", "_value"} <= set(reader.fieldnames):
            raise ProviderError("InfluxDB returned an invalid series table.")
        result = []
        timestamps = set()
        for item in reader:
            if not item.get("_time") or item["_time"] == "_time":
                continue
            try:
                value = float(item["_value"])
                timestamp = datetime.fromisoformat(item["_time"].replace("Z", "+00:00"))
                if not math.isfinite(value) or timestamp.tzinfo is None:
                    raise ValueError()
            except (ValueError, KeyError) as exc:
                raise ProviderError(
                    "InfluxDB returned a nonnumeric or invalid series value."
                ) from exc
            if timestamp.isoformat() in timestamps:
                raise ProviderError(
                    "Multiple series share a timestamp. Add explicit tag filters to select one series."
                )
            timestamps.add(timestamp.isoformat())
            result.append({"timestamp": timestamp.isoformat(), "value": value})
            if len(result) > limit:
                raise ProviderError(
                    "Query result exceeds the row limit. Narrow the time range or choose aggregation."
                )
        return result
