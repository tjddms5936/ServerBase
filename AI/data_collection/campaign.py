from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from .config import AI_ROOT, CollectionConfig, Identifier, load_manifest
from .runtime import child_environment, ensure_ports_free, stop_owned_process, wait_for_capture
from .traffic import CampaignPlan, build_expectations, generate_traffic


def write_json(path: Path, values: dict) -> None:
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_campaign(
    plan: CampaignPlan, campaign_id: str, output_dir: Path, server_exe: Path,
    scenario_manifest: Path, dataset_purpose: str = "controlled_experiment",
) -> Path:
    campaign_id = TypeAdapter(Identifier).validate_python(campaign_id)
    if not server_exe.is_file():
        raise FileNotFoundError("Server.exe가 없습니다. Debug x64 빌드 또는 --server-exe 지정이 필요합니다.")
    manifest, _, manifest_hash = load_manifest(scenario_manifest, plan.profiles[0].scenario_id)
    labels = {scenario.scenario_id: scenario.label for scenario in manifest.scenarios}
    build_id = "sha256:" + file_hash(server_exe)
    contract_path = AI_ROOT / "configs" / "operational_feature_schema.json"
    root = output_dir.resolve() / campaign_id
    entries = []
    expectations = []
    # 모든 기대값을 송신 전에 계산한다. 수집 결과를 보고 정답이나 기대 수를 수정하지 않는다.
    for repeat in range(plan.repeats):
        for profile_index, profile in enumerate(plan.profiles):
            run_id = f"{campaign_id}_r{repeat:02d}_{profile.scenario_id}"
            seed = plan.seed + repeat * 100 + profile_index
            expected = build_expectations(profile, seed, run_id, labels.get(profile.scenario_id))
            config = CollectionConfig(
                run_id=run_id, scenario_id=profile.scenario_id, server_build_id=build_id,
                scenario_manifest=scenario_manifest.resolve(), output_dir=root / "runs",
                dataset_purpose=dataset_purpose,
            )
            entries.append({
                "run_id": run_id, "scenario_id": profile.scenario_id, "repeat_index": repeat,
                "seed": seed, "independent_label": expected["independent_label"],
                "expectations_file": f"expectations/{run_id}.json",
                "config_file": f"configs/{run_id}.json",
            })
            expectations.append((expected, config))
    root.mkdir(parents=True, exist_ok=False)
    for name in ("expectations", "configs", "outcomes", "logs"):
        (root / name).mkdir()
    for entry, (expected, config) in zip(entries, expectations):
        expectation_path = root / entry["expectations_file"]
        write_json(expectation_path, expected)
        entry["expectations_sha256"] = file_hash(expectation_path)
        write_json(root / entry["config_file"], config.model_dump(mode="json"))
    source_files = [
        AI_ROOT / "data_collection" / name for name in (
            "traffic.py", "campaign.py", "runtime.py", "api_runner.py", "quality.py", "collector.py", "config.py",
        )
    ]
    write_json(root / "campaign_manifest.json", {
        "campaign_version": "v1", "campaign_id": campaign_id,
        "created_at_utc": datetime.now(UTC).isoformat(), "dataset_purpose": dataset_purpose,
        "plan_snapshot": plan.model_dump(mode="json"), "runs": entries,
        "source_manifest_sha256": manifest_hash, "scenario_manifest_snapshot": manifest.model_dump(mode="json"),
        "server_exe": str(server_exe.resolve()), "server_build_id": build_id,
        "operational_contract_sha256": file_hash(contract_path),
        "operational_contract_snapshot": json.loads(contract_path.read_text(encoding="utf-8")),
        "python_version": sys.version,
        "code_evidence": [{"path": str(path.relative_to(AI_ROOT)), "sha256": file_hash(path)} for path in source_files],
    })
    return root


def run_capture(root: Path, entry: dict, server_exe: Path) -> dict:
    ensure_ports_free()
    run_id = entry["run_id"]
    expected = json.loads((root / entry["expectations_file"]).read_text(encoding="utf-8"))
    config_path = root / entry["config_file"]
    # 지정한 빌드가 준비 이후 바뀌면 메타데이터와 실제 실행이 달라지므로 중단한다.
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["server_build_id"] != "sha256:" + file_hash(server_exe):
        raise RuntimeError("수집 준비 이후 Server.exe가 변경되었습니다.")
    environment = child_environment(config_path)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    api_process = server_process = None
    result = {"runtime_version": "v1", "run_id": run_id, "passed": False, "error": None}
    with (root / "logs" / f"{run_id}_api.log").open("wb") as api_output, \
            (root / "logs" / f"{run_id}_server.log").open("wb") as server_output:
        try:
            api_process = subprocess.Popen(
                [sys.executable, "-m", "data_collection.api_runner"], cwd=AI_ROOT, env=environment,
                stdout=api_output, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, creationflags=flags,
            )
            wait_for_capture(api_process, run_id)
            server_process = subprocess.Popen(
                [str(server_exe.resolve())], cwd=AI_ROOT.parent / "Server", env=environment,
                stdout=server_output, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, creationflags=flags,
            )
            outcome = generate_traffic(expected)
            write_json(root / "outcomes" / f"{run_id}_traffic.json", outcome)
            if not outcome["passed"]:
                raise RuntimeError("일부 클라이언트의 송신·응답·종료 검증이 실패했습니다.")
            wait_for_capture(api_process, run_id, expected["expected_records"])
            result["passed"] = True
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
        finally:
            # 마지막 전송을 놓치지 않도록 C++ 전송 worker부터 종료하고 API를 배출한다.
            result["server_shutdown"] = stop_owned_process(server_process)
            result["api_shutdown"] = stop_owned_process(api_process)
            if not result["server_shutdown"]["graceful"] or not result["api_shutdown"]["graceful"]:
                result["passed"] = False
            write_json(root / "outcomes" / f"{run_id}_runtime.json", result)
    return result


def run_campaign(root: Path, server_exe: Path) -> dict:
    manifest = json.loads((root / "campaign_manifest.json").read_text(encoding="utf-8"))
    results = []
    for entry in manifest["runs"]:
        try:
            result = run_capture(root, entry, server_exe)
        except Exception as error:
            result = {"run_id": entry["run_id"], "passed": False, "error": f"{type(error).__name__}: {error}"}
            results.append(result)
            # 시작 조건이 깨지면 다른 실행의 포트를 건드리지 않고 캠페인을 중단한다.
            break
        results.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'}: {entry['run_id']}", flush=True)
    summary = {
        "campaign_id": manifest["campaign_id"], "finished_at_utc": datetime.now(UTC).isoformat(),
        "planned_runs": len(manifest["runs"]), "executed_runs": len(results),
        "passed": len(results) == len(manifest["runs"]) and all(result["passed"] for result in results),
        "runs": results,
    }
    write_json(root / "campaign_runtime.json", summary)
    return summary
