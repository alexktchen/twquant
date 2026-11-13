"""整套測試共用的設定：測試不得連網（SPEC A-006 AC7）。"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """任何測試只要想連出去就會失敗；要測送出的程式請用假的連線。"""

    def refuse(self: socket.socket, address: object) -> None:
        self.close()
        raise AssertionError(f"測試不得連網：有程式想連到 {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
