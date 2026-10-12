from __future__ import annotations

import argparse
import sys
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_collection.quality import audit_campaign, save_quality_report


def main() -> int:
    parser = argparse.ArgumentParser(description="보관한 수집 캠페인의 누락·중복·독립 정답 분포를 다시 검증합니다.")
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_data_quality")
    args = parser.parse_args()
    report = audit_campaign(args.campaign_dir.resolve())
    save_quality_report(report, args.report_prefix)
    print(f"품질 검증: {'PASS' if report['passed'] else 'FAIL'} / {report['passed_runs']}/{report['run_count']} run")
    print(f"검증 보고서: {args.report_prefix.with_suffix('.md')}")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
