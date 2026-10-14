from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd

from data_collection.augmentation import verify_hashes
from data_collection.config import Identifier, StrictModel
from data_collection.quality import read_json
from data_collection.recovery_design import compile_recovery_plans, summarize_recovery_design
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from data_preparation.operational_prefix_features import prefix_observations, transform_prefix_features
from evaluation.compare_operational_baselines import load_verified_model
from training.operational_data import OperationalSplit, load_operational_data
from training.train_operational_logistic_regression import calculate_metrics, scenario_metrics, write_json


class DiagnosisConfig(StrictModel):
    config_version: Literal["v1"]
    dataset_id: Identifier
    dataset_dir: Path
    model_dir: Path
    comparison_report: Path
    final_report: Path
    recovery_plan: Path
    previous_plan: Path
    artifact_dir: Path
    report_prefix: Path
    candidate_id: Literal["rf_leaf_5"]
    threshold: Literal[0.3]
    short_parse_interval_ms: Literal[1.0]


def load_config(path: Path) -> DiagnosisConfig:
    path = path.resolve()
    values = read_json(path)
    for name in ("dataset_dir", "model_dir", "comparison_report", "final_report", "recovery_plan",
                 "previous_plan", "artifact_dir", "report_prefix"):
        values[name] = (path.parent / values[name]).resolve()
    return DiagnosisConfig.model_validate(values)


def numeric_summary(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype="float64")
    if not np.isfinite(values).all():
        raise ValueError("진단 통계에 유한하지 않은 값이 있습니다.")
    if not len(values):
        return {"count": 0, "minimum": None, "p10": None, "median": None, "p90": None, "maximum": None}
    return {"count": len(values), "minimum": float(values.min()), "p10": float(np.quantile(values, 0.1)),
            "median": float(np.median(values)), "p90": float(np.quantile(values, 0.9)), "maximum": float(values.max())}


def diagnose_split(source: OperationalSplit, probability: np.ndarray, threshold: float,
                   short_interval_ms: float) -> tuple[dict, pd.DataFrame]:
    probability = np.asarray(probability, dtype="float64")
    if (probability.shape != source.y.shape or not np.isfinite(probability).all() or
            ((probability < 0) | (probability > 1)).any()):
        raise ValueError("모델 확률의 행 수·범위가 올바르지 않습니다.")
    observations = prefix_observations(source)
    prediction = (probability >= threshold).astype("int64")
    frame = pd.concat([source.metadata.copy(), source.X.copy(), observations], axis=1)
    frame["label"], frame["prediction"], frame["anomaly_probability"] = source.y, prediction, probability
    frame["threshold"] = threshold
    frame["outcome"] = np.select([(source.y == 0) & (prediction == 0), (source.y == 0) & (prediction == 1),
                                  (source.y == 1) & (prediction == 0)], ["TN", "FP", "FN"], default="TP")
    # 첫 메시지 0ms는 실제 짧은 간격 관측과 분리한다. 정답은 변경하지 않는다.
    frame["interval_bucket"] = np.select([
        observations.is_first_message.to_numpy(), source.X.message_interval_ms.to_numpy() <= short_interval_ms,
        source.X.message_interval_ms.to_numpy() <= 5.0], ["first_message", "at_most_1ms", "over_1_to_5ms"],
        default="over_5ms")
    buckets, groups, sessions = [], [], []
    for (scenario, bucket), rows in frame.groupby(["scenario_id", "interval_bucket"], sort=True):
        normal = rows.label.eq(0)
        anomaly = rows.label.eq(1)
        fp, fn = int(rows.outcome.eq("FP").sum()), int(rows.outcome.eq("FN").sum())
        buckets.append({"scenario_id": scenario, "bucket": bucket, "rows": len(rows), "fp": fp, "fn": fn,
                        "normal_rows": int(normal.sum()), "anomaly_rows": int(anomaly.sum()),
                        "fpr": fp / int(normal.sum()) if normal.any() else None,
                        "recall": 1 - fn / int(anomaly.sum()) if anomaly.any() else None})
    columns = FEATURE_NAMES + ["prefix_mean_interval_ms", "parsed_bytes_prefix_total", "recv_ahead_of_parsed_bytes",
                               "anomaly_probability"]
    for (scenario, outcome), rows in frame.groupby(["scenario_id", "outcome"], sort=True):
        groups.append({"scenario_id": scenario, "outcome": outcome, "rows": len(rows),
                       "first_messages": int(rows.is_first_message.sum()),
                       "same_recv_counter_rows": int(rows.recv_counter_same_as_previous.sum()),
                       "short_nonfirst_rows": int((~rows.is_first_message & rows.message_interval_ms.le(short_interval_ms)).sum()),
                       "features": {name: numeric_summary(rows[name].to_numpy()) for name in columns}})
    for key, rows in frame.groupby(["campaign_id", "run_id", "session_id"], sort=False):
        negatives = int(rows.label.eq(0).sum())
        fp = int(rows.outcome.eq("FP").sum())
        sessions.append({**dict(zip(["campaign_id", "run_id", "session_id"], key)),
                         "scenario_id": rows.scenario_id.iloc[0], "rows": len(rows), "fp": fp,
                         "fn": int(rows.outcome.eq("FN").sum()),
                         "message_fpr": fp / negatives if negatives else None})
    normal_sessions = [item for item in sessions if item["message_fpr"] is not None]
    transform_checks = {}
    for transform_id in ("raw7_v1", "without_io5_v1", "prefix_mean5_v1", "prefix_mean_parsed6_v1"):
        transformed = transform_prefix_features(source, transform_id, observations)
        transform_checks[transform_id] = {"rows": len(transformed), "columns": list(transformed.columns),
                                          "finite": bool(np.isfinite(transformed.to_numpy()).all()), "fit_performed": False}
    return {"metrics": calculate_metrics(source.y, probability, threshold),
            "by_scenario": scenario_metrics(source, prediction), "interval_buckets": buckets,
            "scenario_outcome_features": groups, "sessions": sessions,
            "normal_session_diagnostics": {
                "sessions": len(normal_sessions), "sessions_with_any_fp": sum(item["fp"] > 0 for item in normal_sessions),
                "macro_message_fpr": float(np.mean([item["message_fpr"] for item in normal_sessions])),
                "interpretation": "세션별 메시지 FPR의 평균이며 별도 세션 분류 모델의 FPR이 아니다."},
            "candidate_transform_checks": transform_checks}, frame


def render_report(report: dict) -> str:
    validation = report["splits"]["validation"]
    lines = ["# RF 정상 묶음 오탐 진단과 보정 사전 설계", "", "## 이번 단계의 범위", "",
             "기존 RF leaf=5·threshold=0.3을 고정하고 Train/Validation을 재예측했다. 새 모델 학습·threshold 탐색·Test CSV 접근·트래픽 송신·배포는 하지 않았다.",
             "이미 사용한 Test는 기존 최종 JSON 보고서의 승인 보류 근거만 인용했다. 이 분석은 Test 공개 후의 개발 진단이며 미관측 데이터에 대한 독립 검증이 아니다.", "",
             "## 기존 모델 재예측", "", "| 자료 | 메시지 | FP | FN | FPR | Recall |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, split in report["splits"].items():
        metrics, matrix = split["metrics"], split["metrics"]["confusion_matrix"]
        lines.append(f"| {name} | {metrics['rows']} | {matrix['fp']} | {matrix['fn']} | {metrics['false_positive_rate']:.2%} | {metrics['recall']:.2%} |")
    lines += ["", "Train 성능은 학습 자료에 대한 참고값이며 일반화 성능이 아니다. Validation은 이전 후보·threshold 선정에 이미 사용됐다.",
              "", "## Validation의 정상 시나리오", "", "| 시나리오 | 정상 메시지 | FP | FPR |", "| --- | ---: | ---: | ---: |"]
    for name, values in validation["by_scenario"].items():
        if values["normal_rows"]:
            lines.append(f"| {name} | {values['normal_rows']} | {values['fp']} | {values['false_positive_rate']:.2%} |")
    lines += ["", "## 파싱 간격별 정상 묶음과 burst", "", "| 시나리오 | 간격 구간 | 메시지 | FP | FN |",
              "| --- | --- | ---: | ---: | ---: |"]
    for row in validation["interval_buckets"]:
        if row["scenario_id"] in {"normal_batched_chat", "controlled_burst_chat"}:
            lines.append(f"| {row['scenario_id']} | {row['bucket']} | {row['rows']} | {row['fp']} | {row['fn']} |")
    lines += ["", "첫 메시지의 0ms는 비교할 이전 메시지가 없다는 표현이다. 나머지 1ms 이하 구간도 네트워크 도착 간격이 아니라 파서 실행 간격이다.",
              "", "## 입력과 관측 시점의 단서", ""]
    importance = report["training_impurity_importance"]
    lines.append(f"RF의 시간 관련 중요도 합은 {importance['timing_sum']:.2%}다. 이는 학습 트리의 불순도 감소 중요도이며 인과관계나 판단의 기여 비율을 증명하지 않는다.")
    for name, value in importance["features"].items():
        lines.append(f"- `{name}`: {value:.6f}")
    for group in validation["scenario_outcome_features"]:
        if group["scenario_id"] == "normal_batched_chat" and group["outcome"] == "FP":
            interval = group["features"]["message_interval_ms"]
            lines += ["", f"정상 묶음 FP {group['rows']}건의 파싱 간격 중앙값은 {interval['median']:.6f}ms, 최댓값은 {interval['maximum']:.6f}ms다.",
                      f"이 중 첫 메시지는 {group['first_messages']}건, 이전 메시지와 누적 수신량이 같은 행은 {group['same_recv_counter_rows']}건이다."]
    lines += ["", "한 번의 RecvCompleted에서 받은 전체 바이트가 먼저 누적되고 그 뒤 각 PacketParsed가 발생한다. 따라서 뒤 프레임까지 이미 수신한 바이트가 앞 메시지 snapshot에 포함될 수 있다. 이는 실제로 이미 관측한 값이며 미래 시각을 읽는 누출과는 다르지만, 현재 프레임까지 파싱한 바이트와 의미가 다르다.",
              "", "같은 수신 counter와 짧은 파싱 간격은 묶음 처리의 단서다. recv id나 클라이언트 송신 간격 자체는 관측하지 않으므로 실제 TCP 패킷 묶음 경계를 확정하지 않는다.",
              "", "## 보정 후보와 새 데이터 계획", "", "| 후보 | 입력 표현 | 입력 수 |", "| --- | --- | ---: |"]
    descriptions = {"rf_raw7": "원본 7개 재학습 대조군", "rf_without_io5": "누적 송수신량 제외",
                    "rf_prefix_mean5": "누적 송수신량 제외 + 과거·현재 평균 파싱 간격",
                    "rf_prefix_mean_parsed6": "평균 파싱 간격 + 현재까지 파싱한 프레임 크기 합"}
    for candidate, columns in report["recovery_design"]["candidate_input_columns"].items():
        lines.append(f"| {candidate} | {descriptions[candidate]} | {len(columns)} |")
    design = report["recovery_design"]
    lines += ["", "트리 수 100·leaf 5·seed 42를 유지하고 후보 4개 × threshold 0.3/0.4/0.5/0.6/0.7의 20개 조합만 새 Validation에서 비교하도록 고정했다. 아직 학습하거나 후보를 선정하지 않았다.",
              "", f"새 캠페인은 6개·계획 run {design['planned_runs']}개·계획 세션 {design['planned_sessions']}개다. 각 캠페인에서 정상 묶음 batch 1/4/8과 pacing 3개 범위를 9조합으로 교차한다. 같은 반복의 burst는 동일 크기·개수 범위·batch·동시성을 사용하고 지정 대기만 0이다.",
              "", "캠페인 역할은 새 Train 3개·Validation 1개·Test 2개다. seed와 파생 run seed가 이전 계획과 겹치지 않음을 확인했다. 이 값들은 계획이며 실제로 수집한 데이터가 아니다.",
              "", "기존 전체 FPR 10%·Recall 50%·비용 한도를 유지하고 정상 묶음 FPR 10% 이하도 별도 요구한다. 새 Validation에서 후보를 잠근 뒤 새 Test로만 승인 확인한다. 기준 미달이면 미선정/승인 보류하며 Test 점수로 정책을 바꾸지 않는다.",
              "", "## 한계와 다음 작업", ""] + [f"- {item}" for item in report["limitations"]]
    lines += ["", "다음 커밋은 이 설계를 실제 수집기의 별도 실행 경로에 연결하여 새 캠페인을 수집·품질 검증하고 3/1/2 역할로 분리하는 단계다. 그 다음 새 Train 전용 학습·Validation 선정·비용 확인·새 Test 승인 순서로 진행한다.",
              "", "추가 보정 묶음은 아직 진행 중이므로 FastAPI 연결·TCP 통합·최종 문서화를 포함한 큰 작업 묶음은 여전히 4개 남아 있다.", ""]
    return "\n".join(lines)


def run_diagnosis(config: DiagnosisConfig, config_path: Path) -> dict:
    outputs = [config.report_prefix.with_suffix(suffix) for suffix in (".json", ".md", ".predictions.csv", ".errors.csv")]
    if config.artifact_dir.exists() or any(path.exists() for path in outputs):
        raise FileExistsError("기존 진단을 덮어쓰지 않습니다. 새 artifact-dir와 report-prefix를 지정하세요.")
    data = load_operational_data(config.dataset_dir, config.dataset_id)
    comparison, final = read_json(config.comparison_report), read_json(config.final_report)
    prior = comparison["comparison"]["models"]["best_rf_validation"]
    if (prior["candidate_id"] != config.candidate_id or prior["threshold"] != config.threshold or
            final["status"] != "complete" or final["approval"]["status"] != "withheld_after_test" or
            final["selected_model_id"] != "selected_rf" or
            final["models"]["selected_rf"]["candidate_id"] != config.candidate_id or
            final["models"]["selected_rf"]["threshold"] != config.threshold):
        raise ValueError("기존 선정·승인 보류 모델과 진단 정책이 다릅니다.")
    design, previous_design = read_json(config.recovery_plan), read_json(config.previous_plan)
    plans = compile_recovery_plans(design, previous_design)
    plan_summary = summarize_recovery_design(design, plans)
    pipeline, training_report, model_evidence, saved = load_verified_model(config.model_dir, "rf", data)
    if file_hash(config.model_dir / "pipeline.joblib") != final["models"]["selected_rf"]["files"]["model"]["sha256"]:
        raise ValueError("최종 평가 RF와 진단 RF의 모델 해시가 다릅니다.")
    paths = [config_path, config.recovery_plan, config.previous_plan, config.comparison_report, config.final_report,
             config.model_dir / "training_manifest.json", Path(model_evidence["report_path"])]
    paths += [config.model_dir / name for name in model_evidence["artifacts"]]
    paths += [AI_ROOT / "data_collection" / "recovery_design.py",
              AI_ROOT / "data_preparation" / "operational_prefix_features.py", Path(__file__).resolve(),
              AI_ROOT.parent / "Server" / "ServerBase" / "Session.cpp",
              AI_ROOT.parent / "Server" / "FeatureExtraction" / "FeatureCollector.cpp"]
    protected = {str(path.resolve()): file_hash(path) for path in paths}
    protected.update({str(config.dataset_dir / name): digest for name, digest in data.input_sha256.items()})
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    config.report_prefix.parent.mkdir(parents=True, exist_ok=True)
    lock_path = config.artifact_dir / "diagnosis_lock.json"
    write_json(lock_path, {"status": "locked_before_diagnostic_predictions", "created_at_utc": datetime.now(UTC).isoformat(),
                          "config": config.model_dump(mode="json"), "protected_sha256": protected,
                          "recovery_plan_snapshot": design, "recovery_campaign_snapshots": [
                              {"slot": slot, "plan": plan.model_dump(mode="json")} for slot, plan in plans]})
    try:
        splits, frames = {}, []
        for name in ("train", "validation"):
            source = getattr(data, name)
            probability = pipeline.predict_proba(source.X)[:, 1]
            if name == "validation" and (saved.row_id.tolist() != source.metadata.row_id.tolist() or
                    not np.array_equal(saved.label.to_numpy(), source.y) or
                    not np.allclose(saved.anomaly_probability.to_numpy(), probability, rtol=0, atol=1e-14) or
                    calculate_metrics(source.y, probability, 0.5) != training_report["validation_metrics"] or
                    calculate_metrics(source.y, probability, config.threshold) != prior["validation_metrics"]):
                raise ValueError("기존 Validation 예측·지표·행 순서와 다릅니다.")
            splits[name], frame = diagnose_split(source, probability, config.threshold, config.short_parse_interval_ms)
            frames.append(frame)
        importance = dict(zip(FEATURE_NAMES, pipeline.named_steps["classifier"].feature_importances_.tolist()))
        report = {
            "report_version": "v1", "status": "complete", "generated_at_utc": datetime.now(UTC).isoformat(),
            "scope": "post_test_development_diagnosis_and_recovery_design", "dataset_id": data.dataset_id,
            "candidate_id": config.candidate_id, "threshold": config.threshold, "splits": splits,
            "training_impurity_importance": {"features": importance,
                                             "timing_sum": importance["connection_duration_ms"] + importance["message_interval_ms"]},
            "previous_test_evidence": {"source": str(config.final_report), "sha256": protected[str(config.final_report)],
                                       "approval": final["approval"], "metrics": final["test_results"]["selected_rf"]["metrics"],
                                       "normal_batched": final["test_results"]["selected_rf"]["by_scenario"]["normal_batched_chat"],
                                       "source_type": "existing_report_only_no_test_csv_or_prediction_csv_read"},
            "recovery_design": plan_summary, "recovery_plan_sha256": protected[str(config.recovery_plan)],
            "diagnosis_lock_sha256": file_hash(lock_path), "protected_sha256": protected,
            "test_files_read": False, "model_fit_performed": False, "threshold_search_performed": False,
            "traffic_sent": False, "deployment_approved": False, "fastapi_still_dummy": True,
            "limitations": [
                "학습된 중요도와 오분류의 상관은 원인 확정이나 보정 효과 검증이 아니다.",
                "단일 수신의 누적 바이트와 현재 프레임의 파싱 합은 의미가 다르며 기존 v2 수집 자체가 잘못됐다고 단정하지 않는다.",
                "평균 간격도 파서 처리 시각의 평균이지 클라이언트 송신 간격이나 TCP 패킷 도착 간격이 아니다.",
                "첫 정상 묶음과 burst의 짧은 prefix는 같은 관측을 가질 수 있다. 과거가 없는 순간의 정보를 복원하지 못한다.",
                "파생 입력은 종료 summary를 쓰지 않는다. 누락된 메시지를 0이나 미래 데이터로 채우지 않는다.",
                "본문 내부 유효성·의도·실제 공격을 관측하지 않는다. error_count가 정답이나 현재 메시지의 검증 결과가 아니다.",
                "새 seed·캠페인도 같은 생성기·장비의 실험이다. 실제 침입 탐지 일반화를 보장하지 않는다.",
                "실시간 prefix 상태·누락 감지·수명 관리 및 새 모델 학습·비용·독립 승인은 후속 작업이다.",
            ],
        }
        verify_hashes(protected)
        predictions = pd.concat(frames, ignore_index=True)
        write_json(outputs[0], report)
        with outputs[1].open("x", encoding="utf-8") as stream:
            stream.write(render_report(report))
        for path, frame in ((outputs[2], predictions), (outputs[3], predictions[predictions.outcome.isin(["FP", "FN"])])):
            with path.open("x", encoding="utf-8", newline="") as stream:
                frame.to_csv(stream, index=False)
        verify_hashes(protected)
        write_json(config.artifact_dir / "diagnosis_manifest.json", {
            "status": "complete", "protected_sha256": protected, "lock_sha256": file_hash(lock_path),
            "outputs": {str(path): file_hash(path) for path in outputs}, "test_files_read": False,
            "fit_performed": False, "collection_performed": False})
        return report
    except Exception as error:
        write_json(config.artifact_dir / "diagnosis_failure.json", {"status": "failed", "error": str(error)})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="고정 RF의 Train/Validation 오탐을 진단하고 새 보정 실험 계획을 검증합니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_rf_diagnosis.json")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    overrides = {name: getattr(args, name).resolve() for name in ("artifact_dir", "report_prefix") if getattr(args, name)}
    config = DiagnosisConfig.model_validate({**config.model_dump(), **overrides})
    report = run_diagnosis(config, args.config.resolve())
    matrix = report["splits"]["validation"]["metrics"]["confusion_matrix"]
    print(f"RF 진단 완료: Validation FP {matrix['fp']}건·FN {matrix['fn']}건. 보정 후보 학습·수집은 미실행.")
    print(f"보고서: {config.report_prefix.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
