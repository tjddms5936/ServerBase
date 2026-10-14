from __future__ import annotations

import random
from collections import Counter

from pydantic import TypeAdapter

from data_collection.augmentation import compile_plans
from data_collection.config import Identifier
from data_collection.traffic import CampaignPlan, TrafficProfile, SCENARIO_LABELS, planned_runs, build_expectations
from data_preparation.operational_prefix_features import TRANSFORM_COLUMNS


EXPECTED_CONTROLS = {
    "text_sizes": [8, 24, 96, 384, 768], "valid_message_count_range": [12, 24],
    "batch_sizes": [1, 4, 8], "normal_wait_ms_ranges": [[1, 4], [5, 15], [20, 40]],
    "burst_wait_ms": 0, "fragment_sizes_bytes": [7, 19, 31], "concurrency_levels": [1, 2, 4],
}
EXPECTED_CANDIDATES = [
    {"id": "rf_raw7", "input_transform_id": "raw7_v1"},
    {"id": "rf_without_io5", "input_transform_id": "without_io5_v1"},
    {"id": "rf_prefix_mean5", "input_transform_id": "prefix_mean5_v1"},
    {"id": "rf_prefix_mean_parsed6", "input_transform_id": "prefix_mean_parsed6_v1"},
]
EXPECTED_PARAMETERS = {"n_estimators": 100, "min_samples_leaf": 5, "max_depth": None,
                       "class_weight": None, "random_state": 42, "n_jobs": 1}


def compile_recovery_plans(design: dict, previous_design: dict) -> list[tuple[dict, CampaignPlan]]:
    if (design["plan_version"] != "recovery_v1" or design["status"] != "design_only" or
            design["raw_feature_contract_id"] != "server_application_message_v2" or
            design["split_policy"] != "preassigned_campaign_holdout" or design["previous_test_exposed"] is not True or
            design["repeats_per_campaign"] != 9 or design["sessions_per_run"] != 4):
        raise ValueError("보정 설계의 상태·계약·분할·반복 수가 다릅니다.")
    TypeAdapter(Identifier).validate_python(design["target_dataset_id"])
    slots = design["campaign_slots"]
    if (len(slots) != 6 or len({slot["slot_id"] for slot in slots}) != 6 or
            len({slot["seed"] for slot in slots}) != 6 or
            Counter(slot["split"] for slot in slots) != {"train": 3, "validation": 1, "test": 2} or
            len(design["scenario_ids"]) != 7 or set(design["scenario_ids"]) != set(SCENARIO_LABELS)):
        raise ValueError("독립 캠페인 6개·3/1/2 역할·전체 시나리오가 필요합니다.")
    controls, safety = design["generator_controls"], design["safety_limits"]
    if any(controls[key] != value for key, value in EXPECTED_CONTROLS.items()):
        raise ValueError("제한된 송신 제어 범위 밖의 계획입니다.")
    if safety != {"destination": "127.0.0.1:4000", "max_messages_per_session": 24,
                  "max_sessions_per_run": 4, "max_concurrency": 4, "max_text_bytes": 768,
                  "external_traffic_allowed": False, "auto_collect_now": False}:
        raise ValueError("로컬 안전 한도와 자동 수집 금지를 유지해야 합니다.")
    experiment, criteria = design["development_experiment"], design["approval_criteria"]
    if (experiment["fit_scope"] != "new_train_only" or experiment["selection_scope"] != "new_validation_only" or
            experiment["model_kind"] != "random_forest" or experiment["model_parameters"] != EXPECTED_PARAMETERS or
            experiment["candidates"] != EXPECTED_CANDIDATES or experiment["thresholds"] != [0.3, 0.4, 0.5, 0.6, 0.7] or
            experiment["maximum_trials"] != 20 or criteria["max_false_positive_rate"] != 0.1 or
            criteria["max_normal_batched_false_positive_rate"] != 0.1 or criteria["min_recall"] != 0.5 or
            criteria["max_single_request_p95_ms"] != 25.0 or criteria["max_process_peak_rss_mib"] != 512.0 or
            criteria["max_deployment_artifacts_mib"] != 50.0 or criteria["scope"] != "local_advisory_integration_only"):
        raise ValueError("보정 후보·threshold·승인 조건의 사전 한도가 다릅니다.")
    if (design["label_policy"]["source"] != "송신 전에 고정한 시나리오 정답 manifest" or
            design["label_policy"]["keep_early_prefix_labels"] is not True or
            design["prefix_policy"]["group_key"] != ["campaign_id", "run_id", "session_id"] or
            design["prefix_policy"]["order"] != "request_index"):
        raise ValueError("독립 정답과 prefix 그룹 기준을 유지해야 합니다.")
    previous_plans = compile_plans(previous_design)
    previous_seeds = {slot["seed"] for slot, _ in previous_plans}
    previous_run_seeds = {seed for _, plan in previous_plans for _, _, seed in planned_runs(plan)}
    if previous_seeds & {slot["seed"] for slot in slots}:
        raise ValueError("이전 수집 캠페인 seed를 재사용하지 않습니다.")
    plans = []
    for slot_index, slot in enumerate(slots):
        TypeAdapter(Identifier).validate_python(slot["slot_id"])
        profiles_by_repeat = []
        for repeat in range(9):
            # 묶음 크기와 정상 대기 범위를 독립 축으로 교차하여 한쪽에 고정 대응하지 않는다.
            batch_level, wait_level = divmod(repeat, 3)
            concurrency = controls["concurrency_levels"][(slot_index + batch_level + wait_level) % 3]
            profiles = []
            for scenario in design["scenario_ids"]:
                burst = scenario == "controlled_burst_chat"
                wait = [0, 0] if burst else controls["normal_wait_ms_ranges"][wait_level]
                profiles.append(TrafficProfile(
                    scenario_id=scenario, sessions=4, concurrency=concurrency, messages_min=12, messages_max=24,
                    text_sizes=controls["text_sizes"], interval_min_ms=wait[0], interval_max_ms=wait[1],
                    batch_size=controls["batch_sizes"][batch_level]
                    if scenario in {"normal_batched_chat", "controlled_burst_chat"} else 1,
                    fragment_size_bytes=controls["fragment_sizes_bytes"][(batch_level + wait_level) % 3]
                    if scenario == "normal_fragmented_chat" else None,
                ))
            random.Random(slot["seed"] * 1009 + repeat).shuffle(profiles)
            profiles_by_repeat.append(profiles)
        plans.append((slot, CampaignPlan(plan_version="v2", seed=slot["seed"], repeats=9,
                                        profiles=profiles_by_repeat[0], repeat_profiles=profiles_by_repeat)))
    seeds = [seed for _, plan in plans for _, _, seed in planned_runs(plan)]
    if len(seeds) != len(set(seeds)) or previous_run_seeds & set(seeds):
        raise ValueError("새 run끼리 또는 기존 run과 파생 seed가 충돌합니다.")
    return plans


def summarize_recovery_design(design: dict, plans: list[tuple[dict, CampaignPlan]]) -> dict:
    splits = {}
    for slot, plan in plans:
        summary = splits.setdefault(slot["split"], {"campaigns": 0, "runs": 0, "sessions": 0,
                                                    "expected_message_rows": 0, "normal_batch_crossing": set()})
        summary["campaigns"] += 1
        for repeat, profile, seed in planned_runs(plan):
            expected = build_expectations(profile, seed, f"{slot['slot_id']}_r{repeat}_{profile.scenario_id}",
                                          SCENARIO_LABELS[profile.scenario_id])
            summary["runs"] += 1
            summary["sessions"] += profile.sessions
            summary["expected_message_rows"] += expected["expected_messages"]
            if profile.scenario_id == "normal_batched_chat":
                summary["normal_batch_crossing"].add((profile.batch_size, profile.interval_min_ms, profile.interval_max_ms))
    for summary in splits.values():
        summary["normal_batch_crossing"] = sorted(summary["normal_batch_crossing"])
    return {"status": "compiled_design_not_collected", "splits": splits,
            "planned_runs": sum(item["runs"] for item in splits.values()),
            "planned_sessions": sum(item["sessions"] for item in splits.values()),
            "candidate_input_columns": {item["id"]: TRANSFORM_COLUMNS[item["input_transform_id"]]
                                        for item in design["development_experiment"]["candidates"]},
            "planned_trial_count": 20, "collection_performed": False, "fit_performed": False,
            "interpretation": "기대 메시지 수는 생성기 계획의 값이며 실제 수집 성공·분할 완료·모델 승인 결과가 아니다."}
