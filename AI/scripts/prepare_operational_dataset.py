from __future__ import annotations

import argparse
import sys
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_preparation.operational_dataset import DatasetConfig, build_dataset, load_config, save_report


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 메시지 원본을 검증하고 run/session 단위 고정 데이터셋을 구성합니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_dataset.json")
    parser.add_argument("--campaign-dir", type=Path, nargs="+", help="설정의 입력 캠페인 대신 지정한 캠페인 사용")
    parser.add_argument("--output-dir", type=Path, help="설정 대신 새 데이터셋 저장 경로 지정")
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_dataset")
    args = parser.parse_args()
    values = load_config(args.config).model_dump()
    if args.campaign_dir is not None:
        values["campaign_dirs"] = [path.resolve() for path in args.campaign_dir]
    if args.output_dir is not None:
        values["output_dir"] = args.output_dir.resolve()
    config = DatasetConfig.model_validate(values)
    try:
        report = build_dataset(config)
    except (OSError, ValueError, KeyError) as error:
        print(f"데이터셋 구성 실패: {error}", file=sys.stderr)
        return 2
    save_report(report, args.report_prefix)
    print(f"데이터셋 구성 완료: {report['output_dir']}")
    for split, summary in report["splits"].items():
        print(f"  {split}: run {summary['runs']}, 세션 {summary['sessions']}, 메시지 {summary['rows']}, 정답 {summary['label_distribution']}")
    print(f"보고서: {args.report_prefix.with_suffix('.md')}")
    print("이번 단계에서는 전처리기와 모델을 학습하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
