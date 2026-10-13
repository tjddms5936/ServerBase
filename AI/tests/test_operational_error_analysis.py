from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

import test_operational_dataset as dataset_fixtures
from data_preparation.operational_dataset import DatasetConfig, build_dataset, file_hash
import evaluation.analyze_operational_errors as analysis
from training.operational_data import load_operational_data
from training.run_operational_validation_experiment import ExperimentConfig, run_experiment


class OperationalErrorAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="operational_error_analysis_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        campaign = dataset_fixtures.make_campaign(self.directory)
        self.dataset_dir = self.directory / "dataset"
        build_dataset(DatasetConfig(dataset_id="fixture_v1", campaign_dirs=[campaign], output_dir=self.dataset_dir))
        self.experiment_dir = self.directory / "selection"
        config = ExperimentConfig(
            experiment_id="fixture_v1", dataset_id="fixture_v1", dataset_dir=self.dataset_dir,
            artifact_dir=self.experiment_dir, report_prefix=self.directory / "experiment_report",
            thresholds=[0.3, 0.5, 0.7], selection_rule={"max_false_positive_rate": 1.0, "min_recall": 0.5},
            candidates=[{"id": "lr_c_1", "kind": "logistic_regression", "C": 1.0}],
        )
        with contextlib.redirect_stdout(io.StringIO()):
            run_experiment(config)
        self.report_prefix = self.directory / "analysis_report"

    def analyze(self, **overrides) -> dict:
        values = dict(dataset_dir=self.dataset_dir, dataset_id="fixture_v1", experiment_dir=self.experiment_dir,
                      plan_path=analysis.DEFAULT_PLAN, report_prefix=self.report_prefix)
        values.update(overrides)
        return analysis.analyze(**values)

    def refresh_experiment_hash(self, name: str) -> None:
        path = self.experiment_dir / "experiment_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["artifacts"][name]["sha256"] = file_hash(self.experiment_dir / name)
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_analysis_never_fits_changes_selection_or_sends_traffic(self) -> None:
        paths = [path for path in self.experiment_dir.rglob("*") if path.is_file()]
        before = {path: file_hash(path) for path in paths}
        with patch.object(Pipeline, "fit", side_effect=AssertionError("분석 중 재학습 금지")), \
             patch("socket.create_connection", side_effect=AssertionError("분석 중 트래픽 송신 금지")):
            report = self.analyze()
        self.assertEqual(before, {path: file_hash(path) for path in paths})
        self.assertFalse(report["model_refit_performed"])
        self.assertFalse(report["threshold_changed"])
        self.assertEqual(report["augmentation_design"]["actual_new_campaigns_collected"], 0)

    def test_test_csv_and_raw_jsonl_are_not_opened(self) -> None:
        original = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.name in {"X_test.csv", "y_test.csv", "metadata_test.csv"} or path.suffix == ".jsonl":
                raise AssertionError("Test 또는 원본 JSONL 접근 금지")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            report = self.analyze()
        self.assertFalse(report["test_files_read"])
        self.assertFalse(report["raw_jsonl_read"])
        self.assertFalse(report["summary_used_as_input_or_label"])

    def test_analysis_works_when_test_csv_is_absent(self) -> None:
        for name in ("X_test.csv", "y_test.csv", "metadata_test.csv"):
            (self.dataset_dir / name).unlink()
        report = self.analyze()
        self.assertEqual(report["analysis_split"], "validation")

    def test_case_count_matches_matrix_and_csv_contains_trace_and_features(self) -> None:
        report = self.analyze()
        matrix = report["validation_metrics"]["confusion_matrix"]
        self.assertEqual(len(report["error_cases"]), matrix["fp"] + matrix["fn"])
        csv = pd.read_csv(self.report_prefix.with_suffix(".csv"))
        self.assertEqual(len(csv), len(report["error_cases"]))
        self.assertTrue({"row_id", "run_id", "session_id", "request_index", "source_line", "threshold",
                         "packet_size", "payload_size", "error_count_total"}.issubset(csv.columns))
        self.assertEqual(set(csv["split"]), {"validation"})
        self.assertEqual(file_hash(self.report_prefix.with_suffix(".csv")), report["error_csv"]["sha256"])

    def test_contribution_sum_and_nearest_opposite_example_are_consistent(self) -> None:
        report = self.analyze()
        self.assertTrue(report["error_cases"])
        for case in report["error_cases"]:
            score = sum(case["feature_contributions"].values()) + case["lr_intercept"]
            self.assertAlmostEqual(score, case["lr_score"])
            self.assertAlmostEqual(1 / (1 + np.exp(-score)), case["anomaly_probability"])
            self.assertNotEqual(case["label"], case["nearest_opposite_label_train_example"]["label"])
            self.assertAlmostEqual(case["threshold_logit"], np.log(case["threshold"] / (1 - case["threshold"])))

    def test_representation_counterexample_is_not_claimed_as_captured_pair(self) -> None:
        report = self.analyze()
        probe = report["representation_probe"]
        self.assertEqual(probe["probe_kind"], "constructed_representation_counterexample_not_captured_traffic")
        self.assertTrue(probe["wire_size_equal"])
        self.assertFalse(probe["wire_bytes_equal"])
        self.assertTrue(probe["same_model_output"])
        self.assertEqual(probe["normal_declared_text_length"], probe["available_text_bytes_after_fixed_fields"])
        self.assertGreater(probe["malformed_declared_text_length"], probe["available_text_bytes_after_fixed_fields"])

    def test_empty_error_set_is_supported(self) -> None:
        data = load_operational_data(self.dataset_dir, "fixture_v1")
        pipeline = joblib.load(self.experiment_dir / "candidates" / "lr_c_1" / "pipeline.joblib")
        probability = np.where(data.validation.y == 1, 0.9, 0.1)
        csv, cases = analysis.error_cases(data, pipeline, 0.5, probability)
        self.assertEqual(cases, [])
        self.assertTrue(csv.empty)
        self.assertIn("row_id", csv.columns)

    def test_observed_conflicting_vectors_are_separate_from_probe(self) -> None:
        report = self.analyze()
        # 단위 테스트 원본에는 동일 입력·다른 정답이 있다. 실제 캠페인의 0개 충돌을 하드코딩하지 않는다.
        self.assertGreater(report["feature_diagnostics"]["observed_exact_feature_conflicting_label_groups"], 0)
        self.assertIn("error_count_total", report["feature_diagnostics"]["constant_train_features"])
        self.assertTrue(report["feature_diagnostics"]["packet_payload_difference_always_8"])

    def test_augmentation_plan_has_campaign_level_holdout_and_bounded_budget(self) -> None:
        report = self.analyze()
        design = report["augmentation_design"]
        self.assertEqual(design["status"], "design_only")
        self.assertEqual(design["planned_campaigns"], 6)
        self.assertEqual(design["planned_runs"], 84)
        self.assertEqual(design["planned_sessions_including_header_rejection"], 336)
        self.assertEqual(design["planned_message_observable_sessions"], 288)
        self.assertEqual(design["planned_header_rejection_runs"], 12)
        self.assertEqual([slot["split"] for slot in design["plan"]["campaign_slots"]], ["train", "train", "train", "validation", "test", "test"])

    def test_invalid_design_seed_or_external_destination_is_rejected(self) -> None:
        selection = json.loads((self.experiment_dir / "selection.json").read_text(encoding="utf-8"))
        for change in ("duplicate_seed", "external_destination", "auto_collect", "missing_holdout"):
            plan = json.loads(analysis.DEFAULT_PLAN.read_text(encoding="utf-8"))
            if change == "duplicate_seed":
                plan["campaign_slots"][1]["seed"] = plan["campaign_slots"][0]["seed"]
            elif change == "external_destination":
                plan["safety_limits"]["destination"] = "example.com:4000"
            elif change == "auto_collect":
                plan["safety_limits"]["auto_collect_now"] = True
            else:
                for slot in plan["campaign_slots"]:
                    slot["split"] = "train"
            path = self.directory / f"plan_{change}.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.subTest(change=change), self.assertRaises(ValueError):
                analysis.augmentation_design(path, selection)

    def test_tampered_selected_model_is_rejected_without_outputs(self) -> None:
        with (self.experiment_dir / "candidates" / "lr_c_1" / "pipeline.joblib").open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.analyze()
        self.assertFalse(self.report_prefix.with_suffix(".csv").exists())

    def test_modified_threshold_is_rejected_even_after_refreshing_outer_hash(self) -> None:
        path = self.experiment_dir / "selection.json"
        selection = json.loads(path.read_text(encoding="utf-8"))
        selection["threshold"] = 0.7
        path.write_text(json.dumps(selection), encoding="utf-8")
        self.refresh_experiment_hash(path.name)
        with self.assertRaisesRegex(ValueError, "threshold"):
            self.analyze()

    def test_reordered_selected_csv_is_rejected(self) -> None:
        path = self.experiment_dir / "selected_validation_predictions.csv"
        pd.read_csv(path).iloc[::-1].to_csv(path, index=False)
        self.refresh_experiment_hash(path.name)
        with self.assertRaisesRegex(ValueError, "행 순서"):
            self.analyze()

    def test_selected_path_cannot_escape_experiment_directory(self) -> None:
        with self.assertRaises(ValueError):
            analysis.inside_artifact(self.experiment_dir, "../outside.joblib")

    def test_existing_reports_and_immutable_inputs_are_protected(self) -> None:
        self.analyze()
        before = file_hash(self.report_prefix.with_suffix(".json"))
        with self.assertRaises(FileExistsError):
            self.analyze()
        self.assertEqual(before, file_hash(self.report_prefix.with_suffix(".json")))
        with self.assertRaisesRegex(ValueError, "내부에 분석 결과"):
            self.analyze(report_prefix=self.experiment_dir / "invalid_report")


if __name__ == "__main__":
    unittest.main()
