#!/usr/bin/env python3
"""
DPI-aware VLESS checker for aggressive mobile networks (CGNAT + whitelist + early TLS drops).

Вывод:
1) JSON-массив с результатами по каждой прокси.
2) Краткая сводка (total/active/inactive).
3) Технический разбор принятых решений.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import socket
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "max_handshake_ms": 1200,
    "recv_timeout": 0.9,
    "probe_attempts": 3,
    "min_success": 1,
    "workers": 16,
    "max_parallel_per_host": 1,
    "min_bytes_received": 50,
    "max_latency_ms": 2000,
    "mobile_whitelist_enabled": True,
    "mobile_whitelist_fail_open": False,
    "mobile_whitelist_timeout_sec": 10,
    "mobile_whitelist_retries": 2,
    "mobile_whitelist_retry_sleep_sec": 1,
    "mobile_whitelist_domains_url": "https://raw.githubusercontent.com/hxehex/russia-mobile-internet-whitelist/refs/heads/main/whitelist.txt",
    "mobile_whitelist_ips_url": "https://raw.githubusercontent.com/hxehex/russia-mobile-internet-whitelist/refs/heads/main/ipwhitelist.txt",
    "mobile_whitelist_cidrs_url": "https://raw.githubusercontent.com/hxehex/russia-mobile-internet-whitelist/refs/heads/main/cidrwhitelist.txt",
    "http_probe_path": "/generate_204",
    "http_probe_host": "connectivitycheck.gstatic.com",
    "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
}


@dataclass
class AttemptSnapshot:
    attempt: int
    handshake_ms: int
    bytes_received: int
    duration_ms: int
    status: str
    error: str | None = None


@dataclass
class ProxyResult:
    proxy: str
    status: str
    reason: str | None
    snapshots: list[AttemptSnapshot]
    latency_ms: int | None = None
    bytes_received: int | None = None


class HostLimiter:
    def __init__(self, per_host_limit: int):
        self._per_host_limit = max(1, int(per_host_limit))
        self._locks: dict[str, asyncio.Semaphore] = {}
        self._guard = asyncio.Lock()

    async def sem(self, host: str) -> asyncio.Semaphore:
        async with self._guard:
            if host not in self._locks:
                self._locks[host] = asyncio.Semaphore(self._per_host_limit)
            return self._locks[host]


def load_config(path: str | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if not path:
        return cfg
    with open(path, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)
    if isinstance(user_cfg, dict):
        cfg.update(user_cfg)
    return cfg


def parse_vless(link: str) -> tuple[bool, str, int, str]:
    if not link.lower().startswith("vless://"):
        return False, "", 0, "malformed_vless_scheme"
    try:
        p = urllib.parse.urlparse(link.strip())
        host = (p.hostname or "").strip()
        port = int(p.port or 443)
        if not host or port <= 0:
            return False, "", 0, "malformed_vless_host_port"
        return True, host, port, ""
    except Exception:
        return False, "", 0, "malformed_vless_parse"


def _fetch_lines(url: str, timeout: float) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_CONFIG["user_agent"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="ignore")
    return [x.strip() for x in raw.splitlines() if x.strip()]


def load_mobile_whitelist(cfg: dict[str, Any]) -> dict[str, Any]:
    if not cfg.get("mobile_whitelist_enabled", True):
        return {"ok": False, "domains": set(), "ips": set(), "cidrs": [], "reason": "disabled"}
    retries = max(1, int(cfg.get("mobile_whitelist_retries", 2)))
    timeout = float(cfg.get("mobile_whitelist_timeout_sec", 10))
    sleep_sec = float(cfg.get("mobile_whitelist_retry_sleep_sec", 1))

    for i in range(retries):
        try:
            domains = set(_fetch_lines(cfg["mobile_whitelist_domains_url"], timeout))
            ips = set(_fetch_lines(cfg["mobile_whitelist_ips_url"], timeout))
            cidrs = [ipaddress.ip_network(x, strict=False) for x in _fetch_lines(cfg["mobile_whitelist_cidrs_url"], timeout)]
            return {"ok": True, "domains": domains, "ips": ips, "cidrs": cidrs, "reason": None}
        except Exception as e:
            if i + 1 == retries:
                return {"ok": False, "domains": set(), "ips": set(), "cidrs": [], "reason": str(e)}
            time.sleep(sleep_sec)
    return {"ok": False, "domains": set(), "ips": set(), "cidrs": [], "reason": "unknown"}


def in_whitelist(host: str, wl: dict[str, Any]) -> bool:
    if not wl.get("ok"):
        return False
    h = host.lower().strip()
    if h in wl["domains"]:
        return True
    try:
        ip_obj = ipaddress.ip_address(h)
    except ValueError:
        ip_obj = None
    if ip_obj is not None:
        if h in wl["ips"]:
            return True
        return any(ip_obj in n for n in wl["cidrs"])
    for dom in wl["domains"]:
        if h == dom or h.endswith("." + dom):
            return True
    return False


async def _single_attempt(
    host: str,
    port: int,
    cfg: dict[str, Any],
    attempt_no: int,
) -> AttemptSnapshot:
    start = time.perf_counter()
    hs_ms = 0
    bytes_recv = 0
    err_msg = None
    status = "inactive"
    writer = None
    try:
        hs_timeout = max(0.2, float(cfg["max_handshake_ms"]) / 1000.0)
        conn_start = time.perf_counter()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=hs_timeout)
        hs_ms = int((time.perf_counter() - conn_start) * 1000)

        # TLS mimic + minimal HTTP request.
        req = (
            f"GET {cfg['http_probe_path']} HTTP/1.1\r\n"
            f"Host: {cfg['http_probe_host']}\r\n"
            f"User-Agent: {cfg['user_agent']}\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(req)
        await writer.drain()

        data = await asyncio.wait_for(reader.read(512), timeout=float(cfg["recv_timeout"]))
        bytes_recv = len(data)
        duration_ms = int((time.perf_counter() - start) * 1000)

        if hs_ms > int(cfg["max_handshake_ms"]):
            return AttemptSnapshot(attempt_no, hs_ms, bytes_recv, duration_ms, "inactive", "slow_handshake")
        if duration_ms <= 1000 and bytes_recv < int(cfg["min_bytes_received"]):
            return AttemptSnapshot(attempt_no, hs_ms, bytes_recv, duration_ms, "inactive", "early_termination_dpi_pattern")
        if bytes_recv < int(cfg["min_bytes_received"]):
            return AttemptSnapshot(attempt_no, hs_ms, bytes_recv, duration_ms, "inactive", "dpi_drop_bytes_lt_min")
        if hs_ms > int(cfg["max_latency_ms"]):
            return AttemptSnapshot(attempt_no, hs_ms, bytes_recv, duration_ms, "inactive", "latency_too_high")
        return AttemptSnapshot(attempt_no, hs_ms, bytes_recv, duration_ms, "active", None)
    except (asyncio.TimeoutError, TimeoutError):
        err_msg = "tcp_or_l7_timeout"
    except (ConnectionError, socket.gaierror, ssl.SSLError, OSError) as e:
        err_msg = f"tcp_or_tls_error:{type(e).__name__}"
    except Exception as e:  # noqa: BLE001 - explicit classification path
        err_msg = f"unexpected_error:{type(e).__name__}"
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
    duration_ms = int((time.perf_counter() - start) * 1000)
    status = "inactive"
    return AttemptSnapshot(attempt_no, hs_ms, bytes_recv, duration_ms, status, err_msg)


async def check_one(
    proxy: str,
    cfg: dict[str, Any],
    wl: dict[str, Any],
    host_limiter: HostLimiter,
) -> ProxyResult:
    ok, host, port, parse_reason = parse_vless(proxy)
    if not ok:
        return ProxyResult(proxy=proxy, status="Inactive", reason=parse_reason, snapshots=[])

    if cfg.get("mobile_whitelist_enabled", True):
        if wl.get("ok"):
            if not in_whitelist(host, wl):
                return ProxyResult(proxy=proxy, status="Inactive", reason="not_in_mobile_whitelist", snapshots=[])
        elif not cfg.get("mobile_whitelist_fail_open", False):
            return ProxyResult(proxy=proxy, status="Inactive", reason=f"whitelist_unavailable:{wl.get('reason')}", snapshots=[])

    snapshots: list[AttemptSnapshot] = []
    sem = await host_limiter.sem(host)
    async with sem:
        for i in range(1, max(1, int(cfg["probe_attempts"])) + 1):
            snap = await _single_attempt(host, port, cfg, i)
            snapshots.append(snap)
            active_hits = sum(1 for x in snapshots if x.status == "active")
            if active_hits >= max(1, int(cfg["min_success"])):
                return ProxyResult(
                    proxy=proxy,
                    status="Active",
                    reason=None,
                    snapshots=snapshots,
                    latency_ms=snap.handshake_ms,
                    bytes_received=snap.bytes_received,
                )

    last_reason = snapshots[-1].error if snapshots else "no_attempts"
    return ProxyResult(proxy=proxy, status="Inactive", reason=last_reason, snapshots=snapshots)


async def run_all(proxies: list[str], cfg: dict[str, Any]) -> list[ProxyResult]:
    wl = load_mobile_whitelist(cfg)
    limiter = HostLimiter(cfg.get("max_parallel_per_host", 1))
    workers = max(1, int(cfg.get("workers", 16)))
    sem = asyncio.Semaphore(workers)

    async def worker(p: str) -> ProxyResult:
        async with sem:
            return await check_one(p, cfg, wl, limiter)

    return await asyncio.gather(*(worker(p) for p in proxies))


def read_proxies(path: str) -> list[str]:
    proxies: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.lower().startswith("vless://"):
                proxies.append(s)
    return proxies


def print_report(results: list[ProxyResult]) -> None:
    payload = []
    active = 0
    inactive = 0
    for r in results:
        if r.status == "Active":
            active += 1
        else:
            inactive += 1
        item = {
            "proxy": r.proxy,
            "status": r.status,
        }
        if r.reason:
            item["reason"] = r.reason
        if r.latency_ms is not None:
            item["latency_ms"] = r.latency_ms
        if r.bytes_received is not None:
            item["bytes_received"] = r.bytes_received
        item["snapshots"] = [asdict(s) for s in r.snapshots]
        payload.append(item)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()
    print("Summary:")
    print(f"- Total proxies checked: {len(results)}")
    print(f"- Total active proxies: {active}")
    print(f"- Total inactive proxies: {inactive}")
    print()
    print("Технический анализ:")
    print("- Чекер работает в fail-fast режиме: короткий handshake timeout и быстрый отсев по ранним сбоям.")
    print("- Включен L4/L7 агрессивный фильтр: slow handshake, раннее завершение и паттерн малого ответа (<min_bytes_received).")
    print("- Учитывается mobile whitelist (domains/IP/CIDR), при недоступности источников по умолчанию fail-closed.")
    print("- Для снижения ложноположительных используется per-host лимит параллелизма и сбор snapshot-метрик на каждую попытку.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DPI-aware mobile VLESS checker")
    p.add_argument("--input", required=True, help="Файл со списком VLESS ссылок (по одной на строку)")
    p.add_argument("--config", required=False, help="JSON-конфиг (необязательно)")
    p.add_argument("--output", required=False, help="Куда сохранить JSON-отчет")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    proxies = read_proxies(args.input)
    results = asyncio.run(run_all(proxies, cfg))
    print_report(results)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "proxy": r.proxy,
                        "status": r.status,
                        "reason": r.reason,
                        "latency_ms": r.latency_ms,
                        "bytes_received": r.bytes_received,
                        "snapshots": [asdict(s) for s in r.snapshots],
                    }
                    for r in results
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )


if __name__ == "__main__":
    main()

