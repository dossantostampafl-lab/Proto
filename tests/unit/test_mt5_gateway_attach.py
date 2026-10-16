"""TerminalGateway: log in with a password, or attach to an already logged-in terminal."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from apps.mt5_bridge.gateway import TerminalGateway

LOGIN = 511447388
SERVER = "XPMT5-DEMO"
PATH = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"


class FakeMt5:
    def __init__(self, *, logged_in: int | None = LOGIN, server: str = SERVER) -> None:
        self.calls: list[dict] = []
        self.shut_down = False
        self._account = (
            SimpleNamespace(login=logged_in, server=server) if logged_in is not None else None
        )

    def initialize(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True

    def last_error(self):
        return (1, "Success")

    def account_info(self):
        return self._account

    def shutdown(self) -> None:
        self.shut_down = True


@pytest.fixture
def install(monkeypatch):
    def _install(fake: FakeMt5) -> FakeMt5:
        monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
        return fake

    return _install


def test_with_a_password_it_logs_in_as_before(install) -> None:
    fake = install(FakeMt5())
    TerminalGateway(login=LOGIN, password="s3cret", server=SERVER, path=PATH)
    assert fake.calls == [{"login": LOGIN, "password": "s3cret", "server": SERVER, "path": PATH}]


@pytest.mark.parametrize("password", [None, ""])
def test_without_a_password_it_attaches_and_sends_no_credentials(install, password) -> None:
    fake = install(FakeMt5())
    TerminalGateway(login=LOGIN, password=password, server=SERVER, path=PATH)
    assert fake.calls == [{"path": PATH}]
    assert fake.shut_down is False


@pytest.mark.parametrize(
    "fake",
    [
        FakeMt5(logged_in=999),  # another account is logged in
        FakeMt5(server="OTHER-SERVER"),  # same login, another server
        FakeMt5(logged_in=None),  # the terminal is not logged in at all
    ],
    ids=["other-login", "other-server", "not-logged-in"],
)
def test_attach_refuses_a_terminal_that_is_not_the_expected_account(install, fake) -> None:
    install(fake)
    with pytest.raises(RuntimeError, match="not logged in to the expected account"):
        TerminalGateway(login=LOGIN, password=None, server=SERVER)
    assert fake.shut_down is True
