from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from data_collection.augmentation import verify_hashes
from data_collection.quality import inside_path, read_json
from data_preparation.operational_dataset import FEATURE_NAMES, file_hash
from evaluation.compare_operational_baselines import load_verified_model
from training.train_operational_logistic_regression import calculate_metrics


def manifest_evidence(root: Path, data) -> dict:
    path = root / "experiment_manifest.json"
    manifest = read_json(path)
    if manifest["status"] != "complete" or manifest["input_sha256"] != data.input_sha256:
        raise ValueError("완료 모델의 고정 Train/Validation 입력이 다릅니다.")
    evidence = {str(path): file_hash(path)}
    evidence.update({str(inside_path(root, name)): item["sha256"] for name, item in manifest["artifacts"].items()})
    evidence.update(manifest["reports"])
    verify_hashes(evidence)
    return evidence


def freeze_models(config, data, validation_windows) -> tuple[dict, dict, dict]:
    augmentation = read_json(config.augmentation_report)
    lstm = read_json(config.lstm_report)
    evidence = manifest_evidence(config.augmentation_root, data)
    evidence.update(manifest_evidence(config.lstm_root, data))
    evidence.update({str(config.augmentation_report): file_hash(config.augmentation_report),
                     str(config.lstm_report): file_hash(config.lstm_report)})
    # 과거 실험 산출물과 당시 source의 보호 해시를 확인하되 수정하거나 재학습하지 않는다.
    evidence.update(lstm["protected_sha256"])
    verify_hashes(evidence)
    descriptors, runtimes = {}, {}
    for policy in config.models:
        if policy.kind == "lstm":
            selected = lstm["selection"]["selected_trial"]
            if (lstm["selection"]["status"] != "selected" or selected["candidate_id"] != policy.candidate_id or
                    selected["threshold"] != policy.threshold or lstm["window_size"] != config.window_size):
                raise ValueError("LSTM 정책이 기존 Validation 선정과 다릅니다.")
            candidate = next(value for value in lstm["candidates"] if value["id"] == policy.candidate_id)
            root = config.lstm_root
            files = {"model": root / candidate["model_path"], "scaler": root / "scaler.joblib",
                     "feature_schema": root / "feature_schema.json", "label_map": root / "label_map.json",
                     "sequence_schema": root / "sequence_schema.json"}
            expected_metrics = selected["metrics"]
            architecture = candidate["specification"]
        else:
            role = "best_lr_validation" if policy.kind == "logistic_regression" else "best_rf_validation"
            prior = augmentation["comparison"]["models"][role]
            if prior["candidate_id"] != policy.candidate_id or prior["threshold"] != policy.threshold or prior["kind"] != policy.kind:
                raise ValueError("LR/RF 정책이 기존 Validation 선정과 다릅니다.")
            root = config.augmentation_root / "candidates" / policy.candidate_id
            pipeline, report, _, saved = load_verified_model(root, "lr" if policy.kind == "logistic_regression" else "rf", data)
            probability = pipeline.predict_proba(data.validation.X)[:, 1]
            if (saved.row_id.tolist() != data.validation.metadata.row_id.tolist() or
                    not np.array_equal(saved.label.to_numpy(), data.validation.y) or
                    not np.allclose(saved.anomaly_probability.to_numpy(), probability, rtol=0, atol=1e-14) or
                    calculate_metrics(data.validation.y, probability, 0.5) != report["validation_metrics"]):
                raise ValueError("기존 LR/RF의 Validation 재예측·행 정렬이 다릅니다.")
            files = {"model": root / "pipeline.joblib", "feature_schema": root / "feature_schema.json", "label_map": root / "label_map.json"}
            expected_metrics, architecture = prior["validation_metrics"], None
        descriptor = {**policy.model_dump(), "window_size": config.window_size, "architecture": architecture,
                      "batch_size": 64, "files": {role: {"path": str(path.resolve()), "sha256": file_hash(path), "bytes": path.stat().st_size}
                                                    for role, path in files.items()}, "validation_metrics": expected_metrics}
        runtime = FrozenRuntime(descriptor)
        if read_json(files["feature_schema"]) != data.schema and policy.kind == "lstm":
            raise ValueError("LSTM 원본 feature schema가 고정 메시지 의미와 다릅니다.")
        if read_json(files["label_map"]) != data.label_map:
            raise ValueError("최종 비교 모델의 정답 정의가 다릅니다.")
        if policy.kind == "lstm":
            scaler = runtime.scaler
            if (int(scaler.n_samples_seen_) != len(data.train.y) or list(scaler.feature_names_in_) != FEATURE_NAMES or
                    not np.allclose(scaler.mean_, data.train.X.mean().to_numpy()) or
                    not np.allclose(scaler.var_, data.train.X.var(ddof=0).to_numpy())):
                raise ValueError("LSTM scaler의 원본 Train 전용 학습을 확인할 수 없습니다.")
            probability = runtime.predict_raw(validation_windows.X, validation_windows.lengths)
        else:
            probability = runtime.predict_raw(data.validation.X.to_numpy())
        metrics = calculate_metrics(data.validation.y, probability, policy.threshold)
        if metrics != expected_metrics:
            raise ValueError("잠글 모델의 Validation 지표가 이전 선정 결과와 다릅니다.")
        descriptors[policy.id], runtimes[policy.id] = descriptor, runtime
        evidence.update({value["path"]: value["sha256"] for value in descriptor["files"].values()})
    verify_hashes(evidence)
    return descriptors, runtimes, evidence


class FrozenRuntime:
    def __init__(self, descriptor: dict) -> None:
        self.descriptor = descriptor
        self.kind, self.threshold = descriptor["kind"], descriptor["threshold"]
        verify_hashes({value["path"]: value["sha256"] for value in descriptor["files"].values()})
        model_path = Path(descriptor["files"]["model"]["path"])
        if self.kind == "lstm":
            # LR/RF 측정 프로세스에는 torch를 import하지 않아 다른 모델의 메모리 비용을 섞지 않는다.
            import torch
            from training.operational_lstm import load_checkpoint
            torch.set_num_threads(1)
            torch.use_deterministic_algorithms(True, warn_only=False)
            self.model, _ = load_checkpoint(model_path, descriptor["architecture"], descriptor["window_size"])
            self.scaler = joblib.load(descriptor["files"]["scaler"]["path"])
        else:
            self.pipeline = joblib.load(model_path)
            self.model = self.pipeline.named_steps["classifier"]
            self.scaler = self.pipeline.named_steps.get("scaler")
            if self.kind == "random_forest" and self.model.n_jobs != 1:
                raise ValueError("RF 비용 비교는 학습된 n_jobs=1 정책을 유지합니다.")

    def prepare(self, raw: np.ndarray, lengths: np.ndarray | None = None):
        if self.kind != "lstm":
            frame = pd.DataFrame(raw, columns=FEATURE_NAMES)
            return self.scaler.transform(frame) if self.scaler is not None else frame
        import torch
        width = self.descriptor["window_size"]
        if (raw.ndim != 3 or raw.shape[1:] != (width, 7) or lengths is None or lengths.shape != (len(raw),) or
                lengths.dtype != np.dtype("int64") or ((lengths < 1) | (lengths > width)).any()):
            raise ValueError("고정 LSTM의 입력 shape·유효 길이가 다릅니다.")
        mask = np.arange(width)[None, :] < lengths[:, None]
        values = np.zeros(raw.shape, dtype="float32")
        values[mask] = self.scaler.transform(pd.DataFrame(raw[mask], columns=FEATURE_NAMES)).astype("float32")
        if not np.isfinite(values).all():
            raise ValueError("최종 추론 정규화 값이 유한하지 않습니다.")
        return torch.from_numpy(values), torch.from_numpy(lengths)

    def predict_ready(self, prepared) -> np.ndarray:
        if self.kind != "lstm":
            return self.model.predict_proba(prepared)[:, 1]
        import torch
        X, lengths = prepared
        chunks = []
        # 학습 당시의 batch 64 평가 경로를 유지해 원본 Validation 지표도 정확히 대조한다.
        with torch.inference_mode():
            for start in range(0, len(X), self.descriptor["batch_size"]):
                end = start + self.descriptor["batch_size"]
                chunks.append(torch.sigmoid(self.model(X[start:end], lengths[start:end])).numpy().astype("float64"))
        return np.concatenate(chunks)

    def predict_raw(self, raw: np.ndarray, lengths: np.ndarray | None = None) -> np.ndarray:
        if self.kind != "lstm":
            return self.pipeline.predict_proba(pd.DataFrame(raw, columns=FEATURE_NAMES))[:, 1]
        return self.predict_ready(self.prepare(raw, lengths))

    def array_storage_bytes(self) -> int:
        if self.kind == "lstm":
            size = sum(parameter.numel() * parameter.element_size() for parameter in self.model.parameters())
        elif self.kind == "random_forest":
            size = sum(estimator.tree_.__getstate__()["nodes"].nbytes + estimator.tree_.__getstate__()["values"].nbytes
                       for estimator in self.model.estimators_) + self.model.classes_.nbytes
        else:
            size = self.model.coef_.nbytes + self.model.intercept_.nbytes + self.model.classes_.nbytes
        if self.scaler is not None:
            size += sum(np.asarray(getattr(self.scaler, name)).nbytes for name in ("mean_", "var_", "scale_", "n_samples_seen_"))
        return int(size)
