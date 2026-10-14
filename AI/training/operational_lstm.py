from __future__ import annotations

import hashlib
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps_lstm").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps_lstm"))

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence
from sklearn.preprocessing import StandardScaler

from data_preparation.operational_dataset import FEATURE_NAMES
from data_preparation.operational_sequences import SequenceSplit


class MessageLSTM(nn.Module):
    def __init__(self, hidden_size: int) -> None:
        super().__init__()
        self.lstm = nn.LSTM(input_size=len(FEATURE_NAMES), hidden_size=hidden_size, num_layers=1, batch_first=True)
        self.head = nn.Linear(hidden_size, 1)

    def forward(self, X: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        if (X.ndim != 3 or X.shape[2] != len(FEATURE_NAMES) or X.dtype != torch.float32 or X.device.type != "cpu" or
                lengths.shape != (len(X),) or lengths.dtype != torch.int64 or lengths.device.type != "cpu" or
                len(X) == 0 or torch.any(lengths < 1) or torch.any(lengths > X.shape[1])):
            raise ValueError("LSTM 입력은 CPU float32 창과 1부터 시작하는 int64 유효 길이여야 합니다.")
        # padding은 순환 계산에서 제외한다. 초기 상태를 넘기지 않아 창마다 0에서 시작한다.
        packed = pack_padded_sequence(X, lengths, batch_first=True, enforce_sorted=False)
        _, (hidden, _) = self.lstm(packed)
        return self.head(hidden[-1]).squeeze(-1)


@contextmanager
def cpu_runtime(seed: int, threads: int):
    previous_threads = torch.get_num_threads()
    previous_deterministic = torch.are_deterministic_algorithms_enabled()
    previous_warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    try:
        torch.set_num_threads(threads)
        torch.use_deterministic_algorithms(True, warn_only=False)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            yield
    finally:
        torch.set_num_threads(previous_threads)
        torch.use_deterministic_algorithms(previous_deterministic, warn_only=previous_warn_only)


def transform_windows(scaler: StandardScaler, windows: SequenceSplit) -> np.ndarray:
    # 겹친 창을 fit에 사용하지 않는다. 이미 fit한 scaler로 유효 입력만 변환하고 padding은 0으로 남긴다.
    result = np.zeros(windows.X.shape, dtype="float32")
    valid = pd.DataFrame(windows.X[windows.mask], columns=FEATURE_NAMES)
    result[windows.mask] = scaler.transform(valid).astype("float32")
    if not np.isfinite(result).all():
        raise ValueError("정규화한 시계열 입력에 비정상 수치가 있습니다.")
    return result


@dataclass
class StopState:
    patience: int
    best_loss: float = float("inf")
    best_epoch: int = 0
    stale_epochs: int = 0

    def update(self, epoch: int, loss: float) -> tuple[bool, bool]:
        if not np.isfinite(loss):
            raise ValueError("Validation 손실이 유한하지 않습니다.")
        improved = loss < self.best_loss
        if improved:
            self.best_loss, self.best_epoch, self.stale_epochs = loss, epoch, 0
        else:
            self.stale_epochs += 1
        return improved, self.stale_epochs >= self.patience


def evaluate(model: MessageLSTM, X: torch.Tensor, lengths: torch.Tensor, y: torch.Tensor,
             batch_size: int) -> tuple[float, np.ndarray]:
    model.eval()
    criterion = nn.BCEWithLogitsLoss(reduction="sum")
    loss_sum, chunks = 0.0, []
    # Validation은 gradient 계산과 가중치 갱신 없이 관측한다.
    with torch.inference_mode():
        for start in range(0, len(y), batch_size):
            end = start + batch_size
            logits = model(X[start:end], lengths[start:end])
            loss_sum += float(criterion(logits, y[start:end]).item())
            chunks.append(torch.sigmoid(logits).numpy().astype("float64"))
    probability = np.concatenate(chunks)
    loss = loss_sum / len(y)
    if not np.isfinite(loss) or not np.isfinite(probability).all():
        raise ValueError("평가 손실·확률이 유한하지 않습니다.")
    return loss, probability


def weight_hash(state: dict[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(state):
        value = state[name].detach().cpu().contiguous().numpy()
        digest.update(json.dumps([name, str(value.dtype), list(value.shape)], separators=(",", ":")).encode("ascii"))
        digest.update(value.tobytes())
    return digest.hexdigest()


def save_checkpoint(path: Path, model: MessageLSTM, candidate: dict, epoch: int, loss: float, window_size: int) -> str:
    state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
    digest = weight_hash(state)
    with path.open("xb") as stream:
        torch.save({"format_version": "v1", "candidate": candidate, "state_dict": state,
                    "feature_order": FEATURE_NAMES, "window_size": window_size, "padding_side": "right",
                    "readout": "last_valid_timestep", "best_epoch": epoch, "best_validation_loss": loss,
                    "weight_sha256": digest}, stream)
    return digest


def load_checkpoint(path: Path, candidate: dict, window_size: int) -> tuple[MessageLSTM, dict]:
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if (checkpoint.get("format_version") != "v1" or checkpoint.get("candidate") != candidate or
            checkpoint.get("feature_order") != FEATURE_NAMES or checkpoint.get("window_size") != window_size or
            checkpoint.get("padding_side") != "right" or checkpoint.get("readout") != "last_valid_timestep"):
        raise ValueError("저장 LSTM의 구조·입력 계약이 다릅니다.")
    state = checkpoint["state_dict"]
    if (not state or any(not isinstance(value, torch.Tensor) or value.dtype != torch.float32 or
                         value.device.type != "cpu" or not torch.isfinite(value).all() for value in state.values()) or
            weight_hash(state) != checkpoint["weight_sha256"]):
        raise ValueError("저장 LSTM의 가중치·해시가 올바르지 않습니다.")
    model = MessageLSTM(candidate["hidden_size"])
    model.load_state_dict(state, strict=True)
    model.eval()
    return model, {name: value for name, value in checkpoint.items() if name != "state_dict"}


def fit_candidate(candidate: dict, training: dict, seed: int, directory: Path,
                  train_X: np.ndarray, train: SequenceSplit, validation_X: np.ndarray,
                  validation: SequenceSplit) -> tuple[dict, np.ndarray, np.ndarray]:
    torch.manual_seed(seed)
    model = MessageLSTM(candidate["hidden_size"])
    optimizer = torch.optim.Adam(model.parameters(), lr=training["learning_rate"], weight_decay=training["weight_decay"])
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(training["positive_class_weight"], dtype=torch.float32))
    X_train, X_validation = torch.from_numpy(train_X), torch.from_numpy(validation_X)
    lengths_train, lengths_validation = torch.from_numpy(train.lengths), torch.from_numpy(validation.lengths)
    y_train = torch.from_numpy(train.y.astype("float32"))
    y_validation = torch.from_numpy(validation.y.astype("float32"))
    generator = torch.Generator(device="cpu").manual_seed(seed)
    stop = StopState(training["early_stopping_patience"])
    batch_size = training["batch_size"]
    history, best_state = [], None
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    for epoch in range(1, training["max_epochs"] + 1):
        model.train()
        order = torch.randperm(len(y_train), generator=generator)
        for start in range(0, len(order), batch_size):
            indices = order[start:start + batch_size]
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(X_train[indices], lengths_train[indices]), y_train[indices])
            if not torch.isfinite(loss):
                raise ValueError("학습 손실이 유한하지 않습니다.")
            loss.backward()
            if any(parameter.grad is None or not torch.isfinite(parameter.grad).all() for parameter in model.parameters()):
                raise ValueError("LSTM gradient가 누락되거나 유한하지 않습니다.")
            optimizer.step()
        train_loss, _ = evaluate(model, X_train, lengths_train, y_train, batch_size)
        validation_loss, _ = evaluate(model, X_validation, lengths_validation, y_validation, batch_size)
        improved, should_stop = stop.update(epoch, validation_loss)
        if improved:
            # clone하지 않으면 다음 optimizer 갱신이 최적 epoch의 참조 가중치까지 바꾼다.
            best_state = {name: value.detach().clone() for name, value in model.state_dict().items()}
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": validation_loss,
                        "best_epoch_so_far": stop.best_epoch, "improved": improved, "stale_epochs": stop.stale_epochs})
        if epoch == 1 or improved or should_stop or epoch == training["max_epochs"]:
            print(f"[운영 LSTM] {candidate['id']} epoch {epoch}: Train loss={train_loss:.6f}, Validation loss={validation_loss:.6f}", flush=True)
        if should_stop:
            break
    fit_seconds = time.perf_counter() - started
    model.load_state_dict(best_state)
    _, train_probability = evaluate(model, X_train, lengths_train, y_train, batch_size)
    best_loss, probability = evaluate(model, X_validation, lengths_validation, y_validation, batch_size)
    if best_loss != stop.best_loss:
        raise ValueError("최적 epoch 복원 후 Validation 손실이 다릅니다.")
    path = directory / "model.pt"
    digest = save_checkpoint(path, model, candidate, stop.best_epoch, stop.best_loss, train_X.shape[1])
    restored, checkpoint = load_checkpoint(path, candidate, train_X.shape[1])
    restored_loss, restored_probability = evaluate(restored, X_validation, lengths_validation, y_validation, batch_size)
    if restored_loss != best_loss or not np.array_equal(restored_probability, probability):
        raise ValueError("저장 checkpoint 재로딩의 예측이 다릅니다.")
    pd.DataFrame(history).to_csv(directory / "training_history.csv", index=False, encoding="utf-8", mode="x")
    return {"epochs_used": len(history), "max_epochs": training["max_epochs"], "best_epoch": stop.best_epoch,
            "best_validation_loss": stop.best_loss, "termination": "early_stopping" if should_stop else "max_epochs",
            "fit_seconds": fit_seconds, "history": history, "parameter_count": sum(value.numel() for value in model.parameters()),
            "weight_sha256": digest, "checkpoint": checkpoint, "reload_prediction_equal": True}, train_probability, probability
