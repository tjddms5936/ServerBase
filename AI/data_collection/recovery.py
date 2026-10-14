from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from data_collection.augmentation import summarize_controls, summarize_quality, verify_hashes
from data_collection.campaign import file_hash, prepare_campaign, run_campaign, validate_capture_paths, write_json
from data_collection.config import AI_ROOT, Identifier
from data_collection.quality import audit_campaign, read_json, save_quality_report
from data_collection.recovery_design import compile_recovery_plans, summarize_recovery_design
from data_collection.runtime import ensure_ports_free
from data_collection.traffic import CampaignPlan
from data_preparation.operational_dataset import DatasetConfig, build_dataset, save_report


def require_new_outputs(dataset_output: Path, report_prefix: Path, dataset_report_prefix: Path) -> None:
    files = [prefix.with_suffix(suffix).resolve() for prefix in (report_prefix, dataset_report_prefix)
             for suffix in (".json", ".md")]
    if len(set(files)) != 4:
        raise ValueError("수집 보고서와 데이터셋 보고서는 서로 다른 경로여야 합니다.")
    if dataset_output.exists() or any(path.exists() for path in files):
        raise FileExistsError("기존 데이터셋·보고서를 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.")
    if any(path.is_relative_to(dataset_output.resolve()) for path in files):
        raise ValueError("보고서는 데이터셋 폴더 밖에 저장해야 합니다.")


def freeze_recovery_reference(final_report: Path, diagnosis_report: Path, design_path: Path) -> dict:
    final, diagnosis = read_json(final_report), read_json(diagnosis_report)
    if (final["status"] != "complete" or final["selected_model_id"] != "selected_rf" or
            final["approval"]["status"] != "withheld_after_test" or diagnosis["status"] != "complete" or
            diagnosis["candidate_id"] != "rf_leaf_5" or diagnosis["threshold"] != 0.3 or
            diagnosis["recovery_plan_sha256"] != file_hash(design_path) or
            diagnosis["previous_test_evidence"]["sha256"] != file_hash(final_report)):
        raise ValueError("완료 진단·승인 보류 근거·고정 보정 계획이 다릅니다.")
    descriptor = final["models"]["selected_rf"]
    if descriptor["candidate_id"] != "rf_leaf_5" or descriptor["threshold"] != 0.3:
        raise ValueError("보정 수집의 기준 RF 정책이 다릅니다.")
    # 학습 모델을 역직렬화하거나 예측하지 않고 파일·정책·이전 진단 근거만 잠근다.
    hashes = dict(diagnosis["protected_sha256"])
    hashes.update({str(Path(item["path"]).resolve()): item["sha256"] for item in descriptor["files"].values()})
    hashes.update({str(final_report.resolve()): file_hash(final_report),
                   str(diagnosis_report.resolve()): file_hash(diagnosis_report)})
    verify_hashes(hashes)
    return {"candidate_id": "rf_leaf_5", "kind": "random_forest", "threshold": 0.3,
            "deployment_approval": "withheld_after_test", "previous_test_exposed": True,
            "model_sha256": descriptor["files"]["model"]["sha256"], "file_sha256": hashes,
            "model_loaded": False, "prediction_performed": False, "fit_performed": False}


def prepare_recovery_batch(design_path: Path, previous_plan: Path, batch_id: str, output_dir: Path,
                           server_exe: Path, scenario_manifest: Path, dataset_output: Path,
                           report_prefix: Path, dataset_report_prefix: Path,
                           final_report: Path, diagnosis_report: Path) -> Path:
    TypeAdapter(Identifier).validate_python(batch_id)
    require_new_outputs(dataset_output, report_prefix, dataset_report_prefix)
    design_path, previous_plan = design_path.resolve(), previous_plan.resolve()
    design = read_json(design_path)
    plans = compile_recovery_plans(design, read_json(previous_plan))
    reference = freeze_recovery_reference(final_report.resolve(), diagnosis_report.resolve(), design_path)
    if not server_exe.is_file():
        raise FileNotFoundError("수집에 사용할 Server.exe가 없습니다.")
    root = output_dir.resolve() / batch_id
    if dataset_output.resolve().is_relative_to(root) or any(
            prefix.resolve().is_relative_to(root) for prefix in (report_prefix, dataset_report_prefix)):
        raise ValueError("데이터셋과 보고서는 원본 batch 밖에 저장해야 합니다.")
    # 긴 역할 이름은 registry에 보존하고 파일 경로에는 짧은 캠페인 id를 쓴다.
    entries = [{**slot, "campaign_id": f"{batch_id}_c{index}",
                "campaign_dir": str(root / f"{batch_id}_c{index}"), "plan_snapshot": plan.model_dump(mode="json")}
               for index, (slot, plan) in enumerate(plans)]
    for entry, (_, plan) in zip(entries, plans):
        validate_capture_paths(Path(entry["campaign_dir"]), entry["campaign_id"], plan)
    locked = {str(path.resolve()): file_hash(path) for path in
              (design_path, previous_plan, server_exe, scenario_manifest, AI_ROOT / "configs" / "operational_feature_schema.json")}
    locked.update(reference["file_sha256"])
    source_files = list((AI_ROOT / "data_collection").glob("*.py")) + list((AI_ROOT / "inference_api").glob("*.py"))
    source_files += [AI_ROOT / "scripts" / "collect_operational_recovery.py",
                     AI_ROOT / "data_preparation" / "operational_dataset.py",
                     AI_ROOT / "data_preparation" / "operational_prefix_features.py"]
    locked.update({str(path.resolve()): file_hash(path) for path in source_files})
    root.mkdir(parents=True, exist_ok=False)
    registry_path = root / "assignment_manifest.json"
    try:
        # 역할·실제 id·출력 위치·실행 코드·정답을 먼저 고정하고 그 뒤 기대값을 생성한다.
        write_json(registry_path, {
            "assignment_version": "v1", "batch_kind": "recovery_v1", "batch_id": batch_id,
            "locked_at_utc": datetime.now(UTC).isoformat(), "prepared_before_collection": True,
            "split_policy": design["split_policy"], "feature_contract_id": design["raw_feature_contract_id"],
            "target_dataset_id": design["target_dataset_id"], "campaigns": entries,
            "reference_baseline": reference, "recovery_plan_snapshot": design,
            "planned_summary": summarize_recovery_design(design, plans), "locked_file_sha256": locked,
            "design_path": str(design_path), "design_sha256": file_hash(design_path),
            "server_exe": str(server_exe.resolve()), "outputs": {
                "dataset_output": str(dataset_output.resolve()), "report_prefix": str(report_prefix.resolve()),
                "dataset_report_prefix": str(dataset_report_prefix.resolve())},
        })
        capture_hashes = {}
        for entry, (_, plan) in zip(entries, plans):
            campaign_root = prepare_campaign(plan, entry["campaign_id"], root, server_exe, scenario_manifest)
            manifest_path = campaign_root / "campaign_manifest.json"
            manifest = read_json(manifest_path)
            capture_hashes[str(manifest_path)] = file_hash(manifest_path)
            for run in manifest["runs"]:
                for name in ("config_file", "expectations_file"):
                    path = campaign_root / run[name]
                    capture_hashes[str(path)] = file_hash(path)
        verify_hashes(locked)
        write_json(root / "prepared_manifest.json", {
            "status": "prepared", "assignment_sha256": file_hash(registry_path),
            "prepared_at_utc": datetime.now(UTC).isoformat(), "capture_input_sha256": capture_hashes,
            "collection_started": False,
        })
        return root
    except BaseException as error:
        write_json(root / "preparation_failure.json", {"status": "failed", "error": f"{type(error).__name__}: {error}"})
        raise


def recovery_coverage(registry: dict) -> dict:
    coverage = {}
    expected = {(batch, low, high) for batch in (1, 4, 8) for low, high in ((1, 4), (5, 15), (20, 40))}
    for entry in registry["campaigns"]:
        plan = CampaignPlan.model_validate(entry["plan_snapshot"])
        observed = set()
        for repeat in range(plan.repeats):
            profiles = {profile.scenario_id: profile for profile in plan.profiles_for_repeat(repeat)}
            normal, burst = profiles["normal_batched_chat"], profiles["controlled_burst_chat"]
            if (any(getattr(normal, name) != getattr(burst, name) for name in
                    ("sessions", "concurrency", "messages_min", "messages_max", "text_sizes", "batch_size")) or
                    (burst.interval_min_ms, burst.interval_max_ms) != (0, 0)):
                raise ValueError("정상 묶음과 burst의 비교 조건이 계획과 다릅니다.")
            observed.add((normal.batch_size, normal.interval_min_ms, normal.interval_max_ms))
        if observed != expected:
            raise ValueError("캠페인의 정상 묶음 9조합이 누락되거나 바뀌었습니다.")
        coverage[entry["campaign_id"]] = {"slot_id": entry["slot_id"], "split": entry["split"],
                                           "normal_batch_pacing_combinations": sorted(observed),
                                           "matched_burst_controls": True}
    return coverage


def verify_collected_counts(registry: dict, quality: dict, dataset: dict) -> None:
    planned = registry["planned_summary"]
    if (not quality["passed"] or quality["runs"] != planned["planned_runs"] or
            quality["passed_runs"] != planned["planned_runs"] or
            quality["totals"]["sessions"] != planned["planned_sessions"] or
            quality["totals"]["summaries"] != planned["planned_sessions"]):
        raise ValueError("실제 수집 run·세션·종료 요약이 사전 계획과 다릅니다.")
    for name, expected in planned["splits"].items():
        actual = dataset["splits"][name]
        if actual["rows"] != expected["expected_message_rows"] or actual["campaigns"] != expected["campaigns"]:
            raise ValueError(f"{name}의 실제 메시지·캠페인 수가 사전 계획과 다릅니다.")
    if quality["totals"]["messages"] != sum(item["rows"] for item in dataset["splits"].values()):
        raise ValueError("수집 품질 결과와 데이터셋의 메시지 수가 다릅니다.")


def render_report(report: dict) -> str:
    quality = report["quality"]
    lines = ["# 보정 캠페인 실제 수집·품질 검증·분할", "", "## 완료 결과", "",
             f"batch: `{report['batch_id']}`", f"원본: `{report['batch_path']}`", "",
             f"독립 캠페인 6개, run {quality['passed_runs']}/{quality['runs']}개 통과, 세션 {quality['totals']['sessions']}개를 실제 수집했다.",
             f"메시지 {quality['totals']['messages']}건·종료 요약 {quality['totals']['summaries']}건이며 정답은 송신 전 독립 시나리오 manifest를 유지했다.",
             "", "기존 RF는 파일·threshold를 해시로 고정했지만 로드하거나 예측하지 않았다. FastAPI는 dummy 응답이며 이 수치는 탐지 성능이 아니라 수집 품질이다.",
             "", "## 송신 전에 고정한 역할", "", "| 슬롯 | 실제 캠페인 | seed | 분할 |", "| --- | --- | ---: | --- |"]
    for entry in report["assignment"]["campaigns"]:
        lines.append(f"| {entry['slot_id']} | {entry['campaign_id']} | {entry['seed']} | {entry['split']} |")
    lines += ["", "한 캠페인의 모든 반복·run·세션·메시지는 같은 split에 속한다. 파일 경로용 짧은 캠페인 id와 설계의 역할 이름은 registry로 연결한다.",
              "", "## 수집 품질", ""]
    for name, value in quality["totals"].items():
        lines.append(f"- `{name}`: {value}")
    lines += ["", "## 메시지 데이터셋", "", "| 분할 | 캠페인 | 메시지 run | 메시지 세션 | 메시지 | 정상 | 통제된 이상 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, counts in report["splits"].items():
        lines.append(f"| {name} | {counts['campaigns']} | {counts['runs']} | {counts['sessions']} | {counts['rows']} | {counts['label_distribution']['0']} | {counts['label_distribution']['1']} |")
    lines += ["", f"데이터셋: `{report['dataset_output']}`", "",
              f"헤더 거부 run {report['excluded_runs']}개는 기대 메시지가 0건이므로 별도 coverage로 남겼다. 종료 요약을 가짜 메시지나 학습 정답으로 바꾸지 않았다.",
              "", "## 분할과 중복 검증", ""]
    for pair, counts in report["identity_overlaps"].items():
        lines.append(f"- `{pair}`: {counts}")
    lines += [f"- 동일 feature 값 진단: {report['duplicate_features']}", "",
              "동일 feature 값은 같은 세션·행 식별자 중복과 구분하며 결과를 보고 행 삭제·정답 교체를 하지 않았다.",
              "", "## 제어 조건과 관측 범위", "",
              "각 캠페인의 정상 묶음 크기 1/4/8 × 정상 pacing 1~4/5~15/20~40ms를 9조합으로 실행했다. 동일 반복 burst는 같은 크기·개수 범위·묶음·동시성으로 지정 대기만 0ms다.",
              "", "실제 sendall 호출 수·묶음 구성·송신 바이트·응답·종료 및 수집 누락을 run별 품질 검사로 확인했다. 이 교차는 묶음 크기와 pacing에 대한 것이며 모든 동시성·크기까지 완전 교차한 실험은 아니다.",
              "", report["generator_controls"]["interpretation"],
              "", "Train/Validation의 송신·파싱 범위만 진단했다. 새 Test 원본은 수집 품질·추출·분할·해시 검사를 위해 읽었지만 Test feature 범위 분석·모델 예측·threshold 선정에는 사용하지 않았다.",
              "", "## 보호와 한계", "",
              "계획·독립 정답·역할·출력 경로·모델·빌드·실행 코드·각 run 설정/기대값을 송신 전에 잠갔다. 전체 원본과 출력의 해시를 완료 manifest에 보관했다. 실패 batch는 보존하고 자동 재송신하거나 통과 run만 골라 데이터셋을 승인하지 않는다.",
              "", "이전 보정 설계·진단·최종 평가·모델·데이터셋은 덮어쓰지 않았다. 기존 Test는 이미 노출된 진단 자료이며 이번 새 Test와 구분한다. 새 seed의 같은 생성기·장비 실험이므로 실제 외부 침입이나 운영 일반화를 보장하지 않는다.",
              "", "## 다음 작업과 남은 단계", "",
              "다음은 새 Train 전용 RF 후보 4개 학습과 새 Validation의 20개 조합 비교다. 이후 비용 확인·후보 잠금·새 Test 승인이 끝나야 보정 묶음이 완료된다.",
              "", "보정 묶음은 수집·분할까지 진행 중이므로 FastAPI 실제 모델 연결·TCP 통합·최종 문서화를 포함한 큰 작업 묶음은 아직 4개 남아 있다.", ""]
    return "\n".join(lines)


def collect_recovery_batch(root: Path, server_exe: Path) -> dict:
    root = root.resolve()
    if (root / "batch_runtime.json").exists():
        raise FileExistsError("이미 시도한 batch는 재송신하지 않습니다. 실패 원본을 보존하고 새 batch를 준비하세요.")
    registry_path, prepared_path = root / "assignment_manifest.json", root / "prepared_manifest.json"
    registry, prepared = read_json(registry_path), read_json(prepared_path)
    if registry.get("batch_kind") != "recovery_v1" or prepared.get("status") != "prepared":
        raise ValueError("보정 수집을 위해 모두 준비한 batch가 필요합니다.")
    if str(server_exe.resolve()) != registry["server_exe"]:
        raise ValueError("수집 전에 고정한 Server.exe 경로를 유지해야 합니다.")
    output = registry["outputs"]
    dataset_output, report_prefix, dataset_report_prefix = (Path(output[name]) for name in
                                                            ("dataset_output", "report_prefix", "dataset_report_prefix"))
    require_new_outputs(dataset_output, report_prefix, dataset_report_prefix)
    fixed = {str(registry_path): prepared["assignment_sha256"], str(prepared_path): file_hash(prepared_path),
             **registry["locked_file_sha256"], **prepared["capture_input_sha256"]}
    runtime = {"batch_id": registry["batch_id"], "status": "running", "started_at_utc": datetime.now(UTC).isoformat(),
               "executed_campaigns": [], "error": None}
    reports = []
    try:
        verify_hashes(fixed)
        ensure_ports_free()
        coverage = recovery_coverage(registry)
        write_json(root / "batch_runtime.json", runtime)
        for entry in registry["campaigns"]:
            verify_hashes(fixed)
            campaign_root = Path(entry["campaign_dir"])
            print(f"보정 캠페인 시작: {entry['slot_id']} / {entry['split']} / seed={entry['seed']}", flush=True)
            executed = run_campaign(campaign_root, server_exe)
            checked = audit_campaign(campaign_root)
            save_quality_report(checked, campaign_root / "quality_report")
            reports.append(checked)
            runtime["executed_campaigns"].append({"campaign_id": entry["campaign_id"],
                                                  "runtime_passed": executed["passed"], "quality_passed": checked["passed"]})
            write_json(root / "batch_runtime.json", runtime)
            if not executed["passed"] or not checked["passed"]:
                raise RuntimeError(f"보정 캠페인 수집·품질 검증 실패: {entry['slot_id']}. 실패 run을 골라 제외하지 않습니다.")
            print(f"보정 캠페인 품질 PASS: {entry['slot_id']} ({checked['passed_runs']}/{checked['run_count']} run)", flush=True)
        verify_hashes(fixed)
        quality = summarize_quality(registry, reports)
        config = DatasetConfig(config_version="v2", dataset_id=registry["target_dataset_id"],
                               campaign_dirs=[Path(entry["campaign_dir"]) for entry in registry["campaigns"]],
                               output_dir=dataset_output, split_policy="preassigned_campaign_holdout", campaign_registry=registry_path)
        write_json(root / "dataset_config.json", config.model_dump(mode="json"))
        dataset = build_dataset(config)
        verify_collected_counts(registry, quality, dataset)
        verify_hashes(fixed)
        save_report(dataset, dataset_report_prefix)
        report = {"status": "complete", "batch_id": registry["batch_id"], "batch_path": str(root),
                  "generated_at_utc": datetime.now(UTC).isoformat(), "assignment": registry,
                  "assignment_sha256": file_hash(registry_path), "quality": quality,
                  "dataset_output": str(dataset_output), "dataset_manifest_sha256": file_hash(dataset_output / "dataset_manifest.json"),
                  "splits": dataset["splits"], "identity_overlaps": dataset["identity_overlaps"],
                  "duplicate_features": dataset["duplicate_features"], "excluded_runs": len(dataset["excluded_sources"]),
                  "recovery_coverage": coverage, "generator_controls": summarize_controls(registry, dataset_output),
                  "preprocessing_fitted": False, "model_training_performed": False,
                  "trained_model_prediction_performed": False, "test_model_evaluated": False,
                  "test_feature_ranges_analyzed": False, "test_quality_checked": True,
                  "legacy_baseline_unchanged": True, "fastapi_still_dummy": True, "deployment_approved": False,
                  "destination": "127.0.0.1:4000"}
        report_prefix.parent.mkdir(parents=True, exist_ok=True)
        write_json(report_prefix.with_suffix(".json"), report)
        report_prefix.with_suffix(".md").write_text(render_report(report), encoding="utf-8")
        outputs = {str(prefix.with_suffix(suffix)): file_hash(prefix.with_suffix(suffix))
                   for prefix in (report_prefix, dataset_report_prefix) for suffix in (".json", ".md")}
        outputs[str(dataset_output / "dataset_manifest.json")] = file_hash(dataset_output / "dataset_manifest.json")
        outputs.update({str(dataset_output / name): description["sha256"] for name, description in dataset["output_files"].items()})
        verify_hashes(fixed)
        verify_hashes(dataset["source_file_sha256"])
        verify_hashes(outputs)
        write_json(root / "completion_manifest.json", {"status": "complete", "locked_input_sha256": fixed,
                   "source_file_sha256": dataset["source_file_sha256"], "output_sha256": outputs,
                   "completed_at_utc": datetime.now(UTC).isoformat(), "model_fit_performed": False,
                   "trained_model_prediction_performed": False, "test_model_evaluated": False})
        runtime["status"] = "complete"
        return report
    except BaseException as error:
        runtime.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        runtime["finished_at_utc"] = datetime.now(UTC).isoformat()
        write_json(root / "batch_runtime.json", runtime)
