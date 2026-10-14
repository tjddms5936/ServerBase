from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import test_operational_augmentation as fixtures
import data_preparation.operational_sequences as sequences
from data_collection.quality import read_json
from data_preparation.operational_dataset import FEATURE_NAMES, METADATA_COLUMNS, build_dataset, file_hash
from training.operational_data import OperationalSplit, load_operational_data


def source_split(keys: list[tuple], labels: list[int] | None = None, split: str = "train") -> OperationalSplit:
    metadata, values = [], []
    for position, (campaign, run, session, index) in enumerate(keys):
        row = {name: "trace" for name in METADATA_COLUMNS}
        row.update(row_id=f"{campaign}:{run}:{session}:{index}", campaign_id=campaign, run_id=run, session_id=session,
                   scenario_id="controlled_burst_chat", request_index=str(index), split=split)
        metadata.append(row)
        values.append({"packet_size": 100 + position, "payload_size": 92 + position, "connection_duration_ms": float(index),
                       "message_interval_ms": 0.0 if index == 1 else 1.0, "bytes_received_total": index * 1000,
                       "bytes_sent_total": index * 24, "error_count_total": 0})
    return OperationalSplit(pd.DataFrame(values, columns=FEATURE_NAMES, dtype="float64"),
                            np.array(labels if labels is not None else [1] * len(keys), dtype="int64"), pd.DataFrame(metadata))


class WindowTests(unittest.TestCase):
    def test_recent_past_and_current_only_with_right_padding(self) -> None:
        source = source_split([("c", "r", "s", index) for index in range(1, 6)], [0, 0, 1, 0, 1])
        windows = sequences.make_windows(source, 3, "fixture")
        np.testing.assert_array_equal(windows.lengths, [1, 2, 3, 3, 3])
        np.testing.assert_array_equal(windows.source_positions, [[0, -1, -1], [0, 1, -1], [0, 1, 2], [1, 2, 3], [2, 3, 4]])
        np.testing.assert_array_equal(windows.y, source.y)
        self.assertTrue(np.all(windows.X[~windows.mask] == 0))
        for anchor, length in enumerate(windows.lengths):
            np.testing.assert_array_equal(windows.X[anchor, length - 1], source.X.iloc[anchor].to_numpy())
        self.assertEqual(sequences.read_trace(windows.metadata.iloc[4].source_row_ids), source.metadata.iloc[2:5].row_id.tolist())

    def test_unordered_input_is_sorted_inside_session_but_anchor_order_stays(self) -> None:
        source = source_split([("c", "r", "s", 3), ("c", "r", "s", 1), ("c", "r", "s", 2)])
        windows = sequences.make_windows(source, 3, "fixture")
        np.testing.assert_array_equal(windows.source_positions, [[1, 2, 0], [1, -1, -1], [1, 2, -1]])
        self.assertEqual(windows.metadata.row_id.tolist(), source.metadata.row_id.tolist())

    def test_same_session_id_in_different_runs_does_not_mix(self) -> None:
        source = source_split([("c", "a", "1", 1), ("c", "b", "1", 1), ("c", "a", "1", 2), ("c", "b", "1", 2)])
        windows = sequences.make_windows(source, 3, "fixture")
        np.testing.assert_array_equal(windows.source_positions[2], [0, 2, -1])
        np.testing.assert_array_equal(windows.source_positions[3], [1, 3, -1])

    def test_single_message_and_zero_features_are_valid_not_dropped(self) -> None:
        source = source_split([("c", "r", "s", 1)])
        windows = sequences.make_windows(source, 8, "fixture")
        self.assertEqual(windows.X.shape, (1, 8, 7))
        self.assertEqual(int(windows.lengths[0]), 1)
        self.assertTrue(windows.mask[0, 0])
        self.assertEqual(windows.X[0, 0, FEATURE_NAMES.index("message_interval_ms")], 0)
        self.assertFalse(windows.mask[0, 1])

    def test_appending_future_messages_does_not_change_existing_windows(self) -> None:
        short = source_split([("c", "r", "s", index) for index in range(1, 4)])
        long = source_split([("c", "r", "s", index) for index in range(1, 8)])
        before = sequences.make_windows(short, 4, "fixture")
        after = sequences.make_windows(long, 4, "fixture")
        for name in sequences.ARRAY_NAMES:
            np.testing.assert_array_equal(getattr(before, name), getattr(after, name)[:3])
        pd.testing.assert_frame_equal(before.metadata, after.metadata.iloc[:3].reset_index(drop=True))

    def test_label_is_current_message_not_history_majority(self) -> None:
        source = source_split([("c", "r", "s", index) for index in range(1, 5)], [1, 1, 1, 0])
        windows = sequences.make_windows(source, 4, "fixture")
        self.assertEqual(windows.y[-1], 0)
        np.testing.assert_array_equal(windows.y, [1, 1, 1, 0])

    def test_missing_duplicate_fractional_and_invalid_request_indices_are_rejected(self) -> None:
        for indices in ((1, 3), (1, 1), (0, 1), (2, 3)):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                sequences.make_windows(source_split([("c", "r", "s", index) for index in indices]), 3, "fixture")
        source = source_split([("c", "r", "s", 1)])
        source.metadata.loc[0, "request_index"] = "1.5"
        with self.assertRaises(ValueError):
            sequences.make_windows(source, 3, "fixture")

    def test_split_campaign_and_numeric_corruption_are_rejected(self) -> None:
        original = source_split([("c", "r", "s", 1), ("c", "r", "s", 2)])
        for kind in ("split", "campaign", "nan", "negative", "counter", "columns"):
            source = copy.deepcopy(original)
            if kind == "split":
                source.metadata.loc[1, "split"] = "validation"
            elif kind == "campaign":
                source.metadata.loc[1, "campaign_id"] = "other"
            elif kind == "nan":
                source.X.loc[0, "payload_size"] = np.nan
            elif kind == "negative":
                source.X.loc[0, "payload_size"] = -1
            elif kind == "counter":
                source.X.loc[1, "bytes_received_total"] = 1
            else:
                source.X = source.X[FEATURE_NAMES[::-1]]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                sequences.make_windows(source, 3, "fixture")

    def test_window_mutation_of_future_source_padding_mask_and_label_is_rejected(self) -> None:
        source = source_split([("c", "r", "s", index) for index in range(1, 5)])
        original = sequences.make_windows(source, 3, "fixture")
        for kind in ("future", "padding", "mask", "label", "length", "trace", "anchor"):
            windows = copy.deepcopy(original)
            if kind == "future":
                windows.source_positions[1, 0] = 3
                windows.X[1, 0] = source.X.iloc[3].to_numpy()
            elif kind == "padding":
                windows.X[0, 2, 0] = 99
            elif kind == "mask":
                windows.mask[0, 2] = True
            elif kind == "label":
                windows.y[0] = 0
            elif kind == "length":
                windows.lengths[0] = 2
            elif kind == "trace":
                windows.metadata.loc[0, "source_row_ids"] = '["wrong"]'
            else:
                windows.metadata.loc[0, "row_id"] = "wrong"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                sequences.validate_windows(windows, source, 3, "fixture")

    def test_window_size_one_matches_single_message_baseline(self) -> None:
        source = source_split([("c", "r", "s", index) for index in range(1, 4)])
        windows = sequences.make_windows(source, 1, "fixture")
        np.testing.assert_array_equal(windows.X[:, 0], source.X.to_numpy())
        self.assertTrue(windows.mask.all())
        for size in (0, 65, True, 1.5):
            with self.assertRaises(ValueError):
                sequences.make_windows(source, size, "fixture")

    def test_partition_overlap_is_rejected(self) -> None:
        source = source_split([("c", "r", "s", 1)])
        left = sequences.make_windows(source, 3, "fixture")
        right = copy.deepcopy(left)
        right.metadata["split"] = "validation"
        with self.assertRaises(ValueError):
            sequences.validate_partitions(left, right)


class SequenceDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.CampaignHoldoutTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = self.fixture.directory
        base = self.fixture.config()
        build_dataset(base)
        self.config = sequences.SequenceConfig(dataset_id="fixture_sequence", source_dataset_id=base.dataset_id,
                                               source_dataset_dir=base.output_dir, output_dir=self.directory / "sequences",
                                               window_size=3, expected_train_rows=6, expected_validation_rows=6)
        self.prefix = self.directory / "report"

    def build(self, **overrides):
        values = self.config.model_dump()
        values.update(overrides)
        return sequences.build_dataset(sequences.SequenceConfig.model_validate(values), self.prefix)

    def test_config_lstm_plan_and_archive_contract(self) -> None:
        default = sequences.load_config(AI_ROOT / "configs" / "operational_sequence_dataset.json")
        self.assertEqual(default.window_size, 8)
        self.assertEqual(default.padding_side, "right")
        self.assertEqual(default.target_policy, "current_message_label")
        plan = read_json(AI_ROOT / "configs" / "operational_lstm_plan.json")
        self.assertFalse(plan["model_training_performed"])
        self.assertEqual(len(plan["candidates"]), 2)
        self.assertEqual(plan["training"]["max_epochs"], 30)
        self.assertEqual(plan["preprocessing"]["fit_scope"], "unique_original_train_rows_only")

    def test_no_fit_prediction_test_jsonl_summary_or_network_access(self) -> None:
        original = Path.open

        def guarded(path, *args, **kwargs):
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv", "collection_summary.json"} or path.suffix == ".jsonl":
                raise AssertionError("Test·원본 JSONL·요약 접근 금지")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded), patch.object(Pipeline, "fit", side_effect=AssertionError("학습 금지")), \
                patch.object(Pipeline, "predict_proba", side_effect=AssertionError("예측 금지")), \
                patch.object(StandardScaler, "fit", side_effect=AssertionError("scaler fit 금지")), \
                patch("socket.create_connection", side_effect=AssertionError("트래픽 금지")):
            report = self.build()
        self.assertFalse(report["model_training_performed"])
        self.assertFalse(report["preprocessing_fitted"])
        self.assertFalse(report["test_files_read"])

    def test_all_anchors_labels_and_traces_survive_save_and_reload(self) -> None:
        report = self.build()
        data = load_operational_data(self.config.source_dataset_dir, self.config.source_dataset_id)
        for split in sequences.SPLITS:
            restored = sequences.load_split(self.config.output_dir, split)
            sequences.validate_windows(restored, getattr(data, split), 3, "fixture_sequence")
            self.assertEqual(report["splits"][split]["shape"], [6, 3, 7])
            self.assertEqual(report["splits"][split]["length_one_windows"], 4)
            self.assertEqual(report["splits"][split]["label_distribution"], {"0": 4, "1": 2})
        self.assertTrue(all(value == 0 for value in report["identity_overlaps"].values()))
        self.assertFalse((self.config.output_dir / "test.npz").exists())
        with self.assertRaises(ValueError):
            sequences.load_split(self.config.output_dir, "test")

    def test_missing_test_csv_does_not_block_build(self) -> None:
        for name in ("X_test.csv", "y_test.csv", "metadata_test.csv"):
            (self.config.source_dataset_dir / name).unlink()
        self.assertEqual(self.build()["splits"]["validation"]["windows"], 6)

    def test_window_protocol_is_locked_before_first_construction(self) -> None:
        original = sequences.make_windows
        calls = []

        def make(source, size, dataset_id):
            lock = read_json(self.config.output_dir / "window_protocol_lock.json")
            self.assertEqual(lock["status"], "locked_before_window_build")
            self.assertEqual(lock["protocol"]["window_size"], size)
            calls.append(source.metadata.iloc[0].split)
            return original(source, size, dataset_id)

        with patch.object(sequences, "make_windows", make):
            self.build()
        self.assertEqual(calls, ["train", "validation"])

    def test_existing_outputs_wrong_counts_and_immutable_source_are_protected(self) -> None:
        with self.assertRaisesRegex(ValueError, "행 수"):
            self.build(expected_train_rows=1538)
        with self.assertRaisesRegex(ValueError, "고정 원본"):
            self.build(output_dir=self.config.source_dataset_dir / "invalid")
        self.assertFalse(self.config.output_dir.exists())
        self.build()
        before = file_hash(self.config.output_dir / "sequence_manifest.json")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(before, file_hash(self.config.output_dir / "sequence_manifest.json"))

    def test_source_change_blocks_complete_manifest(self) -> None:
        original = sequences.make_windows

        def make(source, size, dataset_id):
            result = original(source, size, dataset_id)
            with (self.config.source_dataset_dir / "X_train.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return result

        with patch.object(sequences, "make_windows", make), self.assertRaisesRegex(ValueError, "원본 데이터"):
            self.build()
        self.assertFalse((self.config.output_dir / "sequence_manifest.json").exists())

    def test_pickle_archive_is_not_accepted(self) -> None:
        self.build()
        path = self.config.output_dir / "train.npz"
        with path.open("wb") as stream:
            np.savez_compressed(stream, X=np.array([{}], dtype=object), y=np.array([0]),
                                lengths=np.array([1]), mask=np.array([[True]]), source_positions=np.array([[0]]))
        with self.assertRaises(ValueError):
            sequences.load_split(self.config.output_dir, "train")

    def test_dataset_outputs_are_reproducible(self) -> None:
        first = self.build()
        second_config = self.config.model_copy(update={"output_dir": self.directory / "repeat"})
        second = sequences.build_dataset(second_config, self.directory / "repeat_report")
        for name in ("train.npz", "validation.npz", "metadata_train.csv", "metadata_validation.csv", "sequence_schema.json"):
            self.assertEqual(first["output_files"][name]["sha256"], second["output_files"][name]["sha256"])


if __name__ == "__main__":
    unittest.main()
