from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_collection.campaign import prepare_campaign, run_campaign
from data_collection.quality import audit_campaign, save_quality_report
from data_collection.runtime import ensure_ports_free
from data_collection.traffic import CampaignPlan, SCENARIO_LABELS


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 정상·통제된 이상 트래픽을 여러 run으로 수집하고 품질을 검증합니다.")
    parser.add_argument("--plan", type=Path, default=AI_ROOT / "configs" / "traffic_collection_plan.json")
    parser.add_argument("--scenario-manifest", type=Path, default=AI_ROOT / "configs" / "collection_scenarios.json")
    parser.add_argument("--server-exe", type=Path, default=AI_ROOT.parent / "Server" / "BIN" / "Debug" / "Server.exe")
    parser.add_argument("--output-dir", type=Path, default=AI_ROOT / "data" / "raw" / "operational_campaigns")
    parser.add_argument("--campaign-id", help="생략하면 UTC 시각과 임의 ID로 생성")
    parser.add_argument("--repeats", type=int, help="시나리오별 반복 수. 1~10")
    parser.add_argument("--scenarios", nargs="+", choices=list(SCENARIO_LABELS), help="선택한 시나리오만 실행")
    parser.add_argument("--dataset-purpose", choices=["controlled_experiment", "integration_test"], default="controlled_experiment")
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_data_quality")
    args = parser.parse_args()
    values = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    if args.repeats is not None:
        values["repeats"] = args.repeats
    if args.scenarios is not None:
        values["profiles"] = [profile for profile in values["profiles"] if profile["scenario_id"] in args.scenarios]
    plan = CampaignPlan.model_validate(values)
    ensure_ports_free()
    campaign_id = args.campaign_id or f"campaign_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid4().hex[:8]}"
    root = prepare_campaign(plan, campaign_id, args.output_dir, args.server_exe, args.scenario_manifest, args.dataset_purpose)
    print(f"원본 저장 위치: {root}", flush=True)
    print(f"계획: {len(plan.profiles)}개 시나리오 × {plan.repeats}회, 연결 대상 127.0.0.1:4000", flush=True)
    run_campaign(root, args.server_exe)
    report = audit_campaign(root)
    save_quality_report(report, root / "quality_report")
    save_quality_report(report, args.report_prefix)
    print(f"품질 검증: {'PASS' if report['passed'] else 'FAIL'} / {report['passed_runs']}/{report['run_count']} run", flush=True)
    print(f"보고서: {args.report_prefix.with_suffix('.md')}", flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
