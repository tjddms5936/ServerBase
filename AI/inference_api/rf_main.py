from __future__ import annotations

from pathlib import Path

from .approved_bundle import DEFAULT_CONFIG, load_serving_config
from .main import create_app
from .rf_service import ApprovedRfService


def create_rf_app(config_path: Path = DEFAULT_CONFIG):
    # import만으로 모델을 읽지 않고 lifespan 시작 때 검증·로딩한다.
    return create_app(lambda: ApprovedRfService(load_serving_config(config_path)))


app = create_rf_app()
