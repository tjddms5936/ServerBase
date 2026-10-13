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

from data_collection.augmentation import prepare_batch, collect_batch
from data_collection.runtime import ensure_ports_free


def main() -> int:
    parser = argparse.ArgumentParser(description="설계한 6개 로컬 캠페인을 수집·검증하고 사전 캠페인 분할로 저장합니다.")
    parser.add_argument("--design", type=Path, default=AI_ROOT / "configs" / "operational_augmentation_plan.json")
    parser.add_argument("--server-exe", type=Path, default=AI_ROOT.parent / "Server" / "BIN" / "Debug" / "Server.exe")
    parser.add_argument("--scenario-manifest", type=Path, default=AI_ROOT / "configs" / "collection_scenarios.json")
    parser.add_argument("--output-dir", type=Path, default=AI_ROOT / "data" / "raw" / "operational_augmentation")
    parser.add_argument("--dataset-output", type=Path, default=AI_ROOT / "data" / "processed" / "operational_message" / "v2_campaign_holdout")
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_augmentation_collection")
    parser.add_argument("--dataset-report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_dataset_v2")
    parser.add_argument("--batch-id", help="생략하면 UTC와 임의 ID로 생성합니다.")
    parser.add_argument("--prepare-only", action="store_true", help="계획과 기대값만 저장하고 송신하지 않습니다.")
    args = parser.parse_args()
    if not args.prepare_only and (args.dataset_output.exists() or any(
            prefix.with_suffix(suffix).exists() for prefix in (args.report_prefix, args.dataset_report_prefix)
            for suffix in (".json", ".md"))):
        parser.error("기존 데이터셋·보고서가 있습니다. 세 출력 경로를 새 버전으로 지정하세요.")
    if not args.prepare_only:
        ensure_ports_free()
    batch_id = args.batch_id or f"aug_{datetime.now(UTC):%y%m%d}_{uuid4().hex[:8]}"
    root = prepare_batch(args.design, batch_id, args.output_dir, args.server_exe, args.scenario_manifest)
    print(f"6개 캠페인 준비 완료: {root}", flush=True)
    if args.prepare_only:
        return 0
    report = collect_batch(root, args.server_exe, args.dataset_output, args.report_prefix, args.dataset_report_prefix)
    print(f"수집·분할 완료: {report['quality']['totals']['messages']}개 메시지 / {report['quality']['passed_runs']}개 run 통과", flush=True)
    print(f"보고서: {args.report_prefix.with_suffix('.md')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
