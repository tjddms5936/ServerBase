from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_collection.quality import read_json
from data_collection.recovery import prepare_recovery_batch, collect_recovery_batch, require_new_outputs
from data_collection.runtime import ensure_ports_free


def main() -> int:
    parser = argparse.ArgumentParser(description="고정한 보정 캠페인 6개를 로컬에서 수집·검증하고 사전 역할로 분리합니다.")
    parser.add_argument("--design", type=Path, default=AI_ROOT / "configs" / "operational_recovery_plan.json")
    parser.add_argument("--previous-plan", type=Path, default=AI_ROOT / "configs" / "operational_augmentation_plan.json")
    parser.add_argument("--server-exe", type=Path)
    parser.add_argument("--scenario-manifest", type=Path, default=AI_ROOT / "configs" / "collection_scenarios.json")
    parser.add_argument("--output-dir", type=Path, default=AI_ROOT / "data" / "raw" / "operational_recovery")
    parser.add_argument("--dataset-output", type=Path)
    parser.add_argument("--report-prefix", type=Path)
    parser.add_argument("--dataset-report-prefix", type=Path)
    parser.add_argument("--final-report", type=Path, default=AI_ROOT / "reports" / "operational_final_evaluation.json")
    parser.add_argument("--diagnosis-report", type=Path, default=AI_ROOT / "reports" / "operational_rf_diagnosis.json")
    parser.add_argument("--batch-id", help="생략하면 짧은 UTC 날짜·임의 ID를 생성합니다.")
    parser.add_argument("--prepare-only", action="store_true", help="역할·해시·기대값만 저장하고 실제 송신하지 않습니다.")
    parser.add_argument("--prepared-batch", type=Path, help="아직 수집하지 않은 준비 batch의 잠근 설정으로 실행합니다.")
    args = parser.parse_args()
    if args.prepared_batch:
        if args.prepare_only or args.batch_id or any(getattr(args, name) is not None for name in
                                                   ("dataset_output", "report_prefix", "dataset_report_prefix")):
            parser.error("준비된 batch에서는 새 id·출력 경로·prepare-only를 함께 지정하지 않습니다.")
        root = args.prepared_batch.resolve()
        registry = read_json(root / "assignment_manifest.json")
        server_exe = args.server_exe or Path(registry["server_exe"])
    else:
        server_exe = args.server_exe or AI_ROOT.parent / "Server" / "BIN" / "Debug" / "Server.exe"
        dataset_output = args.dataset_output or AI_ROOT / "data" / "processed" / "operational_message" / "recovery_v1"
        report_prefix = args.report_prefix or AI_ROOT / "reports" / "operational_recovery_collection"
        dataset_report_prefix = args.dataset_report_prefix or AI_ROOT / "reports" / "operational_recovery_dataset"
        require_new_outputs(dataset_output, report_prefix, dataset_report_prefix)
        if not args.prepare_only:
            # 사용자가 이미 실행한 서버는 건드리지 않고 시작 전에 포트 충돌을 알린다.
            ensure_ports_free()
        batch_id = args.batch_id or f"rec_{datetime.now(UTC):%y%m%d}_{uuid4().hex[:8]}"
        root = prepare_recovery_batch(args.design, args.previous_plan, batch_id, args.output_dir,
                                      server_exe, args.scenario_manifest, dataset_output, report_prefix,
                                      dataset_report_prefix, args.final_report, args.diagnosis_report)
        print(f"보정 캠페인 6개 준비 완료: {root}", flush=True)
        if args.prepare_only:
            return 0
    report = collect_recovery_batch(root, server_exe)
    print(f"실제 수집·분할 완료: {report['quality']['passed_runs']}개 run / {report['quality']['totals']['messages']}개 메시지", flush=True)
    print(f"데이터셋: {report['dataset_output']}", flush=True)
    print("학습·모델 선정·새 Test 성능 평가·배포는 수행하지 않았습니다.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
