from __future__ import annotations

import sys
import threading

import uvicorn


def main() -> None:
    server = uvicorn.Server(uvicorn.Config("inference_api.main:app", host="127.0.0.1", port=8000))

    def wait_for_quit() -> None:
        # Windows에서도 테스트가 소유한 API만 정상 종료하도록 표준 입력을 사용한다.
        if sys.stdin.readline().strip() == "quit":
            server.should_exit = True

    threading.Thread(target=wait_for_quit, name="test-shutdown-control", daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
