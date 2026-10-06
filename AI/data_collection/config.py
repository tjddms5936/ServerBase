from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AI_ROOT = Path(__file__).resolve().parents[1]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Scenario(StrictModel):
    scenario_id: Identifier
    label: Annotated[int, Field(ge=0, le=1, strict=True)]
    label_name: Literal["normal", "controlled_anomaly"]
    description: Annotated[str, Field(min_length=1)]
    traffic_profile: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_label(self) -> Scenario:
        if self.label_name != ("normal" if self.label == 0 else "controlled_anomaly"):
            raise ValueError("label과 label_name이 일치하지 않습니다.")
        return self


class ScenarioManifest(StrictModel):
    manifest_version: Literal["v1"]
    label_source: Literal["independent_scenario_definition"]
    scenarios: Annotated[list[Scenario], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ScenarioManifest:
        ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario_id가 중복되었습니다.")
        return self


class CollectionConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    run_id: Identifier
    scenario_id: Identifier
    server_build_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    scenario_manifest: Path
    dataset_purpose: Literal["controlled_experiment", "integration_test"]
    output_dir: Path = AI_ROOT / "data" / "raw" / "operational"
    queue_capacity: Annotated[int, Field(ge=1, le=100000, strict=True)] = 1024
    flush_interval_s: Annotated[float, Field(gt=0, le=60)] = 0.5
    shutdown_timeout_s: Annotated[float, Field(gt=0, le=120)] = 10.0
    verbose_payload_logging: bool = False


def load_collection_config(path: Path) -> CollectionConfig:
    path = path.resolve()
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    # 상대 경로의 기준을 설정 파일 위치로 고정해 실행 위치에 따른 혼동을 막는다.
    for field in ("scenario_manifest", "output_dir"):
        if field in values:
            candidate = Path(values[field])
            values[field] = candidate if candidate.is_absolute() else path.parent / candidate
    return CollectionConfig.model_validate(values)


def config_from_environment() -> CollectionConfig | None:
    path = os.environ.get("NDA_COLLECTION_CONFIG")
    # 기존 실행은 수집하지 않는다. 명시적인 실행 정보 없이는 정답을 붙이지 않는다.
    return load_collection_config(Path(path)) if path else None


def load_manifest(path: Path, scenario_id: str) -> tuple[ScenarioManifest, Scenario, str]:
    content = path.read_bytes()
    manifest = ScenarioManifest.model_validate_json(content.decode("utf-8-sig"))
    for scenario in manifest.scenarios:
        if scenario.scenario_id == scenario_id:
            return manifest, scenario, hashlib.sha256(content).hexdigest()
    raise ValueError(f"manifest에 없는 scenario_id입니다: {scenario_id}")
