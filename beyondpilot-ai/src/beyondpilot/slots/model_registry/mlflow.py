"""Log forecast runs to MLflow: one experiment per series, one run per call.

Needs ``mlflow``. The tracking URI comes from config (``tracking_uri``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import mlflow
from mlflow.tracking import MlflowClient


class Impl:
    def __init__(self, tracking_uri: str, experiment_prefix: str = "beyondpilot-forecast") -> None:
        self.client = MlflowClient(tracking_uri=tracking_uri)
        self.prefix = experiment_prefix

    def _experiment(self, name: str) -> str:
        full = f"{self.prefix}/{name}"
        existing = self.client.get_experiment_by_name(full)
        return existing.experiment_id if existing else self.client.create_experiment(full)

    def _log(self, name: str, metrics: Mapping[str, float], params: Mapping[str, Any]) -> str:
        run = self.client.create_run(self._experiment(name))
        for key, value in params.items():
            self.client.log_param(run.info.run_id, key, str(value)[:500])
        for key, value in metrics.items():
            self.client.log_metric(run.info.run_id, key, float(value))
        self.client.set_terminated(run.info.run_id)
        return run.info.run_id

    def _latest(self, name: str) -> dict[str, Any] | None:
        experiment = self.client.get_experiment_by_name(f"{self.prefix}/{name}")
        if experiment is None:
            return None
        runs = self.client.search_runs(
            [experiment.experiment_id], order_by=["attributes.start_time DESC"], max_results=1
        )
        if not runs:
            return None
        run = runs[0]
        return {
            "version": run.info.run_id,
            "metrics": dict(run.data.metrics),
            "params": dict(run.data.params),
        }

    async def log(self, name: str, metrics: Mapping[str, float], params: Mapping[str, Any]) -> str:
        return await asyncio.to_thread(self._log, name, metrics, params)

    async def latest(self, name: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._latest, name)


__all__ = ["Impl", "mlflow"]
