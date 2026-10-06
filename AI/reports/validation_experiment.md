# Validation 분리와 설정·threshold 선정 실험

## 고정 실험 규칙

공식 train만 사용해 전처리, 모델 학습, 설정 및 threshold 선택을 수행했다. 공식 test CSV는 읽지 않았다.

- 후보: LR C=1, RF min_samples_leaf=1/5/20; RF 트리 수 100개 등 나머지 조건은 고정
- threshold 후보: `[0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]`
- 목표: validation FPR <= 10.00% 중 Recall 최대
- 동률 처리: F1 최대, FPR 최소, 후보 id, threshold 순으로 결정
- 이 오탐률 목표는 실험용이며 실제 운영 환경의 오탐률을 보장하지 않는다.

## 중복 그룹을 고려한 분리

원본 train 175,341건에서 학습 140,272건, validation 35,069건(20.00%)으로 분리했다.

동일 입력 feature 중복 행은 74,301건이었다. id와 정답을 제외하고 결측 표현을 통일한 feature fingerprint를 그룹으로 사용했다. 단순 row 분리로 중복 행이 양쪽에 들어가는 문제를 피하도록 StratifiedGroupKFold의 사전 지정 fold를 사용했다.

- fold 수: 5, validation fold: 0, seed: 42
- 층화 기준: attack_cat; 학습 입력에는 attack_cat을 포함하지 않음
- 학습/validation 간 공유 feature 그룹: 0
- 그룹 크기가 달라 비율과 유형별 비중이 정확한 80:20은 아닐 수 있음

| 공격 유형 | 학습 | Validation |
| --- | --- | --- |
| Analysis | 1,600 | 400 |
| Backdoor | 1,397 | 349 |
| DoS | 9,811 | 2,453 |
| Exploits | 26,714 | 6,679 |
| Fuzzers | 14,547 | 3,637 |
| Generic | 32,000 | 8,000 |
| Normal | 44,800 | 11,200 |
| Reconnaissance | 8,393 | 2,098 |
| Shellcode | 906 | 227 |
| Worms | 104 | 26 |

## 전처리 학습 범위

전처리기는 학습 140,272건에만 fit했다. Validation에는 같은 전처리기의 transform만 적용했다. 기존 전체 train 기반 전처리 CSV와 전처리기는 사용하지 않았다.

숫자형 39개, 범주형 3개를 처리해 192개 입력을 생성했다. 중앙값 대체, 표준화, unknown 처리와 one-hot encoding은 기존 실험과 같은 규칙이다.

## 후보별 threshold 결과

| 후보 | Threshold | FPR | Recall | Precision | F1 | 제약 |
| --- | --- | --- | --- | --- | --- | --- |
| logistic_regression_c1 | 0.3 | 19.49% | 99.42% | 91.58% | 95.34% | 미충족 |
| logistic_regression_c1 | 0.4 | 19.02% | 99.21% | 91.75% | 95.33% | 미충족 |
| logistic_regression_c1 | 0.5 | 17.78% | 98.76% | 92.21% | 95.37% | 미충족 |
| logistic_regression_c1 | 0.6 | 15.12% | 97.50% | 93.22% | 95.31% | 미충족 |
| logistic_regression_c1 | 0.7 | 9.91% | 94.21% | 95.30% | 94.75% | 충족 |
| logistic_regression_c1 | 0.8 | 4.14% | 87.57% | 97.83% | 92.41% | 충족 |
| logistic_regression_c1 | 0.9 | 0.87% | 74.95% | 99.46% | 85.49% | 충족 |
| random_forest_leaf1 | 0.3 | 16.36% | 99.49% | 92.84% | 96.05% | 미충족 |
| random_forest_leaf1 | 0.4 | 12.99% | 98.81% | 94.19% | 96.45% | 미충족 |
| random_forest_leaf1 | 0.5 | 9.26% | 97.75% | 95.74% | 96.73% | 충족 |
| random_forest_leaf1 | 0.6 | 6.02% | 96.02% | 97.14% | 96.58% | 충족 |
| random_forest_leaf1 | 0.7 | 3.75% | 93.90% | 98.16% | 95.98% | 충족 |
| random_forest_leaf1 | 0.8 | 2.13% | 91.16% | 98.91% | 94.88% | 충족 |
| random_forest_leaf1 | 0.9 | 0.63% | 86.47% | 99.66% | 92.60% | 충족 |
| random_forest_leaf5 | 0.3 | 19.19% | 99.90% | 91.73% | 95.64% | 미충족 |
| random_forest_leaf5 | 0.4 | 16.12% | 99.46% | 92.93% | 96.09% | 미충족 |
| random_forest_leaf5 | 0.5 | 11.75% | 98.35% | 94.69% | 96.48% | 미충족 |
| random_forest_leaf5 | 0.6 | 7.09% | 96.11% | 96.65% | 96.38% | 충족 |
| random_forest_leaf5 | 0.7 | 4.16% | 93.62% | 97.96% | 95.74% | 충족 |
| random_forest_leaf5 | 0.8 | 1.70% | 90.07% | 99.12% | 94.38% | 충족 |
| random_forest_leaf5 | 0.9 | 0.24% | 84.14% | 99.87% | 91.33% | 충족 |
| random_forest_leaf20 | 0.3 | 20.62% | 99.98% | 91.18% | 95.38% | 미충족 |
| random_forest_leaf20 | 0.4 | 18.25% | 99.71% | 92.09% | 95.75% | 미충족 |
| random_forest_leaf20 | 0.5 | 13.48% | 98.71% | 93.98% | 96.29% | 미충족 |
| random_forest_leaf20 | 0.6 | 7.96% | 96.15% | 96.26% | 96.21% | 충족 |
| random_forest_leaf20 | 0.7 | 4.80% | 93.63% | 97.65% | 95.60% | 충족 |
| random_forest_leaf20 | 0.8 | 1.95% | 89.30% | 98.99% | 93.89% | 충족 |
| random_forest_leaf20 | 0.9 | 0.21% | 81.52% | 99.88% | 89.77% | 충족 |

제약 충족은 허용 FPR 조건과 학습 수렴 여부를 모두 확인한 결과다. 한 번 학습한 모델의 validation 확률에 여러 threshold를 적용하며, threshold마다 재학습하지 않는다.

## 선정 결과

선정 후보는 `random_forest_leaf1`, threshold는 `0.5`다. Validation FPR 9.26%, Recall 97.75%, F1 96.73%로 사전 규칙을 충족했다.

선정 모델은 분리된 학습 부분으로 학습한 상태로 보관한다. Validation을 합쳐 다시 fit하지 않았으며, 전처리기, 모델, feature schema, threshold 및 split 위치를 함께 저장했다.

## 후보별 공격 유형 Recall

### logistic_regression_c1

이 후보의 제약 충족 threshold: `0.7`

| 유형 | 표본 | 기본 0.5 Recall | 선정 threshold Recall | 선정 FN |
| --- | --- | --- | --- | --- |
| Fuzzers | 3,637 | 94.89% | 72.20% | 1011 |
| Backdoor | 349 | 97.71% | 96.28% | 13 |
| DoS | 2,453 | 98.90% | 97.59% | 59 |
| Exploits | 6,679 | 99.10% | 97.35% | 177 |
| Shellcode | 227 | 99.12% | 94.27% | 13 |
| Reconnaissance | 2,098 | 99.57% | 96.85% | 66 |
| Analysis | 400 | 99.75% | 93.00% | 28 |
| Generic | 8,000 | 99.95% | 99.81% | 15 |
| Worms | 26 | 100.00% | 100.00% | 0 |

### random_forest_leaf1

이 후보의 제약 충족 threshold: `0.5`

| 유형 | 표본 | 기본 0.5 Recall | 선정 threshold Recall | 선정 FN |
| --- | --- | --- | --- | --- |
| Fuzzers | 3,637 | 87.46% | 87.46% | 456 |
| Analysis | 400 | 89.50% | 89.50% | 42 |
| Exploits | 6,679 | 99.45% | 99.45% | 37 |
| Shellcode | 227 | 99.56% | 99.56% | 1 |
| DoS | 2,453 | 99.96% | 99.96% | 1 |
| Generic | 8,000 | 99.99% | 99.99% | 1 |
| Reconnaissance | 2,098 | 100.00% | 100.00% | 0 |
| Backdoor | 349 | 100.00% | 100.00% | 0 |
| Worms | 26 | 100.00% | 100.00% | 0 |

### random_forest_leaf5

이 후보의 제약 충족 threshold: `0.6`

| 유형 | 표본 | 기본 0.5 Recall | 선정 threshold Recall | 선정 FN |
| --- | --- | --- | --- | --- |
| Fuzzers | 3,637 | 90.51% | 77.73% | 810 |
| Analysis | 400 | 93.50% | 86.25% | 55 |
| Shellcode | 227 | 99.12% | 97.80% | 5 |
| Exploits | 6,679 | 99.70% | 99.21% | 53 |
| Reconnaissance | 2,098 | 99.95% | 99.95% | 1 |
| Generic | 8,000 | 99.99% | 99.99% | 1 |
| DoS | 2,453 | 100.00% | 99.88% | 3 |
| Backdoor | 349 | 100.00% | 100.00% | 0 |
| Worms | 26 | 100.00% | 100.00% | 0 |

### random_forest_leaf20

이 후보의 제약 충족 threshold: `0.6`

| 유형 | 표본 | 기본 0.5 Recall | 선정 threshold Recall | 선정 FN |
| --- | --- | --- | --- | --- |
| Fuzzers | 3,637 | 92.58% | 77.73% | 810 |
| Analysis | 400 | 94.50% | 87.50% | 50 |
| Shellcode | 227 | 99.56% | 97.36% | 6 |
| Exploits | 6,679 | 99.79% | 99.27% | 49 |
| Reconnaissance | 2,098 | 99.95% | 99.95% | 1 |
| Generic | 8,000 | 100.00% | 99.99% | 1 |
| DoS | 2,453 | 100.00% | 99.92% | 2 |
| Backdoor | 349 | 100.00% | 100.00% | 0 |
| Worms | 26 | 100.00% | 100.00% | 0 |

## 해석 범위와 다음 단계

- 이번 validation 결과를 과거 공식 test 결과와 직접 비교해 개선폭이라고 표현하면 안 된다. 평가 표본과 전처리 학습 범위가 다르다.
- 정확히 같은 feature 그룹은 분리했지만 동일 세션이나 유사한 흐름 전체를 분리했다는 뜻은 아니다. 이 CSV에는 충분한 시간/세션 정보가 없다.
- 단일 fold와 seed의 탐색 실험이므로 선택 후 수치는 독립적인 최종 성능 추정이 아니다.
- 기존 공식 test도 이전 단계에서 탐색한 자료다. 추후 고정된 설정 평가와 새 데이터 검증의 한계를 구분해 기록해야 한다.
- 저장한 후보와 threshold를 동결하고, 다음 단계에서 운영용 feature의 의미·단위·계산 시점을 C++ 수집 값과 대조한다.
