"""Tests for the localhost-only binding policy (roadmap §P1-3.2).

The server/ and web/ entry points are thin argparse wrappers around
``server/bind.py``; the security-relevant logic lives there and is tested here:
loopback binds always allowed; non-loopback binds fail-closed without the
explicit ``--unsafe`` flag.
"""

from __future__ import annotations

import pytest

from data_analysis_agent.server.bind import is_loopback, resolve_bind_host


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "localhost", "127.0.0.2", "127.8.8.8"],
)
def test_is_loopback_true(host: str) -> None:
    assert is_loopback(host)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "*", "192.168.1.5", "10.0.0.1", "172.16.3.9", "my-laptop.local", "example.com"],
)
def test_is_loopback_false(host: str) -> None:
    assert not is_loopback(host)


@pytest.mark.parametrize(
    "host",
    [
        # 伪装成 loopback 的输入 —— 必须全部判 False(fail-closed 方向),防回归。
        # 若未来 is_loopback 被"改进"为前缀匹配/DNS 解析,这些会被误判 loopback → 真实绕过。
        # 已逐一实测 ipaddress 行为(下方 test_is_loopback_true_ipv4_mapped 单列例外)。
        "127.1",  # 缩写形式,ipaddress 拒绝
        "0x7f000001",  # 十六进制,ipaddress 拒绝
        "0177.0.0.1",  # 八进制/前导零,ipaddress 拒绝
        "LOCALHOST",  # 大小写变体,字面量精确匹配不命中
        " localhost",  # 前导空格
        "localhost ",  # 尾随空格
        "localhost.",  # FQDN 点结尾
        "127.0.0.1.evil.com",  # 域名包含 loopback 前缀
        "0",  # 数字 0(=0.0.0.0 的缩写意图)
        "",  # 空串
    ],
)
def test_is_loopback_false_spoofed(host: str) -> None:
    assert not is_loopback(host)


def test_is_loopback_true_ipv4_mapped() -> None:
    # IPv4-mapped IPv6 "::ffff:127.0.0.1" 经 ipaddress 解析后指向真实 loopback 127.0.0.1,
    # is_loopback 判 True —— 这是正确且安全的(绑它等于绑 loopback),单独钉死以免误判为漏判。
    assert is_loopback("::ffff:127.0.0.1")


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_resolve_loopback_allowed_without_unsafe(host: str) -> None:
    assert resolve_bind_host(host, unsafe=False) == host


def test_resolve_non_loopback_refuses_without_unsafe() -> None:
    # _refuse prints the message then SystemExit(1); assert the exit, not the text.
    with pytest.raises(SystemExit):
        resolve_bind_host("0.0.0.0", unsafe=False)
    with pytest.raises(SystemExit):
        resolve_bind_host("192.168.1.5", unsafe=False)


def test_resolve_non_loopback_allowed_with_unsafe() -> None:
    assert resolve_bind_host("0.0.0.0", unsafe=True) == "0.0.0.0"
    assert resolve_bind_host("192.168.1.5", unsafe=True) == "192.168.1.5"
