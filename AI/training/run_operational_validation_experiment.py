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

import joblib
import numpy as np
from pydantic import Field, model_validator

from data_collection.config import Identifier, StrictModel
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from training.operational_data import OperationalData, load_operational_data
import training.train_operational_logistic_regression as lr_training
import training.train_operational_random_forest as rf_training


CandidateId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
PositiveFloat = Annotated[float, Field(gt=0, strict=True)]
PositiveInt = Annotated[int, Field(ge=1, strict=True)]
Threshold = Annotated[float, Field(gt=0, lt=1, strict=True)]
Rate = Annotated[float, Field(ge=0, le=1, strict=True)]
TIE_BREAKERS = ["f1_desc", "false_positive_rate_asc", "candidate_id_asc", "threshold_asc"]


class LogisticCandidate(StrictModel):
    id: CandidateId
    kind: Literal["logistic_regression"]
    C: PositiveFloat


class ForestCandidate(StrictModel):
    id: CandidateId
    kind: Literal["random_forest"]
    min_samples_leaf: PositiveInt
    max_depth: PositiveInt | None = None


Candidate = Annotated[LogisticCandidate | ForestCandidate, Field(discriminator="kind")]


class SelectionRule(StrictModel):
    max_false_positive_rate: Rate = 0.1
    min_recall: Rate = 0.5
    primary_metric: Literal["recall"] = "recall"
    tie_breakers: list[str] = Field(default_factory=lambda: list(TIE_BREAKERS))

    @model_validator(mode="after")
    def validate_tie_breakers(self) -> SelectionRule:
        if self.tie_breakers != TIE_BREAKERS:
            raise ValueError("동률 순서는 F1 최대·FPR 최소·후보 id·threshold 오름차순이어야 합니다.")
        return self


class CommonParameters(StrictModel):
    random_state: Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)] = 42
    lr_max_iter: PositiveInt = 1000
    lr_tol: PositiveFloat = 0.0001
    rf_n_estimators: Annotated[int, Field(ge=1, le=1000, strict=True)] = 100


class ExperimentConfig(StrictModel):
    config_version: Literal["v1"] = "v1"
    experiment_id: Identifier
    dataset_id: Identifier
    dataset_dir: Path
    artifact_dir: Path
    report_prefix: Path
    thresholds: Annotated[list[Threshold], Field(min_length=1, max_length=15)]
    selection_rule: SelectionRule = Field(default_factory=SelectionRule)
    common_parameters: CommonParameters = Field(default_factory=CommonParameters)
    candidates: Annotated[list[Candidate], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def validate_candidate_space(self) -> ExperimentConfig:
        ids = [candidate.id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("후보 id가 중복되었습니다.")
        if self.thresholds != sorted(set(self.thresholds)) or 0.5 not in self.thresholds:
            raise ValueError("threshold는 중복 없이 오름차순이며 기본 0.5를 포함해야 합니다.")
        specifications = [json.dumps(candidate.model_dump(exclude={"id"}), sort_keys=True) for candidate in self.candidates]
        if len(specifications) != len(set(specifications)):
            raise ValueError("동일 설정 후보를 다른 id로 중복 등록할 수 없습니다.")
        return self


def load_config(path: Path) -> ExperimentConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("dataset_dir", "artifact_dir", "report_prefix"):
        values[name] = (path.parent / Path(values[name])).resolve()
    return ExperimentConfig.model_validate(values)


def eligibility_reasons(metrics: dict, training_acceptable: bool, rule: SelectionRule) -> list[str]:
    reasons = []
    if any(not np.isfinite(metrics[name]) or not 0 <= metrics[name] <= 1 for name in ("false_positive_rate", "recall", "f1")):
        return ["선정 지표는 0~1 사이의 유한한 값이어야 합니다."]
    if not training_acceptable:
        reasons.append("LR 학습 수렴을 확인하지 못했습니다.")
    if metrics["false_positive_rate"] > rule.max_false_positive_rate:
        reasons.append("정상 오탐률 상한을 초과했습니다.")
    if metrics["recall"] < rule.min_recall:
        reasons.append("이상 Recall 하한을 충족하지 못했습니다.")
    return reasons


def choose_trial(trials: list[dict], rule: SelectionRule) -> dict | None:
    # 저장된 eligible 표시를 그대로 믿지 않고 실제 지표와 수렴 상태로 제약을 다시 계산한다.
    feasible = [trial for trial in trials if not eligibility_reasons(trial["metrics"], trial["training_acceptable"], rule)]
    if not feasible:
        return None
    return min(feasible, key=lambda trial: (
        -trial["metrics"]["recall"], -trial["metrics"]["f1"], trial["metrics"]["false_positive_rate"],
        trial["candidate_id"], trial["threshold"],
    ))


def fit_candidate(candidate: Candidate, config: ExperimentConfig, data: OperationalData) -> tuple[dict, np.ndarray]:
    common = config.common_parameters
    paths = {"dataset_id": config.dataset_id, "dataset_dir": config.dataset_dir,
             "artifact_dir": config.artifact_dir / "candidates" / candidate.id,
             "report_prefix": config.artifact_dir / "candidate_reports" / candidate.id,
             "random_state": common.random_state}
    if candidate.kind == "logistic_regression":
        candidate_config = lr_training.TrainingConfig(**paths, C=candidate.C, max_iter=common.lr_max_iter, tol=common.lr_tol)
        result = lr_training.train_model(candidate_config)
    else:
        candidate_config = rf_training.TrainingConfig(**paths, min_samples_leaf=candidate.min_samples_leaf,
                                                     max_depth=candidate.max_depth, n_estimators=common.rf_n_estimators)
        result = rf_training.train_model(candidate_config)
    if result["input_sha256"] != data.input_sha256:
        raise ValueError("후보 학습 사이에 고정 데이터셋 입력이 변경되었습니다.")
    content = (candidate_config.artifact_dir / "pipeline.joblib").read_bytes()
    if hashlib.sha256(content).hexdigest() != result["artifacts"]["pipeline.joblib"]["sha256"]:
        raise ValueError("후보 모델 산출물 해시가 일치하지 않습니다.")
    pipeline = joblib.load(io.BytesIO(content))
    probability = pipeline.predict_proba(data.validation.X)[:, 1]
    if lr_training.calculate_metrics(data.validation.y, probability, 0.5) != result["validation_metrics"]:
        raise ValueError("후보의 기본 threshold 재예측 지표가 학습 기록과 다릅니다.")
    return result, probability


def render_report(report: dict) -> str:
    protocol = report["protocol"]
    rule = protocol["selection_rule"]
    selected = report["selection"]["selected_trial"]
    lines = ["# 운영 Validation 설정·threshold 선정", "", "## 학습 전에 고정한 실험 규칙", "",
             f"- 실험: `{report['experiment_id']}` / 데이터셋: `{report['dataset_id']}`",
             f"- 후보 {len(report['candidates'])}개 × threshold {len(protocol['thresholds'])}개 = {len(report['trials'])}개 조합",
             f"- threshold: `{protocol['thresholds']}`",
             f"- 제약: 정상 FPR <= {rule['max_false_positive_rate']:.0%}, 이상 Recall >= {rule['min_recall']:.0%}",
             "- 목표: 제약 충족 중 Recall 최대; 동률은 F1 최대·FPR 최소·후보 id·threshold 오름차순",
             "- 이 상한·하한은 초기 실험 기준이며 실제 운영 성능 보장이 아니다.",
             "- 기준을 충족한 후보가 없으면 미선정으로 기록한다. 실험 후 기준을 완화하지 않는다.",
             f"- 학습 전 protocol SHA-256: `{report['protocol_sha256']}`", "",
             "## 데이터와 학습 범위", "",
             f"Train {report['dataset_splits']['train']['rows']}건, Validation {report['dataset_splits']['validation']['rows']}건을 고정했다.",
             "각 후보를 Train으로만 한 번 학습한다. LR scaler는 Train에서만 fit하고 RF는 원본 입력을 사용한다.",
             "모델 하나의 Validation 확률에 여러 threshold를 적용하며 threshold마다 재학습하지 않는다. Test CSV는 해시 검사에도 열지 않는다.", "",
             "## 후보별 기본 0.5와 제약 충족 결과", "",
             "| 후보 | 설정 | 학습 가능 | 0.5 FPR | 0.5 Recall | 후보 내 선정 threshold | 선정 F1 |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for candidate in report["candidates"]:
        best = candidate["best_feasible_trial"]
        specification = candidate["specification"]
        setting = f"C={specification['C']}" if candidate["kind"] == "logistic_regression" else f"leaf={specification['min_samples_leaf']}, depth={specification['max_depth']}"
        default = candidate["default_validation_metrics"]
        threshold = str(best["threshold"]) if best else "없음"
        f1 = f"{best['metrics']['f1']:.6f}" if best else "해당 없음"
        lines.append(f"| {candidate['id']} | {setting} | {candidate['training_acceptable']} | {default['false_positive_rate']:.6f} | {default['recall']:.6f} | {threshold} | {f1} |")
    lines += ["", "## 모든 조합 결과", "",
              "| 후보 | threshold | FP | FN | FPR | Recall | F1 | 제약 충족 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for trial in report["trials"]:
        metrics = trial["metrics"]
        matrix = metrics["confusion_matrix"]
        lines.append(f"| {trial['candidate_id']} | {trial['threshold']} | {matrix['fp']} | {matrix['fn']} | {metrics['false_positive_rate']:.6f} | {metrics['recall']:.6f} | {metrics['f1']:.6f} | {'충족' if trial['eligible'] else '미충족'} |")
    lines += ["", "## 선정 결과", ""]
    if selected is None:
        lines += ["기준을 충족한 후보가 없어 모델·threshold를 선정하지 않았다. 무조건 정상으로 판정하는 조합도 Recall 하한을 만족하지 못하면 제외한다."]
    else:
        metrics = selected["metrics"]
        matrix = metrics["confusion_matrix"]
        lines += [f"선정 후보: `{selected['candidate_id']}`, threshold: `{selected['threshold']}`.", "",
                  f"Validation FPR {metrics['false_positive_rate']:.2%}, Recall {metrics['recall']:.2%}, F1 {metrics['f1']:.2%}; FP={matrix['fp']}, FN={matrix['fn']}.", "",
                  "이는 현재 후보 집합·실험 기준에 따른 baseline 선정이며 최종 모델·실제 침입 탐지 성능 검증이 아니다.", "",
                  "### 선정 조합의 시나리오별 결과", "",
                  "| 시나리오 | 메시지 | FP | FN | 이상 Recall | 정상 FPR |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for name, values in selected["validation_by_scenario"].items():
            recall = "해당 없음" if values["recall"] is None else f"{values['recall']:.6f}"
            fpr = "해당 없음" if values["false_positive_rate"] is None else f"{values['false_positive_rate']:.6f}"
            lines.append(f"| {name} | {values['rows']} | {values['fp']} | {values['fn']} | {recall} | {fpr} |")
    lines += ["", "## 저장과 해석", "",
              "selection.json에는 선정 여부와 모델·schema·정답 경로·선정 threshold를 함께 기록한다. 후보별 decision_policy.json의 0.5는 기본 비교용이고, 선정 판단에는 selection.json의 threshold를 사용해야 한다.",
              "Validation을 Train에 합쳐 재학습하지 않았고 FastAPI는 계속 dummy 상태다.", "",
              "## 한계", ""] + [f"- {note}" for note in report["limitations"]]
    lines += ["", "## 다음 작업", "",
              "선정 또는 미선정 근거를 바탕으로 오분류·관측 한계를 분석하고 독립 캠페인 데이터 보강 계획을 세운다. Test는 최종 비교까지 보류한다.", ""]
    return "\n".join(lines)


def run_experiment(config: ExperimentConfig) -> dict:
    # 호출자가 전달한 가변 목록과 분리하고, 실제 실행할 설정을 먼저 확정한다.
    config = ExperimentConfig.model_validate(config.model_dump())
    report_json, report_md = config.report_prefix.with_suffix(".json"), config.report_prefix.with_suffix(".md")
    if config.artifact_dir.exists() or report_json.exists() or report_md.exists():
        raise FileExistsError("기존 선정 실험은 덮어쓰지 않습니다. 새 artifact-dir과 report-prefix를 지정하세요.")
    if any(path.resolve().is_relative_to(config.dataset_dir.resolve()) for path in (config.artifact_dir, report_json, report_md)):
        raise ValueError("고정 데이터셋 내부에는 실험 산출물을 저장할 수 없습니다.")
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    snapshot = config.model_dump(mode="json")
    protocol_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    lock_path = config.artifact_dir / "protocol_lock.json"
    lr_training.write_json(lock_path, {
        "status": "locked_before_fit", "locked_at_utc": datetime.now(UTC).isoformat(),
        "protocol_sha256": protocol_hash, "protocol": snapshot, "input_sha256": data.input_sha256,
    })
    lock_hash = file_hash(lock_path)
    trials, candidates, probabilities = [], [], {}
    for candidate in config.candidates:
        result, probability = fit_candidate(candidate, config, data)
        training_acceptable = candidate.kind == "random_forest" or result["training"]["convergence"]["converged"]
        probabilities[candidate.id] = probability
        candidate_trials = []
        for threshold in config.thresholds:
            metrics = lr_training.calculate_metrics(data.validation.y, probability, threshold)
            reasons = eligibility_reasons(metrics, training_acceptable, config.selection_rule)
            trial = {
                "candidate_id": candidate.id, "kind": candidate.kind, "threshold": threshold,
                "training_acceptable": training_acceptable, "eligible": not reasons, "rejection_reasons": reasons,
                "metrics": metrics,
                "validation_by_scenario": lr_training.scenario_metrics(data.validation, (probability >= threshold).astype("int64")),
            }
            candidate_trials.append(trial)
        trials.extend(candidate_trials)
        best = choose_trial(candidate_trials, config.selection_rule)
        candidates.append({
            "id": candidate.id, "kind": candidate.kind, "specification": candidate.model_dump(mode="json"),
            "training_acceptable": training_acceptable, "training": result["training"],
            "train_metrics": result["train_metrics"], "default_validation_metrics": result["validation_metrics"],
            "best_feasible_trial": best,
            "pipeline_path": f"candidates/{candidate.id}/pipeline.joblib",
            "pipeline_sha256": result["artifacts"]["pipeline.joblib"]["sha256"],
            "candidate_report_path": f"candidate_reports/{candidate.id}.json",
        })
        print(f"[운영 선정] {candidate.id}: threshold {len(candidate_trials)}개 / 제약 충족 {sum(trial['eligible'] for trial in candidate_trials)}개", flush=True)
    # 실행 중 입력이나 잠금 파일이 바뀌면 하나의 고정 실험으로 완료했다고 기록하지 않는다.
    if load_operational_data(config.dataset_dir, config.dataset_id).input_sha256 != data.input_sha256 or file_hash(lock_path) != lock_hash:
        raise ValueError("실험 중 고정 입력 또는 protocol 잠금 파일이 변경되었습니다.")
    selected = choose_trial(trials, config.selection_rule)
    selection = {
        "status": "selected" if selected else "no_feasible_candidate", "selection_scope": "validation_baseline_only",
        "selected_trial": selected, "rule": config.selection_rule.model_dump(),
        "eligible_trial_count": sum(trial["eligible"] for trial in trials),
        "reason": "사전 제약과 동률 순서에 따라 선정했습니다." if selected else "사전 제약을 충족한 조합이 없습니다. 기준은 완화하지 않았습니다.",
        "protocol_sha256": protocol_hash, "feature_contract_id": data.schema["feature_contract_id"],
        "feature_order": FEATURE_NAMES, "label_map": data.label_map, "positive_class": 1, "comparison": ">=",
        "refit_with_validation": False, "test_evaluated": False, "final_model_selected": False, "deployment_ready": False,
        "model_path": None, "model_sha256": None, "feature_schema_path": None, "label_map_path": None, "threshold": None,
    }
    if selected:
        candidate_id = selected["candidate_id"]
        model_dir = config.artifact_dir / "candidates" / candidate_id
        selection.update(model_path=f"candidates/{candidate_id}/pipeline.joblib", model_sha256=file_hash(model_dir / "pipeline.joblib"),
                         feature_schema_path=f"candidates/{candidate_id}/feature_schema.json", label_map_path=f"candidates/{candidate_id}/label_map.json",
                         threshold=selected["threshold"])
        predictions = data.validation.metadata.copy()
        probability = probabilities[candidate_id]
        predicted = (probability >= selected["threshold"]).astype("int64")
        predictions["label"] = data.validation.y
        predictions["anomaly_probability"] = probability
        predictions["predicted_label"] = predicted
        predictions["error_type"] = np.where(predicted == data.validation.y, "correct",
                                             np.where(predicted == 1, "false_positive", "false_negative"))
        predictions.to_csv(config.artifact_dir / "selected_validation_predictions.csv", index=False, encoding="utf-8")
    lr_training.write_json(config.artifact_dir / "selection.json", selection)
    outputs = {path.relative_to(config.artifact_dir).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size}
               for path in sorted(config.artifact_dir.rglob("*")) if path.is_file()}
    report = {
        "report_version": "v1", "generated_at_utc": datetime.now(UTC).isoformat(), "experiment_id": config.experiment_id,
        "dataset_id": config.dataset_id, "feature_contract_id": data.schema["feature_contract_id"], "feature_order": FEATURE_NAMES,
        "protocol": snapshot, "protocol_sha256": protocol_hash, "input_sha256": data.input_sha256,
        "fit_scope": "train_only", "selection_split": "validation", "test_files_read": False, "test_evaluated": False,
        "refit_with_validation": False, "final_model_selected": False, "deployment_ready": False,
        "dataset_splits": result["dataset_splits"], "candidates": candidates, "trials": trials, "selection": selection,
        "artifacts": outputs,
        "code_sha256": {name: file_hash(AI_ROOT / "training" / name) for name in
                        ("operational_data.py", "train_operational_logistic_regression.py", "train_operational_random_forest.py", "run_operational_validation_experiment.py")},
        "limitations": [
            f"Train 캠페인 {data.train.metadata['campaign_id'].nunique()}개, Validation 캠페인 {data.validation.metadata['campaign_id'].nunique()}개다. 메시지별 점수는 외부 독립 캠페인 일반화 성능이 아니다.",
            "Validation은 후보·threshold 선택에 사용했다. 선정된 Validation 지표는 독립적인 최종 성능 추정치가 아니다.",
            "현재 수집 데이터와 의미 계약에서만 비교한다. 본문 오류의 관측 불가와 생성기 편향을 모델 설정으로 해결했다고 주장하지 않는다.",
            "FPR 상한과 Recall 하한은 초기 실험 기준이며 배포 요구사항이나 실제 공격 탐지 보장이 아니다.",
            "threshold는 클래스 확률의 판정 기준이다. 변경한다고 가중치·트리·확률 자체가 바뀌거나 확률 보정이 되는 것은 아니다.",
            "후보 실패 또는 미선정 때문에 기준·후보를 자동 변경하지 않는다. 추가 실험은 별도 protocol과 새로운 실험 경로로 관리한다.",
            "최종 Test 비교·지연시간/메모리 측정·실제 모델 FastAPI 연결은 수행하지 않았다.",
        ],
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    lr_training.write_json(report_json, report)
    with report_md.open("x", encoding="utf-8") as stream:
        stream.write(render_report(report))
    # 미선정도 실험 완료 결과다. 완료 상태와 배포 가능한 모델 존재 여부를 구분한다.
    lr_training.write_json(config.artifact_dir / "experiment_manifest.json", {
        "status": "complete", "selection_status": selection["status"], "experiment_id": config.experiment_id,
        "protocol_sha256": protocol_hash, "input_sha256": data.input_sha256, "artifacts": outputs,
        "reports": {str(path): file_hash(path) for path in (report_json, report_md)}, "deployment_ready": False,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 Train 전용 후보 학습과 Validation 설정·threshold 선정. Test는 읽지 않습니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_validation_experiment.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    try:
        values = load_config(args.config).model_dump()
        for name in ("artifact_dir", "report_prefix"):
            override = getattr(args, name)
            if override is not None:
                values[name] = override.resolve()
        report = run_experiment(ExperimentConfig.model_validate(values))
    except (OSError, ValueError, KeyError) as error:
        print(f"[운영 선정] 실패: {error}", file=sys.stderr)
        return 2
    selected = report["selection"]["selected_trial"]
    if selected:
        metrics = selected["metrics"]
        print(f"[운영 선정] {selected['candidate_id']} / threshold={selected['threshold']} / FPR={metrics['false_positive_rate']:.6f}, Recall={metrics['recall']:.6f}, F1={metrics['f1']:.6f}")
    else:
        print("[운영 선정] 실험은 완료했지만 제약 충족 후보가 없어 미선정입니다.")
    print(f"[운영 선정] 후보 {len(report['candidates'])}개 / 조합 {len(report['trials'])}개 / Test 미사용 / 최종 모델·배포 미확정")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
