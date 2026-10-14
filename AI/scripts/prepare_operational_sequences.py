from __future__ import annotations

import argparse
import sys
from pathlib import Path


AI_ROOT = Path(__file__).resolve().parents[1]
if (AI_ROOT / ".deps").exists():
    sys.path.insert(0, str(AI_ROOT / ".deps"))
sys.path.insert(0, str(AI_ROOT))

from data_preparation.operational_sequences import SequenceConfig, build_dataset, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="동일 run/session 내부의 과거·현재 창 구성. scaler·모델 fit과 Test 접근은 하지 않습니다.")
    parser.add_argument("--config", type=Path, default=AI_ROOT / "configs" / "operational_sequence_dataset.json")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report-prefix", type=Path, default=AI_ROOT / "reports" / "operational_sequence_dataset")
    args = parser.parse_args()
    try:
        values = load_config(args.config).model_dump()
        if args.output_dir is not None:
            values["output_dir"] = args.output_dir.resolve()
        report = build_dataset(SequenceConfig.model_validate(values), args.report_prefix.resolve())
    except (OSError, ValueError, KeyError) as error:
        print(f"[시계열 준비] 실패: {error}", file=sys.stderr)
        return 2
    for split, values in report["splits"].items():
        print(f"[시계열 준비] {split}: {values['shape']}, 짧은 창 {values['partial_windows']}개, 길이 1 {values['length_one_windows']}개")
    print("[시계열 준비] 원본·정답 행 유지 / 학습·Test 접근 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
