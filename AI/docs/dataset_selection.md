# 데이터셋 선정

## 선정 결과

이 프로젝트의 첫 학습 데이터셋은 **UNSW-NB15**로 선정합니다.

UNSW-NB15를 1차 데이터셋으로 선택한 이유는 공식 train/test CSV 파일이 제공되고, baseline 실험을 시작하기에 feature 수가 과도하지 않으며, 정상 traffic과 여러 공격 category를 함께 포함하기 때문입니다. 따라서 첫 번째 재현 가능한 모델 학습 파이프라인을 만들기에 적합합니다.

## 후보 데이터셋 비교

| 데이터셋 | 역할 | 중요한 이유 | 초기 판단 |
|---|---|---|---|
| UNSW-NB15 | 1차 데이터셋 | 공식 train/test split과 feature 설명 파일이 제공됩니다. 표 형태 데이터 기반 ML 기준 실험에 적합합니다. | 먼저 사용 |
| CIC-IDS2017 | 2차 확장 데이터셋 | CICFlowMeter로 생성된 대규모 flow 기반 데이터셋이며 80개 이상의 flow feature를 포함합니다. | 나중에 확장 실험 |
| NSL-KDD | 참고용 데이터셋 | 고전적인 benchmark이지만 오래되었고 현대 traffic을 충분히 대표하기 어렵습니다. | 첫 실험에서는 제외 |

## 예정 실험 흐름

1. UNSW-NB15 CSV 파일을 `data/raw/unsw_nb15/`에 배치합니다.
2. EDA를 수행해 label 분포, feature 타입, 결측치, class imbalance를 확인합니다.
3. 전처리 파이프라인을 구성합니다.
4. Logistic Regression 기준 모델을 학습합니다.
5. RandomForest 기준 모델을 학습합니다.
6. 모델 산출물과 평가 지표를 저장합니다.
7. 가장 적합한 기준 모델 산출물을 FastAPI inference 서버에 연결합니다.
8. 이후 LSTM 같은 sequence 기반 모델과 비교합니다.

## 필요한 원본 파일

아래 파일을 `AI/data/raw/unsw_nb15/`에 배치합니다.

```text
UNSW_NB15_training-set.csv
UNSW_NB15_testing-set.csv
UNSW_NB15_features.csv
```

## 참고 출처

- UNSW Research, "The UNSW-NB15 Dataset": https://research.unsw.edu.au/projects/unsw-nb15-dataset
- Canadian Institute for Cybersecurity, "CIC-IDS2017": https://www.unb.ca/cic/datasets/ids-2017.html
- Canadian Institute for Cybersecurity, "CICFlowMeter": https://www.unb.ca/cic/research/applications.html
- IMPACT Cyber Trust, "NSL-KDD dataset": https://www.impactcybertrust.org/dataset_view?idDataset=928
