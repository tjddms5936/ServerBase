from __future__ import annotations

import random
import csv
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from .campaign import file_hash, prepare_campaign, run_campaign, write_json, validate_capture_paths
from .config import AI_ROOT, Identifier
from .quality import inside_path, read_json, audit_campaign, save_quality_report
from .runtime import ensure_ports_free
from .traffic import CampaignPlan, TrafficProfile, SCENARIO_LABELS, planned_runs
from data_preparation.operational_dataset import DatasetConfig, build_dataset, save_report


def compile_plans(design: dict) -> list[tuple[dict, CampaignPlan]]:
    slots = design["campaign_slots"]
    if (design["plan_version"] != "v1" or design["campaign_split_policy"] != "preassigned_campaign_holdout" or
            design["feature_contract_id"] != "server_application_message_v2" or
            design["freeze_reference_before_collection"] is not True or len(slots) != 6 or
            len({slot["slot_id"] for slot in slots}) != 6 or len({slot["seed"] for slot in slots}) != 6 or
            Counter(slot["split"] for slot in slots) != {"train": 3, "validation": 1, "test": 2} or
            len(design["scenario_ids"]) != 7 or set(design["scenario_ids"]) != set(SCENARIO_LABELS) or
            design["repeats_per_campaign"] != 2 or design["sessions_per_run"] != 4):
        raise ValueError("설계는 독립 seed의 6개 캠페인, 3/1/2 분할, 7개 시나리오와 2회 반복이어야 합니다.")
    safety = design["safety_limits"]
    if (safety["destination"] != "127.0.0.1:4000" or safety["external_traffic_allowed"] is not False or
            safety["max_messages_per_session"] != 24 or safety["max_sessions_per_run"] != 4 or
            safety["max_concurrency"] != 4 or safety["max_text_bytes"] != 768):
        raise ValueError("로컬 수집 안전 한도를 확대하지 않습니다.")
    controls = design["planned_generator_controls"]
    expected = {
        "text_sizes": [8, 24, 96, 384, 768], "normal_message_count_range": [6, 24],
        "burst_message_count_range": [12, 24], "normal_wait_ms_ranges": [[1, 4], [5, 15], [20, 40]],
        "burst_intentional_wait_ms": 0, "batch_sizes": [1, 4, 8],
        "fragment_sizes_bytes": [7, 19, 31], "concurrency_levels": [1, 2, 4],
    }
    if any(controls[name] != value for name, value in expected.items()):
        raise ValueError("이번 수집은 문서에서 사전 설계한 제한된 제어 값만 실행합니다.")
    plans = []
    for slot_index, slot in enumerate(slots):
        TypeAdapter(Identifier).validate_python(slot["slot_id"])
        repeats = []
        for repeat in range(2):
            profiles = []
            for scenario_index, scenario in enumerate(design["scenario_ids"]):
                level = (slot_index + repeat + scenario_index) % 3
                burst = scenario == "controlled_burst_chat"
                count = controls["burst_message_count_range" if burst else "normal_message_count_range"]
                wait = [0, 0] if burst else controls["normal_wait_ms_ranges"][level]
                profiles.append(TrafficProfile(
                    scenario_id=scenario, sessions=4, concurrency=controls["concurrency_levels"][level],
                    messages_min=count[0], messages_max=count[1], text_sizes=controls["text_sizes"],
                    interval_min_ms=wait[0], interval_max_ms=wait[1],
                    batch_size=controls["batch_sizes"][(level + 1) % 3]
                    if scenario in {"normal_batched_chat", "controlled_burst_chat"} else 1,
                    fragment_size_bytes=controls["fragment_sizes_bytes"][(level + 2) % 3]
                    if scenario == "normal_fragmented_chat" else None,
                ))
            # 순서와 송신 내용 난수를 분리하여 순서 변경이 프레임 내용을 바꾸지 않게 한다.
            random.Random(slot["seed"] * 1009 + repeat).shuffle(profiles)
            repeats.append(profiles)
        plans.append((slot, CampaignPlan(plan_version="v2", seed=slot["seed"], repeats=2,
                                        profiles=repeats[0], repeat_profiles=repeats)))
    seeds = [seed for _, plan in plans for _, _, seed in planned_runs(plan)]
    if len(seeds) != len(set(seeds)):
        raise ValueError("서로 다른 run의 파생 seed가 충돌했습니다.")
    return plans


def freeze_reference(selection_path: Path) -> dict:
    selection_path = selection_path.resolve()
    root = selection_path.parent
    selection = read_json(selection_path)
    manifest_path = root / "experiment_manifest.json"
    manifest = read_json(manifest_path)
    if (manifest["status"] != "complete" or selection["status"] != "selected" or
            selection["feature_contract_id"] != "server_application_message_v2" or
            selection["threshold"] != selection["selected_trial"]["threshold"] or
            selection["test_evaluated"] is not False):
        raise ValueError("수집 전에 고정할 선정 모델의 상태·계약·threshold가 올바르지 않습니다.")
    paths = [selection_path]
    for key in ("model_path", "feature_schema_path", "label_map_path"):
        paths.append(inside_path(root, selection[key]))
    hashes = {}
    for path in paths:
        digest = file_hash(path)
        if manifest["artifacts"][path.relative_to(root).as_posix()]["sha256"] != digest:
            raise ValueError("선정 모델 산출물의 해시가 완료 manifest와 다릅니다.")
        hashes[str(path)] = digest
    model_path = inside_path(root, selection["model_path"])
    if file_hash(model_path) != selection["model_sha256"]:
        raise ValueError("선정 모델의 SHA-256이 다릅니다.")
    hashes[str(manifest_path)] = file_hash(manifest_path)
    return {
        "candidate_id": selection["selected_trial"]["candidate_id"],
        "kind": selection["selected_trial"]["kind"], "threshold": selection["threshold"],
        "model_sha256": selection["model_sha256"], "file_sha256": hashes,
        "model_loaded": False, "prediction_performed": False, "fit_performed": False,
    }


def verify_hashes(hashes: dict[str, str]) -> None:
    for path, digest in hashes.items():
        if not Path(path).is_file() or file_hash(Path(path)) != digest:
            raise ValueError(f"준비 이후 고정 파일이 변경되었습니다: {path}")


def prepare_batch(design_path: Path, batch_id: str, output_dir: Path, server_exe: Path,
                  scenario_manifest: Path) -> Path:
    TypeAdapter(Identifier).validate_python(batch_id)
    design_path = design_path.resolve()
    design = read_json(design_path)
    plans = compile_plans(design)
    reference = freeze_reference(design_path.parent / design["reference_selection"])
    root = output_dir.resolve() / batch_id
    entries = [{**slot, "campaign_id": f"{batch_id}_{slot['slot_id']}",
                "campaign_dir": str(root / f"{batch_id}_{slot['slot_id']}"),
                "plan_snapshot": plan.model_dump(mode="json")} for slot, plan in plans]
    for entry, (_, plan) in zip(entries, plans):
        TypeAdapter(Identifier).validate_python(entry["campaign_id"])
        validate_capture_paths(Path(entry["campaign_dir"]), entry["campaign_id"], plan)
    locked = {str(design_path): file_hash(design_path), str(server_exe.resolve()): file_hash(server_exe),
              str(scenario_manifest.resolve()): file_hash(scenario_manifest), **reference["file_sha256"]}
    for path in list((AI_ROOT / "data_collection").glob("*.py")) + [Path(__file__).resolve(),
            AI_ROOT / "data_preparation" / "operational_dataset.py", AI_ROOT / "configs" / "operational_feature_schema.json"]:
        locked[str(path)] = file_hash(path)
    root.mkdir(parents=True, exist_ok=False)
    # 전체 역할·계획을 먼저 저장한다. 이후 실패해도 seed나 분할을 결과에 맞춰 교체하지 않는다.
    registry = root / "assignment_manifest.json"
    write_json(registry, {
        "assignment_version": "v1", "batch_id": batch_id, "locked_at_utc": datetime.now(UTC).isoformat(),
        "prepared_before_collection": True, "split_policy": "preassigned_campaign_holdout",
        "feature_contract_id": design["feature_contract_id"], "target_dataset_id": design["target_dataset_id"],
        "campaigns": entries, "reference_baseline": reference, "locked_file_sha256": locked,
        "design_path": str(design_path), "design_sha256": file_hash(design_path),
    })
    for entry, (_, plan) in zip(entries, plans):
        prepare_campaign(plan, entry["campaign_id"], root, server_exe, scenario_manifest)
    write_json(root / "prepared_manifest.json", {
        "assignment_sha256": file_hash(registry), "prepared_at_utc": datetime.now(UTC).isoformat(),
        "campaign_manifest_sha256": {str(Path(entry["campaign_dir"]) / "campaign_manifest.json"):
                                    file_hash(Path(entry["campaign_dir"]) / "campaign_manifest.json") for entry in entries},
    })
    return root


def summarize_quality(registry: dict, reports: list[dict]) -> dict:
    if len(reports) != 6 or {report["campaign_id"] for report in reports} != {entry["campaign_id"] for entry in registry["campaigns"]}:
        raise ValueError("사전에 지정한 6개 캠페인의 품질 보고서가 모두 필요합니다.")
    totals = {name: sum(report["totals"][name] for report in reports) for name in reports[0]["totals"]}
    return {
        "passed": all(report["passed"] for report in reports), "campaigns": 6,
        "runs": sum(report["run_count"] for report in reports),
        "passed_runs": sum(report["passed_runs"] for report in reports),
        "eligible_message_runs": sum(report["eligible_runs"] for report in reports), "totals": totals,
        "validated_label_distribution": {unit: {label: sum(report["validated_label_distribution"][unit][label]
                                                        for report in reports) for label in ("0", "1")}
                                         for unit in reports[0]["validated_label_distribution"]},
        "campaign_results": [{"campaign_id": report["campaign_id"], "passed": report["passed"],
                              "totals": report["totals"], "quality_report": str(Path(report["campaign_path"]) / "quality_report.json")}
                             for report in reports],
    }


def summarize_controls(registry: dict, dataset_output: Path) -> dict:
    coverage = {}
    for entry in registry["campaigns"]:
        plan = CampaignPlan.model_validate(entry["plan_snapshot"])
        for _, profile, _ in planned_runs(plan):
            key = f"{entry['split']}:{profile.scenario_id}"
            values = coverage.setdefault(key, {"concurrency": set(), "batch_size": set(), "fragment_size_bytes": set(),
                                               "wait_range_ms": set()})
            values["concurrency"].add(profile.concurrency)
            values["batch_size"].add(profile.batch_size)
            if profile.fragment_size_bytes is not None:
                values["fragment_size_bytes"].add(profile.fragment_size_bytes)
            values["wait_range_ms"].add((profile.interval_min_ms, profile.interval_max_ms))
    coverage = {key: {name: sorted(values) for name, values in fields.items()} for key, fields in coverage.items()}
    observed = {}
    # Test는 품질·분할만 확인한다. 관측 값 범위 탐색은 Train/Validation으로 한정한다.
    for split in ("train", "validation"):
        with (dataset_output / f"X_{split}.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        by_scenario = {}
        with (dataset_output / f"metadata_{split}.csv").open(encoding="utf-8", newline="") as stream:
            metadata = list(csv.DictReader(stream))
        for row, meta in zip(rows, metadata):
            fields = by_scenario.setdefault(meta["scenario_id"], {name: [] for name in (
                "packet_size", "connection_duration_ms", "message_interval_ms")})
            for name in fields:
                fields[name].append(float(row[name]))
        intervals, durations, waits = [], [], []
        for entry in registry["campaigns"]:
            if entry["split"] != split:
                continue
            root = Path(entry["campaign_dir"])
            for run in read_json(root / "campaign_manifest.json")["runs"]:
                outcome = read_json(root / "outcomes" / f"{run['run_id']}_traffic.json")
                for session in outcome["sessions"]:
                    groups = session["send_groups"]
                    intervals.extend(group["start_interval_ms"] for group in groups[1:])
                    durations.extend(group["send_duration_ms"] for group in groups)
                    waits.extend(group["wait_after_ms"] for group in groups[:-1])
        def extent(values: list) -> dict:
            return {"count": len(values), "minimum": min(values) if values else None, "maximum": max(values) if values else None}
        observed[split] = {
            "message_by_scenario": {scenario: {name: extent(values) for name, values in fields.items()}
                                    for scenario, fields in by_scenario.items()},
            "actual_client_group_start_interval_ms": extent(intervals),
            "actual_client_group_send_duration_ms": extent(durations), "planned_group_wait_ms": extent(waits),
        }
    return {"planned_coverage": coverage, "observed_train_validation": observed,
            "test_feature_ranges_analyzed": False,
            "interpretation": "클라이언트 묶음 시작 간격은 송신·응답·지정 대기를 포함한다. 서버 메시지 파싱 간격과 직접 대응하지 않으며 같다고 검증하지 않는다."}


def collect_batch(root: Path, server_exe: Path, dataset_output: Path,
                  report_prefix: Path, dataset_report_prefix: Path) -> dict:
    if dataset_output.exists() or any(prefix.with_suffix(suffix).exists() for prefix in (report_prefix, dataset_report_prefix)
                                       for suffix in (".json", ".md")):
        raise FileExistsError("기존 데이터셋·보고서를 덮어쓰지 않습니다. 새 경로를 지정하세요.")
    if (root / "batch_runtime.json").exists():
        raise FileExistsError("실행한 batch는 다시 송신하지 않습니다. 실패 원본을 보존하고 새 batch를 준비하세요.")
    registry_path = root / "assignment_manifest.json"
    registry = read_json(registry_path)
    prepared = read_json(root / "prepared_manifest.json")
    fixed = {str(registry_path): prepared["assignment_sha256"], **registry["locked_file_sha256"],
             **prepared["campaign_manifest_sha256"]}
    reports = []
    runtime = {"batch_id": registry["batch_id"], "started_at_utc": datetime.now(UTC).isoformat(),
               "status": "running", "executed_campaigns": [], "error": None}
    try:
        verify_hashes(fixed)
        ensure_ports_free()
        for entry in registry["campaigns"]:
            verify_hashes(fixed)
            campaign_root = Path(entry["campaign_dir"])
            print(f"캠페인 시작: {entry['slot_id']} / {entry['split']} / seed={entry['seed']}", flush=True)
            executed = run_campaign(campaign_root, server_exe)
            checked = audit_campaign(campaign_root)
            save_quality_report(checked, campaign_root / "quality_report")
            reports.append(checked)
            runtime["executed_campaigns"].append({"campaign_id": entry["campaign_id"],
                                                   "runtime_passed": executed["passed"], "quality_passed": checked["passed"]})
            write_json(root / "batch_runtime.json", runtime)
            if not executed["passed"] or not checked["passed"]:
                raise RuntimeError(f"캠페인 수집·품질 검증 실패: {entry['slot_id']}. 실패 run을 골라 제거하지 않습니다.")
            print(f"캠페인 품질 PASS: {entry['slot_id']} ({checked['passed_runs']}/{checked['run_count']} run)", flush=True)
        verify_hashes(fixed)
        quality = summarize_quality(registry, reports)
        config = DatasetConfig(config_version="v2", dataset_id=registry["target_dataset_id"],
                               campaign_dirs=[Path(entry["campaign_dir"]) for entry in registry["campaigns"]],
                               output_dir=dataset_output, split_policy="preassigned_campaign_holdout",
                               campaign_registry=registry_path)
        write_json(root / "dataset_config.json", config.model_dump(mode="json"))
        dataset = build_dataset(config)
        verify_hashes(fixed)
        save_report(dataset, dataset_report_prefix)
        report = {
            "status": "complete", "batch_id": registry["batch_id"], "batch_path": str(root),
            "generated_at_utc": datetime.now(UTC).isoformat(), "assignment_sha256": file_hash(registry_path),
            "assignment": registry, "quality": quality, "dataset_output": str(dataset_output.resolve()),
            "dataset_manifest_sha256": file_hash(dataset_output / "dataset_manifest.json"),
            "splits": dataset["splits"], "identity_overlaps": dataset["identity_overlaps"],
            "duplicate_features": dataset["duplicate_features"], "excluded_runs": len(dataset["excluded_sources"]),
            "generator_controls": summarize_controls(registry, dataset_output),
            "preprocessing_fitted": False, "model_training_performed": False,
            "model_prediction_performed": False, "test_model_evaluated": False,
            "legacy_baseline_unchanged": True, "destination": "127.0.0.1:4000",
        }
        save_batch_report(report, report_prefix)
        runtime["status"] = "complete"
        return report
    except Exception as error:
        runtime.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        runtime["finished_at_utc"] = datetime.now(UTC).isoformat()
        write_json(root / "batch_runtime.json", runtime)


def save_batch_report(report: dict, prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    write_json(prefix.with_suffix(".json"), report)
    quality = report["quality"]
    lines = ["# 독립 캠페인 수집 및 사전 분할 결과", "",
             f"batch: `{report['batch_id']}`", f"원본: `{report['batch_path']}`", "",
             f"캠페인 {quality['campaigns']}개, run {quality['passed_runs']}/{quality['runs']} 통과, 메시지 후보 run {quality['eligible_message_runs']}개.",
             f"세션 {quality['totals']['sessions']}개, 메시지 {quality['totals']['messages']}건, 종료 요약 {quality['totals']['summaries']}건.", "",
             "## 송신 전에 고정한 역할", "", "| 슬롯 | seed | 분할 |", "| --- | ---: | --- |"]
    for entry in report["assignment"]["campaigns"]:
        lines.append(f"| {entry['slot_id']} | {entry['seed']} | {entry['split']} |")
    lines += ["", "한 캠페인의 모든 반복·run·세션·메시지는 같은 분할에 속한다. 정답은 독립 시나리오 manifest에서 가져오며 모델 출력이나 서버 오류로 생성하지 않는다.",
              "", "## 수집 품질", ""]
    for name, count in quality["totals"].items():
        lines.append(f"- `{name}`: {count}")
    lines += ["", "## 메시지 데이터셋", "", "| 분할 | 캠페인 | run | 세션 | 메시지 | 정상 | 통제된 이상 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for split, counts in report["splits"].items():
        lines.append(f"| {split} | {counts['campaigns']} | {counts['runs']} | {counts['sessions']} | {counts['rows']} | {counts['label_distribution']['0']} | {counts['label_distribution']['1']} |")
    lines += ["", f"데이터셋: `{report['dataset_output']}`", "",
              f"헤더 거부 run {report['excluded_runs']}개는 메시지 0개가 기대값이므로 종료 요약만 품질 검사하고 메시지 모델에서는 제외한다.",
              "", "## 분할 검증", ""]
    for pair, values in report["identity_overlaps"].items():
        lines.append(f"- `{pair}`: {values}")
    lines += [f"- 동일 feature 값 진단: {report['duplicate_features']}", "",
              "같은 값의 feature는 식별자 중복과 구분한다. 행 삭제·정답 교체로 분포를 바꾸지 않는다.", "",
              "## 송신 제어 및 관측", "",
              "본문 문자열 8/24/96/384/768바이트, 정상 6~24개·burst 12~24개 메시지, 동시 연결 1/2/4개를 사전 배치했다. 정상 묶음·burst 묶음 1/4/8개, 정상 분할 조각 7/19/31바이트를 실제 sendall 호출 수·묶음 크기와 대조했다.",
              "", "정상 묶음 사이 지정 대기는 1~4/5~15/20~40ms, burst는 0ms다. 각 시나리오의 모든 수준이 전체 캠페인과 Train에서 교차되지만 Validation의 시나리오별 반복은 2회이므로 완전한 3수준 요인 실험은 아니다.",
              "", report["generator_controls"]["interpretation"],
              "", "세부 설정 coverage와 Train/Validation의 송신 계측·실제 파싱 간격 범위는 JSON 보고서에 기록했다. Test feature 범위는 분석하지 않았다.", "",
              "## 수행하지 않은 작업과 한계", "",
              "scaler fit·모델 학습·모델 예측·Test 성능 평가를 하지 않았다. 기존 LR과 threshold 0.7은 수집 전에 해시로 고정했고 변경하지 않았다. FastAPI 응답은 여전히 dummy다.",
              "", "새 seed의 독립 캠페인이지만 같은 생성기·프로토콜·Windows 장비·서버 빌드를 사용했다. 실제 외부 공격 데이터나 완전한 요인 실험이 아니다. 송신 대기와 실제 메시지 파싱 간격은 TCP·OS의 영향으로 같지 않다.",
              "", "## 다음 작업", "",
              "고정한 기존 모델·threshold로 새 Validation만 진단한 뒤, 새 Train 전용 fit과 사전 후보 비교 실험을 진행한다. Test는 최종 평가까지 모델에 넣지 않는다.", ""]
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
