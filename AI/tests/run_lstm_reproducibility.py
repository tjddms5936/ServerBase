from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
for dependency in (".deps", ".deps_lstm"):
    if (AI_ROOT / dependency).exists():
        sys.path.insert(0, str(AI_ROOT / dependency))
sys.path.insert(0, str(AI_ROOT))

from data_collection.augmentation import verify_hashes
from data_collection.quality import read_json
from data_preparation.operational_dataset import file_hash
from training.run_operational_lstm_experiment import load_config, load_plan, run_experiment
from training.train_operational_logistic_regression import write_json


def history_hash(history: list[dict]) -> str:
    return hashlib.sha256(json.dumps(history, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def verify(config_path: Path, report_path: Path) -> dict:
    if report_path.exists():
        raise FileExistsError("기존 재현 보고서는 덮어쓰지 않습니다. 새 report-path를 지정하세요.")
    config = load_config(config_path)
    plan = load_plan(config.plan_path)
    if any(report_path.resolve().is_relative_to(root.resolve()) for root in
           (config.artifact_dir, plan.base_dataset_dir, plan.sequence_dataset_dir, plan.reference_selection.parent)):
        raise ValueError("재현 보고서를 원본 데이터나 기존 모델 내부에 저장하지 않습니다.")
    source_report_path = config.report_prefix.with_suffix(".json")
    source = read_json(source_report_path)
    manifest_path = config.artifact_dir / "experiment_manifest.json"
    manifest = read_json(manifest_path)
    if source["status"] != "complete" or manifest["status"] != "complete" or source["protocol"]["execution_config"] != config.model_dump(mode="json"):
        raise ValueError("완료된 원본 실험과 재현 설정이 다릅니다.")
    protected = dict(source["protected_sha256"])
    protected.update(manifest["reports"])
    protected[str(manifest_path)] = file_hash(manifest_path)
    protected.update({str(config.artifact_dir / relative): value["sha256"] for relative, value in manifest["artifacts"].items()})
    verify_hashes(protected)
    candidates = []
    # 실제 원본은 보존하고 재학습 산출물만 임시 경로에 만든 뒤 비교를 마치면 정리한다.
    with tempfile.TemporaryDirectory(prefix="lstm_replay_") as temporary:
        replay_config = config.model_copy(update={"artifact_dir": Path(temporary) / "artifacts", "report_prefix": Path(temporary) / "reports" / "lstm"})
        replay = run_experiment(replay_config)
        if source["environment"] != replay["environment"]:
            raise ValueError("재현 실행의 장치·버전·스레드 환경이 원본과 다릅니다.")
        if source["trials"] != replay["trials"] or source["selection"]["selected_trial"] != replay["selection"]["selected_trial"]:
            raise ValueError("재학습의 전체 threshold 지표 또는 선정 결과가 다릅니다.")
        for original, repeated in zip(source["candidates"], replay["candidates"], strict=True):
            if (original["id"] != repeated["id"] or original["training"]["history"] != repeated["training"]["history"] or
                    original["training"]["weight_sha256"] != repeated["training"]["weight_sha256"] or
                    original["model_sha256"] != repeated["model_sha256"]):
                raise ValueError("재학습의 epoch 손실·가중치·checkpoint 해시가 다릅니다.")
            candidates.append({"id": original["id"], "epochs_used": original["training"]["epochs_used"],
                               "best_epoch": original["training"]["best_epoch"], "history_sha256": history_hash(original["training"]["history"]),
                               "weight_sha256": original["training"]["weight_sha256"], "checkpoint_sha256": original["model_sha256"],
                               "history_equal": True, "weights_equal": True, "checkpoint_equal": True})
        prediction_hash = file_hash(config.report_prefix.with_suffix(".predictions.csv"))
        if prediction_hash != file_hash(replay_config.report_prefix.with_suffix(".predictions.csv")):
            raise ValueError("재학습의 같은 행 비교 CSV가 다릅니다.")
        replay_protocol_hash = replay["protocol_sha256"]
    verify_hashes(protected)
    result = {"status": "passed", "generated_at_utc": datetime.now(UTC).isoformat(), "source_report_sha256": file_hash(source_report_path),
              "source_protocol_sha256": source["protocol_sha256"], "replay_protocol_sha256": replay_protocol_hash,
              "protocol_note": "임시 출력 경로가 달라 protocol 해시는 달라진다. 데이터·설정·seed·환경은 유지해 수치·가중치·예측을 대조했다.",
              "environment": replay["environment"], "candidate_results": candidates, "trial_count": len(source["trials"]),
              "all_trial_metrics_equal": True, "selected_trial_equal": True, "comparison_predictions_sha256": prediction_hash,
              "comparison_predictions_equal": True, "protected_files_unchanged": True, "temporary_replay_artifacts_removed": True,
              "test_files_read": False, "test_evaluated": False, "script_sha256": file_hash(Path(__file__)),
              "limitations": ["현재 CPU 환경·버전·seed에서의 재현 검사다. 다른 장치나 버전의 완전 동일성을 보장하지 않는다.",
                              "같은 데이터 재학습은 독립 데이터 일반화 평가가 아니다."]}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="완료된 운영 LSTM을 임시 경로에 재학습해 수치·가중치·예측 재현성을 검사합니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_lstm_experiment.json")
    parser.add_argument("--report-path", type=Path, default=AI_ROOT / "reports" / "operational_lstm_reproducibility.json")
    args = parser.parse_args()
    try:
        result = verify(args.config.resolve(), args.report_path.resolve())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"[LSTM 재현 검사] 실패: {error}", file=sys.stderr)
        return 2
    print(f"[LSTM 재현 검사] 후보 {len(result['candidate_results'])}개·조합 {result['trial_count']}개·예측·가중치 일치 / Test 미사용")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
