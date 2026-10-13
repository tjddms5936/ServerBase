from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from data_collection.augmentation import verify_hashes
from data_collection.config import StrictModel
from data_collection.quality import read_json
from data_preparation.operational_dataset import file_hash
from evaluation.audit_frozen_operational_baseline import load_frozen_model, verify_campaign_assignment
from evaluation.compare_operational_augmentation import compare, render_report
from training.operational_data import METADATA_COLUMNS, load_operational_data
from training.run_operational_validation_experiment import ExperimentConfig, run_experiment
from training.train_operational_logistic_regression import calculate_metrics, write_json


class AugmentationConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    run_dir: Path
    reference_experiment_dir: Path
    reference_audit_report: Path
    comparison_report_prefix: Path
    expected_train_rows: Annotated[int, Field(ge=1, strict=True)] = 1538
    expected_validation_rows: Annotated[int, Field(ge=1, strict=True)] = 513
    validation_prior_use: Literal["frozen_baseline_audit_completed"] = "frozen_baseline_audit_completed"
    experiment: ExperimentConfig

    @model_validator(mode="after")
    def validate_comparison_controls(self) -> AugmentationConfig:
        candidates = {candidate.id: candidate for candidate in self.experiment.candidates}
        lr, rf = candidates.get("lr_c_1"), candidates.get("rf_leaf_1")
        if (lr is None or lr.kind != "logistic_regression" or lr.C != 1.0 or
                rf is None or rf.kind != "random_forest" or rf.min_samples_leaf != 1 or rf.max_depth is not None or
                0.7 not in self.experiment.thresholds):
            raise ValueError("보강 비교에는 LR C=1·RF leaf=1과 고정 비교 threshold 0.7이 필요합니다.")
        return self


def load_config(path: Path) -> AugmentationConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("run_dir", "reference_experiment_dir", "reference_audit_report", "comparison_report_prefix"):
        values[name] = (path.parent / Path(values[name])).resolve()
    for name in ("dataset_dir", "artifact_dir", "report_prefix"):
        values["experiment"][name] = (path.parent / Path(values["experiment"][name])).resolve()
    return AugmentationConfig.model_validate(values)


def load_reference(config: AugmentationConfig, data) -> tuple[np.ndarray, dict, dict]:
    pipeline, _, evidence = load_frozen_model(config.reference_experiment_dir, data)
    coverage, registry_evidence = verify_campaign_assignment(config.experiment.dataset_dir, data, evidence["frozen_reference"])
    report = read_json(config.reference_audit_report)
    if (report.get("status") != "complete" or report["dataset_id"] != data.dataset_id or
            report["input_sha256"] != data.input_sha256 or report["threshold"] != 0.7 or
            report["test_files_read"] is not False or report["model_refit_performed"] is not False or
            report["model_evidence"]["frozen_reference"] != evidence["frozen_reference"]):
        raise ValueError("기존 고정 모델 진단과 보강 실험의 Validation·정책·모델 근거가 다릅니다.")
    csv_path = Path(report["csv_outputs"]["predictions"]["path"])
    csv_bytes = csv_path.read_bytes()
    if hashlib.sha256(csv_bytes).hexdigest() != report["csv_outputs"]["predictions"]["sha256"]:
        raise ValueError("기존 Validation 예측 CSV의 SHA-256이 다릅니다.")
    saved = pd.read_csv(io.BytesIO(csv_bytes), dtype={name: str for name in METADATA_COLUMNS}, float_precision="round_trip")
    probability = pipeline.predict_proba(data.validation.X)[:, 1]
    if (list(saved.row_id) != list(data.validation.metadata.row_id) or not np.array_equal(saved.label.to_numpy(), data.validation.y) or
            not np.array_equal(saved.anomaly_probability.to_numpy(), probability) or
            not np.array_equal(saved.predicted_label.to_numpy(), (probability >= 0.7).astype("int64")) or
            not np.all(saved.threshold == 0.7) or calculate_metrics(data.validation.y, probability, 0.7) != report["validation_metrics"]):
        raise ValueError("기존 모델 재예측과 저장 진단의 행·정답·확률·지표가 다릅니다.")
    protected = {**evidence["file_sha256"], **registry_evidence,
                 str(config.reference_audit_report): file_hash(config.reference_audit_report),
                 str(csv_path): hashlib.sha256(csv_bytes).hexdigest()}
    return probability, {"model": evidence, "coverage": coverage, "audit_report": str(config.reference_audit_report),
                         "metrics": report["validation_metrics"]}, protected


def run(config: AugmentationConfig, config_path: Path | None = None) -> dict:
    config = AugmentationConfig.model_validate(config.model_dump())
    core = config.experiment
    outputs = [prefix.with_suffix(extension) for prefix in (core.report_prefix, config.comparison_report_prefix)
               for extension in (".json", ".md")]
    if config.run_dir.exists() or core.artifact_dir.exists() or any(path.exists() for path in outputs):
        raise FileExistsError("기존 보강 실험을 덮어쓰지 않습니다. 새 run-dir과 두 보고서 경로를 지정하세요.")
    if len({path.resolve() for path in outputs}) != len(outputs):
        raise ValueError("선정 보고서와 비교 보고서는 서로 다른 경로여야 합니다.")
    if core.artifact_dir.resolve() == config.run_dir.resolve() or not core.artifact_dir.resolve().is_relative_to(config.run_dir.resolve()):
        raise ValueError("후보 선정 산출물은 새로운 보강 run-dir의 하위 폴더여야 합니다.")
    if any(path.resolve().is_relative_to(root.resolve()) for path in [config.run_dir, core.artifact_dir, *outputs]
           for root in (core.dataset_dir, config.reference_experiment_dir)):
        raise ValueError("고정 데이터셋·기존 모델 내부에 보강 산출물을 저장하지 않습니다.")
    data = load_operational_data(core.dataset_dir, core.dataset_id)
    if len(data.train.y) != config.expected_train_rows or len(data.validation.y) != config.expected_validation_rows:
        raise ValueError("보강 Train/Validation 행 수가 사전 지정한 값과 다릅니다.")
    reference_probability, reference, protected = load_reference(config, data)
    protected.update({str(core.dataset_dir / name): digest for name, digest in data.input_sha256.items()})
    sources = [Path(__file__), AI_ROOT / "evaluation" / "compare_operational_augmentation.py",
               AI_ROOT / "evaluation" / "audit_frozen_operational_baseline.py"]
    sources.extend(AI_ROOT / "training" / name for name in ("run_operational_validation_experiment.py",
                   "train_operational_logistic_regression.py", "train_operational_random_forest.py", "operational_data.py"))
    protected.update({str(path): file_hash(path) for path in sources})
    if config_path is not None:
        protected[str(config_path.resolve())] = file_hash(config_path)
    config.run_dir.mkdir(parents=True, exist_ok=False)
    lock_path = config.run_dir / "augmentation_protocol_lock.json"
    snapshot = config.model_dump(mode="json")
    protocol_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    # 후보를 fit하기 전에 비교 기준 모델·입력·규칙과 이미 관측한 Validation 이력을 함께 고정한다.
    write_json(lock_path, {"status": "locked_before_fit", "locked_at_utc": datetime.now(UTC).isoformat(),
                           "protocol_sha256": protocol_hash, "protocol": snapshot, "input_sha256": data.input_sha256,
                           "reference_model": reference, "validation_prior_use": config.validation_prior_use,
                           "protected_file_sha256": protected})
    protected[str(lock_path)] = file_hash(lock_path)
    experiment = run_experiment(core)
    verify_hashes(protected)
    if load_operational_data(core.dataset_dir, core.dataset_id).input_sha256 != data.input_sha256:
        raise ValueError("보강 학습 도중 데이터 입력이 변경되었습니다.")
    comparison, predictions = compare(data, core, experiment, reference_probability, reference["model"]["frozen_reference"]["model_sha256"])
    verify_hashes(protected)
    csv_path = config.run_dir / "comparison_predictions.csv"
    predictions.to_csv(csv_path, index=False, encoding="utf-8", mode="x")
    report = {
        "report_version": "v1", "status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment": "operational_augmentation_train_only_comparison", "run_dir": str(config.run_dir),
        "dataset_id": data.dataset_id, "feature_contract_id": data.schema["feature_contract_id"],
        "feature_order": data.schema["raw_input_features"], "train_rows": len(data.train.y), "validation_rows": len(data.validation.y),
        "candidate_count": len(core.candidates), "trial_count": len(experiment["trials"]),
        "protocol": snapshot, "protocol_sha256": protocol_hash, "selection": experiment["selection"],
        "selection_report": str(core.report_prefix.with_suffix(".json")), "input_sha256": data.input_sha256,
        "fit_scope": "new_train_only", "validation_prior_use": config.validation_prior_use,
        "refit_with_validation": False, "test_files_read": False, "test_evaluated": False,
        "final_model_selected": False, "deployment_ready": False, "reference_model_unchanged": True,
        "reference": reference, "comparison": comparison,
        "comparison_csv": {"path": str(csv_path), "rows": len(predictions), "sha256": file_hash(csv_path)},
        "limitations": [
            "이미 진단한 Validation으로 후보·threshold를 선정한 개발 실험이다. 독립적인 최종 성능 추정치가 아니다.",
            "고정 LR C=1·0.7 재학습 비교와 후보/threshold 선정 비교를 구분한다. 데이터 수·조건·학습 분포도 함께 바뀌므로 하나의 원인을 확정하지 않는다.",
            "Train 3개·Validation 1개 캠페인은 같은 생성기·장비·프로토콜이다. 실제 외부 침입·다른 장비에 대한 일반화를 보장하지 않는다.",
            "같은 세션의 메시지는 독립 표본이 아니다. 시나리오별 FP/FN과 burst prefix를 전체 점수와 함께 보고한다.",
            "본문 필드가 입력에 없는 관측 한계는 남는다. malformed를 잡거나 놓친 결과만으로 본문 유효성 검증을 배웠다고 주장하지 않는다.",
            "헤더 거부는 메시지 관측 범위 밖이다. 기존 프로토콜 거부 경로를 AI 탐지 건수로 합치지 않는다.",
            "성능이 좋아도 학습 데이터 생성기의 크기·타이밍 규칙에 적응한 결과일 수 있다. Test·LSTM·지연시간/메모리·실제 모델 배포는 아직 하지 않았다.",
        ],
    }
    config.comparison_report_prefix.parent.mkdir(parents=True, exist_ok=True)
    with config.comparison_report_prefix.with_suffix(".md").open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    verify_hashes(protected)
    write_json(config.comparison_report_prefix.with_suffix(".json"), report)
    inventory = {path.relative_to(config.run_dir).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size}
                 for path in sorted(config.run_dir.rglob("*")) if path.is_file()}
    # 후보 실험 완료와 전체 보강·비교 완료는 구분한다. 최종 비교와 보존 검사 후에만 표시한다.
    write_json(config.run_dir / "augmentation_manifest.json", {
        "status": "complete", "protocol_sha256": protocol_hash, "selection_status": experiment["selection"]["status"],
        "input_sha256": data.input_sha256, "artifacts": inventory,
        "reports": {str(path): file_hash(path) for path in [*outputs]},
        "test_evaluated": False, "deployment_ready": False,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="새 Train 전용 보강 LR/RF 사전 규칙·학습·동일 Validation 비교. Test는 읽지 않습니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_augmentation_experiment.json")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--selection-report-prefix", type=Path)
    parser.add_argument("--comparison-report-prefix", type=Path)
    args = parser.parse_args()
    try:
        values = load_config(args.config).model_dump()
        if args.run_dir is not None:
            values["run_dir"] = args.run_dir.resolve()
            values["experiment"]["artifact_dir"] = args.run_dir.resolve() / "validation_selection"
        if args.selection_report_prefix is not None:
            values["experiment"]["report_prefix"] = args.selection_report_prefix.resolve()
        if args.comparison_report_prefix is not None:
            values["comparison_report_prefix"] = args.comparison_report_prefix.resolve()
        report = run(AugmentationConfig.model_validate(values), args.config)
    except (OSError, ValueError, KeyError) as error:
        print(f"[보강 실험] 실패: {error}", file=sys.stderr)
        return 2
    trial = report["selection"]["selected_trial"]
    print(f"[보강 실험] Train {report['train_rows']}건 / Validation {report['validation_rows']}건 / Test 미사용")
    print(f"[보강 실험] 선정: {trial['candidate_id']} / threshold={trial['threshold']}" if trial else "[보강 실험] 제약 충족 후보 없음")
    print(f"[보강 실험] 비교 보고서: {args.comparison_report_prefix or values['comparison_report_prefix']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
