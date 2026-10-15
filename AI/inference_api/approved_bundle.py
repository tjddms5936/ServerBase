from __future__ import annotations

import hashlib
import io
import json
import math
import platform
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import joblib
import numpy as np
import sklearn
from pydantic import Field
from sklearn.ensemble import RandomForestClassifier
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.pipeline import Pipeline

from data_collection.config import StrictModel
from .feature_transformer import MESSAGE_FEATURE_ORDER
from .payload_validation import OPERATIONAL_CONTRACT


AI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = AI_ROOT / "configs" / "operational_rf_serving.json"
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
APPROVED = "approved_for_local_advisory_integration"


class ServingConfig(StrictModel):
    config_version: Literal["v1"]
    evaluation_root: Path
    candidate_lock_path: Path
    expected_evaluation_manifest_sha256: Sha256
    expected_deployment_manifest_sha256: Sha256
    model_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
    max_concurrent_predictions: Literal[1]
    verbose_payload_logging: bool = Field(strict=True)


def load_serving_config(path: Path = DEFAULT_CONFIG) -> ServingConfig:
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    for name in ("evaluation_root", "candidate_lock_path"):
        values[name] = (path.resolve().parent / values[name]).resolve()
    return ServingConfig.model_validate(values)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_verified(path: Path, expected: str, size: int | None = None) -> bytes:
    content = path.read_bytes()
    if sha256(content) != expected or (size is not None and len(content) != size):
        raise ValueError(f"승인 파일의 SHA-256 또는 크기가 다릅니다: {path.name}")
    return content


def inside(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("승인 묶음 밖의 경로를 사용할 수 없습니다.")
    return path


@dataclass(frozen=True)
class VerifiedBundle:
    pipeline: Pipeline
    manifest: dict
    evidence: dict[str, str]


def validate_schema(manifest: dict, raw: dict, model_input: dict, labels: dict, policy: dict) -> None:
    contract = OPERATIONAL_CONTRACT
    event = contract["events"]["message"]
    expected = {"feature_contract_id": event["feature_contract_id"], "raw_input_features": MESSAGE_FEATURE_ORDER,
                "wire_schema_version": contract["wire_schema_version"], "capture_layer": contract["capture_layer"],
                "observation_scope": event["observation_scope"], "event_type": "message",
                "feature_units": {name: contract["features"][name]["unit"] for name in MESSAGE_FEATURE_ORDER},
                "feature_dtypes": {name: contract["features"][name]["dtype"] for name in MESSAGE_FEATURE_ORDER}}
    if any(raw.get(name) != value for name, value in expected.items()):
        raise ValueError("원본 feature 의미·단위·타입·순서가 현재 API 계약과 다릅니다.")
    if (raw.get("dataset_id") != manifest["dataset_id"] or raw.get("missing_value_policy") != "reject_without_zero_fill" or
            model_input.get("raw_feature_contract_id") != event["feature_contract_id"] or
            model_input.get("raw_input_features") != MESSAGE_FEATURE_ORDER or model_input.get("model_input_features") != MESSAGE_FEATURE_ORDER or
            model_input.get("input_transform_id") != "raw7_v1" or model_input.get("preprocessing") != "identity_no_scaler" or
            model_input.get("requires_ordered_session_prefix") is not False):
        raise ValueError("선정 모델의 입력 변환은 원본 7개·무정규화·무추가이력이어야 합니다.")
    if labels != {"normal": 0, "controlled_anomaly": 1}:
        raise ValueError("승인 모델의 정답 의미가 다릅니다.")
    if (policy.get("candidate_id") != "rf_raw7" or policy.get("threshold") != 0.7 or policy.get("positive_class") != 1 or
            policy.get("comparison") != ">=" or policy.get("input_transform_id") != "raw7_v1" or
            policy.get("test_confirmed") is not True or policy.get("scope") != "local_advisory_integration_only" or
            policy.get("automatic_blocking_allowed") is not False):
        raise ValueError("승인된 0.7 판정 정책을 확인할 수 없습니다.")


def load_verified_bundle(config: ServingConfig) -> VerifiedBundle:
    root = config.evaluation_root.resolve()
    bundle = root / "deployment_bundle"
    complete_path, manifest_path = root / "evaluation_manifest.json", bundle / "deployment_manifest.json"
    complete_bytes = read_verified(complete_path, config.expected_evaluation_manifest_sha256)
    complete = json.loads(complete_bytes)
    if (complete.get("status") != "complete" or complete.get("approval_status") != APPROVED or
            complete.get("test_evaluated") is not True or complete.get("model_training_performed") is not False):
        raise ValueError("완료된 독립 평가의 로컬 연동 승인이 필요합니다.")
    # 평가·승인 파일만 읽는다. manifest가 가리키는 Train/Test CSV·예측 보고서·과거 source는 열지 않는다.
    evidence = {str(complete_path): sha256(complete_bytes)}
    contents = {}
    expected_artifacts = {"evaluation_protocol_lock.json", "test_verdict.json", *(
        f"deployment_bundle/{value}" for value in ("pipeline.joblib", "feature_schema.json", "model_input_schema.json",
                                                  "label_map.json", "decision_policy.json", "deployment_manifest.json"))}
    if set(complete["artifacts"]) != expected_artifacts:
        raise ValueError("완료 평가의 필수 산출물 8개 구성이 다릅니다.")
    for name, item in complete["artifacts"].items():
        path = inside(root, name)
        contents[name] = read_verified(path, item["sha256"], item["bytes"])
        evidence[str(path)] = item["sha256"]
    if sha256(contents["deployment_bundle/deployment_manifest.json"]) != config.expected_deployment_manifest_sha256:
        raise ValueError("설정에 고정한 승인 묶음 manifest 해시가 다릅니다.")
    manifest = json.loads(contents["deployment_bundle/deployment_manifest.json"])
    if (manifest.get("manifest_version") != "operational_rf_deployment_v1" or manifest.get("status") != APPROVED or
            manifest.get("local_integration_approved") is not True or manifest.get("production_ready") is not False or
            manifest.get("automatic_blocking_allowed") is not False or manifest.get("requires_complete_evaluation_manifest") is not True or
            Path(manifest["evaluation_manifest_path"]).resolve() != complete_path or
            Path(manifest["test_verdict_path"]).resolve() != root / "test_verdict.json" or
            manifest.get("kind") != "random_forest" or manifest.get("candidate_id") != "rf_raw7" or
            manifest.get("threshold") != 0.7 or manifest.get("positive_class") != 1 or manifest.get("comparison") != ">=" or
            manifest.get("feature_contract_id") != OPERATIONAL_CONTRACT["events"]["message"]["feature_contract_id"] or
            manifest.get("feature_order") != MESSAGE_FEATURE_ORDER or manifest.get("input_transform_id") != "raw7_v1" or
            manifest.get("requires_ordered_session_prefix") is not False or manifest.get("preprocessing") != "identity_no_scaler"):
        raise ValueError("로컬 참고용 RF·0.7의 승인 묶음 명세가 아닙니다.")
    verdict = json.loads(contents["test_verdict.json"])
    gate_bytes = read_verified(config.candidate_lock_path, manifest["candidate_lock_sha256"])
    gate = json.loads(gate_bytes)
    evidence[str(config.candidate_lock_path)] = sha256(gate_bytes)
    policy_signature = sha256(json.dumps({"descriptor": gate["descriptor"], "criteria": gate["criteria"], "test_rule": gate["test_rule"],
        "expected_test_sha256": gate["expected_test_sha256"]}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    if (gate.get("status") != "candidate_locked_before_test" or gate["policy_signature"] != policy_signature or
            complete["candidate_lock_sha256"] != manifest["candidate_lock_sha256"] or
            verdict.get("status") != APPROVED or verdict.get("reasons") != [] or
            sha256(contents["test_verdict.json"]) != manifest["test_verdict_sha256"] or
            Path(verdict["candidate_lock_path"]).resolve() != config.candidate_lock_path or
            verdict["candidate_lock_sha256"] != manifest["candidate_lock_sha256"] or
            any(value["policy_signature"] != policy_signature for value in (manifest, verdict, complete)) or
            verdict["criteria"] != gate["criteria"] or verdict["test_input_sha256"] != gate["expected_test_sha256"] or
            verdict["cost_summary"] != gate["cost_summary"] or manifest["model_id"] != gate["selected_model_id"] or
            manifest["dataset_id"] != gate["dataset_id"] or gate["descriptor"]["candidate_id"] != "rf_raw7" or
            gate["descriptor"]["threshold"] != 0.7 or gate["criteria"]["scope"] != "local_advisory_integration_only"):
        raise ValueError("평가 완료·Test 판정·후보 잠금·정책 서명 연결이 다릅니다.")
    criteria = gate["criteria"]
    for value, limit, upper in (
        (verdict["metrics"]["false_positive_rate"], criteria["max_false_positive_rate"], True),
        (verdict["by_scenario"]["normal_batched_chat"]["false_positive_rate"], criteria["max_normal_batched_false_positive_rate"], True),
        (verdict["metrics"]["recall"], criteria["min_recall"], False),
        (verdict["cost_summary"]["single_request_worst_process_p95_ms"], criteria["max_single_request_p95_ms"], True),
        (verdict["cost_summary"]["maximum_native_process_peak_rss_bytes"], criteria["max_process_peak_rss_mib"] * 1024**2, True),
    ):
        if value is None or not math.isfinite(value) or value < 0 or (value > limit if upper else value < limit):
            raise ValueError("저장된 승인 성능·비용이 잠근 기준을 충족하지 않습니다.")
    names = {"model": "pipeline.joblib", "feature_schema": "feature_schema.json", "model_input_schema": "model_input_schema.json",
             "label_map": "label_map.json", "decision_policy": "decision_policy.json"}
    if set(manifest["files"]) != set(names):
        raise ValueError("필수 승인 파일 5개 구성이 다릅니다.")
    documents = {}
    for role, name in names.items():
        item = manifest["files"][role]
        content = contents[f"deployment_bundle/{name}"]
        if item["path"] != name or sha256(content) != item["sha256"] or len(content) != item["bytes"]:
            raise ValueError("승인 묶음 내부 파일 명세가 다릅니다.")
        if role != "decision_policy" and item["sha256"] != gate["descriptor"]["files"][role]["sha256"]:
            raise ValueError("복사 모델·schema·정답 map이 원래 잠근 후보와 다릅니다.")
        if role != "model":
            documents[role] = json.loads(content)
    size = sum(item["bytes"] for item in manifest["files"].values())
    if size != manifest["required_files_bytes"] or size > criteria["max_deployment_artifacts_mib"] * 1024**2:
        raise ValueError("필수 승인 파일 크기가 다르거나 예산을 넘습니다.")
    validate_schema(manifest, documents["feature_schema"], documents["model_input_schema"], documents["label_map"], documents["decision_policy"])
    versions = manifest["environment"]
    if (versions["scikit_learn"] != sklearn.__version__ or versions["numpy"] != np.__version__ or
            versions["python"].split(".")[:2] != platform.python_version().split(".")[:2]):
        raise ValueError("승인 당시 Python 주·부 버전 또는 numpy/scikit-learn 버전이 다릅니다.")
    # pickle 기반 파일은 신뢰하는 로컬 산출물만 사용한다. 검사한 같은 바이트를 로딩해 파일 재읽기 경합도 피한다.
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        pipeline = joblib.load(io.BytesIO(contents["deployment_bundle/pipeline.joblib"]))
    if not isinstance(pipeline, Pipeline) or list(pipeline.named_steps) != ["classifier"]:
        raise ValueError("승인 모델은 scaler 없는 RF Pipeline이어야 합니다.")
    model = pipeline.named_steps["classifier"]
    if (not isinstance(model, RandomForestClassifier) or model.n_jobs != 1 or
            list(model.classes_) != [0, 1] or list(model.feature_names_in_) != MESSAGE_FEATURE_ORDER or
            model.n_estimators != 100 or model.min_samples_leaf != 5 or model.random_state != 42):
        raise ValueError("승인 RF의 클래스·입력·학습 설정이 다릅니다.")
    return VerifiedBundle(pipeline, manifest, evidence)
