from __future__ import annotations

import sys
import threading

import uvicorn


def main() -> None:
    server = uvicorn.Server(uvicorn.Config("inference_api.main:app", host="127.0.0.1", port=8000))

    def wait_for_quit() -> None:
        # 자동 수집기가 소유한 프로세스만 표준 입력으로 정상 종료한다. HTTP 제어 경로는 없다.
        if sys.stdin.readline().strip() == "quit":
            server.should_exit = True

    threading.Thread(target=wait_for_quit, name="collection-shutdown-control", daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
