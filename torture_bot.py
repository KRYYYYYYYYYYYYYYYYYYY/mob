import socket
import time
import os
import asyncio
import ssl
import re
import json
import ctypes
import ipaddress
import urllib.parse
import requests
import psutil
from concurrent.futures import ThreadPoolExecutor
import threading
from ua_versions import get_mobile_user_agents, maybe_refresh_ua_versions
from stress_profile_loader import load_stress_profile_file
from mobile_vless_checker import (
    HostLimiter as AsyncHostLimiter,
    check_one as async_check_one,
    load_mobile_whitelist as async_load_mobile_whitelist,
)

# --- КОНФИГУРАЦИЯ ---
ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}
RANK_FILE = 'test1/ranking.json'
PINNED_FILE = 'test1/pinned.txt'
VETTED_FILE = 'test1/vetted.txt'
BLACKLIST_FILE = 'test1/blacklist.txt'
WIFI_FILE = 'kr/mob/wifi.txt'
DEFERRED_FILE = 'test1/deferred.txt'
INPUT_FILE = 'test1/1.txt'
FAVORITES_FILE = 'test1/favorites.txt'
PROFILE_FILE = 'test1/stress_profile.json'
COUNTRY_CACHE_FILE = 'test1/countries_cache.json'
THRESHOLD = 50

DEFAULT_PROBE_PATHS = ["/", "/generate_204", "/favicon.ico"]
maybe_refresh_ua_versions()
HAPP_UA, V2RAYNG_UA = get_mobile_user_agents()
DEFAULT_MOBILE_HEADER_PROFILES = [
    {
        "user_agent": HAPP_UA,
        "headers": {
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
            "X-Requested-With": "com.happproxy",
        },
    },
    {
        "user_agent": V2RAYNG_UA,
        "headers": {
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
            "X-Requested-With": "com.v2ray.ang",
        },
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
            "Sec-CH-UA": "\"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\"",
            "Sec-CH-UA-Mobile": "?1",
            "Sec-CH-UA-Platform": "\"Android\"",
        },
    },
]
DEFAULT_MOBILE_WHITELIST = {
    "domains_url": "https://raw.githubusercontent.com/hxehex/russia-mobile-internet-whitelist/refs/heads/main/whitelist.txt",
    "ips_url": "https://raw.githubusercontent.com/hxehex/russia-mobile-internet-whitelist/refs/heads/main/ipwhitelist.txt",
    "cidrs_url": "https://raw.githubusercontent.com/hxehex/russia-mobile-internet-whitelist/refs/heads/main/cidrwhitelist.txt",
}

file_lock = threading.Lock()
go_lib = None
_MOBILE_WHITELIST_CACHE = None
_MOBILE_WHITELIST_LOCK = threading.Lock()
_ASYNC_MOBILE_WHITELIST_CACHE = None


def async_mobile_probe_torture(link: str, stress_config: dict) -> bool:
    global _ASYNC_MOBILE_WHITELIST_CACHE
    async_cfg = {
        "max_handshake_ms": int(float(stress_config.get("timeout", 1.2)) * 1000),
        "recv_timeout": float(stress_config.get("recv_timeout", 0.9)),
        "probe_attempts": int(stress_config.get("probe_attempts", 3)),
        "min_success": int(stress_config.get("l7_min_success", 1)),
        "workers": int(stress_config.get("workers", 16)),
        "max_parallel_per_host": 1,
        "min_bytes_received": int(stress_config.get("min_bytes_received", 50)),
        "max_latency_ms": int(stress_config.get("max_latency_ms", 2000)),
        "mobile_whitelist_enabled": bool(stress_config.get("mobile_whitelist_enabled", True)),
        "mobile_whitelist_fail_open": bool(stress_config.get("mobile_whitelist_fail_open", False)),
        "mobile_whitelist_timeout_sec": float(stress_config.get("mobile_whitelist_timeout_sec", 10)),
        "mobile_whitelist_retries": int(stress_config.get("mobile_whitelist_retries", 2)),
        "mobile_whitelist_retry_sleep_sec": float(stress_config.get("mobile_whitelist_retry_sleep_sec", 1)),
        "mobile_whitelist_domains_url": str(stress_config.get("mobile_whitelist_domains_url", DEFAULT_MOBILE_WHITELIST["domains_url"])),
        "mobile_whitelist_ips_url": str(stress_config.get("mobile_whitelist_ips_url", DEFAULT_MOBILE_WHITELIST["ips_url"])),
        "mobile_whitelist_cidrs_url": str(stress_config.get("mobile_whitelist_cidrs_url", DEFAULT_MOBILE_WHITELIST["cidrs_url"])),
        "http_probe_path": "/generate_204",
        "http_probe_host": "connectivitycheck.gstatic.com",
        "user_agent": DEFAULT_MOBILE_HEADER_PROFILES[-1]["user_agent"],
    }
    if _ASYNC_MOBILE_WHITELIST_CACHE is None:
        _ASYNC_MOBILE_WHITELIST_CACHE = async_load_mobile_whitelist(async_cfg)
    limiter = AsyncHostLimiter(1)

    async def _run():
        return await async_check_one(link, async_cfg, _ASYNC_MOBILE_WHITELIST_CACHE, limiter)

    try:
        result = asyncio.run(_run())
        return result.status == "Active"
    except Exception:
        return False

def normalize_rank_entry(base: str, data):
    """Унифицирует формат ranking.json: dict(rank, link, ...)."""
    if isinstance(data, dict):
        rank = int(data.get("rank", 0) or 0)
        entry = dict(data)
        entry["rank"] = max(0, rank)
        entry["link"] = entry.get("link", base)
        return entry
    if isinstance(data, (int, float)):
        return {"rank": max(0, int(data)), "link": base}
    return {"rank": 0, "link": base}

def decrease_rank(ranking_db, base, delta=30, note="FAIL"):
    """Безопасно снижает ранг даже для legacy-формата (int)."""
    if base not in ranking_db:
        return
    entry = normalize_rank_entry(base, ranking_db[base])
    entry["rank"] = max(0, int(entry.get("rank", 0)) - int(delta))
    entry["last_torture"] = note
    ranking_db[base] = entry

def l7_multi_probe(link: str, stress_config: dict, fallback_sni: str) -> bool:
    """Проверка кворумом по нескольким SNI-кандидатам для лучшего совпадения с мобилкой."""
    if os.getenv("USE_ASYNC_MOBILE_CHECKER", "1") == "1":
        return async_mobile_probe_torture(link, stress_config)

    timeout_sec = max(1, int(stress_config.get("timeout", 3)))
    min_hits = max(1, int(stress_config.get("l7_min_success", 2)))
    max_candidates = max(1, int(stress_config.get("l7_max_candidates", 3)))
    probe_attempts = max(1, int(stress_config.get("probe_attempts", 4)))
    between_attempts_sleep = max(0.0, float(stress_config.get("between_attempts_sleep", 0.35)))
    sni_candidates = extract_sni_candidates(link)
    if fallback_sni and fallback_sni not in sni_candidates:
        sni_candidates.append(fallback_sni)
    sni_candidates = [c for c in sni_candidates if c]
    sni_candidates = sni_candidates[:max_candidates]
    hits = 0
    for candidate_sni in sni_candidates:
        for _ in range(probe_attempts):
            if probe_vless_l7(link, candidate_sni, timeout_sec=timeout_sec) > 0:
                hits += 1
                if hits >= min_hits:
                    return True
            if between_attempts_sleep > 0:
                time.sleep(between_attempts_sleep)
    return False

def init_checker_lib():
    """Подключает Go L7 checker (libchecker.so) для инспектора mob."""
    global go_lib
    lib_path = os.path.abspath("libchecker.so")
    if not os.path.exists(lib_path):
        print("⚠️ libchecker.so не найден, инспектор продолжит legacy-проверками.")
        return

    go_lib = ctypes.cdll.LoadLibrary(lib_path)
    go_lib.CheckVlessL7.argtypes = [
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int
    ]
    go_lib.CheckVlessL7.restype = ctypes.c_int
    if hasattr(go_lib, "CheckAnyL7"):
        go_lib.CheckAnyL7.argtypes = [
            ctypes.c_char_p,  # scheme
            ctypes.c_char_p,  # addr
            ctypes.c_int,     # port
            ctypes.c_char_p,  # id
            ctypes.c_char_p,  # security
            ctypes.c_char_p,  # sni
            ctypes.c_char_p,  # pbk
            ctypes.c_char_p,  # sid
            ctypes.c_char_p,  # fp
            ctypes.c_char_p,  # flow
            ctypes.c_char_p,  # net_type
            ctypes.c_char_p,  # path
            ctypes.c_char_p,  # host_hdr
            ctypes.c_char_p,  # method
            ctypes.c_char_p,  # password
            ctypes.c_int,     # timeout
        ]
        go_lib.CheckAnyL7.restype = ctypes.c_int
    if hasattr(go_lib, "SetProbeProfilesJSON"):
        go_lib.SetProbeProfilesJSON.argtypes = [ctypes.c_char_p]
        go_lib.SetProbeProfilesJSON.restype = ctypes.c_int

def configure_go_probe_profiles(stress_config):
    if go_lib is None or not hasattr(go_lib, "SetProbeProfilesJSON"):
        return
    profiles = []
    raw = stress_config.get("mobile_header_profiles")
    if isinstance(raw, list):
        for p in raw:
            if not isinstance(p, dict):
                continue
            ua = str(p.get("user_agent", "")).strip()
            if not ua:
                continue
            headers = p.get("headers", {}) if isinstance(p.get("headers"), dict) else {}
            profiles.append({"user_agent": ua, "headers": headers})
    if not profiles:
        profiles = list(DEFAULT_MOBILE_HEADER_PROFILES)
    try:
        payload = json.dumps(profiles, ensure_ascii=False).encode("utf-8")
        rc = int(go_lib.SetProbeProfilesJSON(payload))
        if rc == 1:
            print(f"🧩 [TORTURE] Go probe profiles configured: {len(profiles)}")
    except Exception as e:
        print(f"⚠️ [TORTURE] Не удалось передать профили в Go checker: {e}")


def extract_sni(link: str) -> str:
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    return params.get("sni", [""])[0]

def _normalize_domain(value: str) -> str:
    d = (value or "").strip().lower().rstrip(".")
    if not d:
        return ""
    if "://" in d:
        try:
            p = urllib.parse.urlparse(d)
            d = (p.hostname or "").strip().lower().rstrip(".")
        except Exception:
            return ""
    return d

def _is_domain_allowed(domain: str, allow_domains: set[str]) -> bool:
    d = _normalize_domain(domain)
    if not d:
        return False
    if d in allow_domains:
        return True
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        tail = ".".join(parts[i:])
        if tail in allow_domains:
            return True
    return False

def _download_lines(url: str, timeout: float = 20.0) -> list[str]:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    out = []
    for line in resp.text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out

def load_mobile_whitelist(config: dict) -> dict:
    domains_url = str(config.get("mobile_whitelist_domains_url", DEFAULT_MOBILE_WHITELIST["domains_url"])).strip()
    ips_url = str(config.get("mobile_whitelist_ips_url", DEFAULT_MOBILE_WHITELIST["ips_url"])).strip()
    cidrs_url = str(config.get("mobile_whitelist_cidrs_url", DEFAULT_MOBILE_WHITELIST["cidrs_url"])).strip()
    timeout = float(config.get("mobile_whitelist_timeout_sec", 20.0))
    domains, ips, cidrs = set(), set(), []
    for item in _download_lines(domains_url, timeout=timeout):
        d = _normalize_domain(item)
        if d:
            domains.add(d)
    for item in _download_lines(ips_url, timeout=timeout):
        try:
            ips.add(str(ipaddress.ip_address(item.strip())))
        except Exception:
            pass
    for item in _download_lines(cidrs_url, timeout=timeout):
        try:
            cidrs.append(ipaddress.ip_network(item.strip(), strict=False))
        except Exception:
            pass
    return {"ok": True, "domains": domains, "ips": ips, "cidrs": cidrs}

def get_mobile_whitelist(config: dict):
    global _MOBILE_WHITELIST_CACHE
    with _MOBILE_WHITELIST_LOCK:
        if _MOBILE_WHITELIST_CACHE is not None and _MOBILE_WHITELIST_CACHE.get("ok"):
            return _MOBILE_WHITELIST_CACHE
        retries = max(1, int(config.get("mobile_whitelist_retries", 3)))
        sleep_sec = float(config.get("mobile_whitelist_retry_sleep_sec", 2.0))
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                _MOBILE_WHITELIST_CACHE = load_mobile_whitelist(config)
                wl = _MOBILE_WHITELIST_CACHE
                print(f"✅ [TORTURE] mobile whitelist loaded: domains={len(wl['domains'])}, ips={len(wl['ips'])}, cidrs={len(wl['cidrs'])}")
                return wl
            except Exception as e:
                last_error = e
                if attempt < retries:
                    time.sleep(max(0.0, sleep_sec))
        _MOBILE_WHITELIST_CACHE = {"ok": False, "domains": set(), "ips": set(), "cidrs": [], "error": str(last_error)}
        print(f"⚠️ [TORTURE] mobile whitelist unavailable: {last_error}")
        return _MOBILE_WHITELIST_CACHE

def is_link_in_mobile_whitelist(link: str, whitelist: dict) -> bool:
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    candidates = []
    for k in ("sni", "host"):
        v = params.get(k, [""])[0].strip()
        if v:
            candidates.append(v)
    if parsed.hostname:
        candidates.append(parsed.hostname)
    for v in candidates:
        try:
            ip = ipaddress.ip_address(v)
            if str(ip) in whitelist.get("ips", set()):
                return True
            if any(ip in net for net in whitelist.get("cidrs", [])):
                return True
        except Exception:
            if _is_domain_allowed(v, whitelist.get("domains", set())):
                return True
    return False

def extract_sni_candidates(link: str):
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    candidates = []
    for key in ("sni", "host"):
        value = params.get(key, [""])[0].strip()
        if value and value not in candidates:
            candidates.append(value)
    if parsed.hostname and parsed.hostname not in candidates:
        candidates.append(parsed.hostname)
    return candidates

def probe_vless_l7(link: str, target_sni: str, timeout_sec: int = 5) -> int:
    if go_lib is None:
        return 0

    try:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        host, port = extract_host_port(link)
        if not host or not port:
            return 0
        uuid = parsed.username if parsed.username else ""
        security = (params.get("security", [""])[0] or "").lower()
        if security == "xtls":
            security = "tls"
        net_type = (params.get("type", ["tcp"])[0] or "tcp").lower()
        if net_type == "httpupgrade":
            net_type = "ws"
        path = params.get("path", [""])[0] or ""
        host_hdr = params.get("host", [""])[0] or ""
        pbk = params.get("pbk", [""])[0] or ""
        sid = params.get("sid", [""])[0] or ""
        fp = params.get("fp", [""])[0] or ""
        flow = params.get("flow", [""])[0] or ""
        sni = target_sni or params.get("sni", [host])[0] or host

        if hasattr(go_lib, "CheckAnyL7"):
            return int(go_lib.CheckAnyL7(
                b"vless",
                host.encode("utf-8"),
                int(port),
                uuid.encode("utf-8"),
                security.encode("utf-8"),
                sni.encode("utf-8"),
                pbk.encode("utf-8"),
                sid.encode("utf-8"),
                fp.encode("utf-8"),
                flow.encode("utf-8"),
                net_type.encode("utf-8"),
                path.encode("utf-8"),
                host_hdr.encode("utf-8"),
                b"",
                b"",
                int(timeout_sec),
            ))

        return int(go_lib.CheckVlessL7(
            host.encode('utf-8'),
            int(port),
            uuid.encode('utf-8'),
            sni.encode('utf-8'),
            pbk.encode('utf-8'),
            sid.encode('utf-8'),
            flow.encode('utf-8'),
            int(timeout_sec)
        ))
    except Exception:
        return 0

def get_wifi_candidates(pinned_list, fav_list=None):
    """Загружает сервера из wifi.txt, исключая закрепы и избранные."""
    if fav_list is None: fav_list = []
    if not os.path.exists(WIFI_FILE):
        return []
    
    # Собираем базы для исключения (всё, что до #)
    excluded_bases = {p.split('#')[0].strip() for p in pinned_list}
    excluded_bases.update({f.split('#')[0].strip() for f in fav_list})
    
    candidates = []
    with open(WIFI_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or 'vless://' not in line:
                continue
            
            base = line.split('#')[0].strip()
            if base not in excluded_bases:
                candidates.append(line) 
                
    return candidates

def add_to_blacklist(base_part):
    """Добавляет сервер в бан-лист, игнорируя дубликаты"""
    existing = set()
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            existing = {line.strip() for line in f if line.strip()}
    
    if base_part not in existing:
        with open(BLACKLIST_FILE, 'a', encoding='utf-8') as f:
            f.write(base_part + "\n")
        print(f"💀 [BLACKLIST] Забанен: {base_part[:30]}...")

def remove_from_all(base_part):
    for path in [WIFI_FILE, DEFERRED_FILE, INPUT_FILE, VETTED_FILE]: 
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            # Сравниваем только левую часть до знака #
            new_lines = [l for l in lines if l.split('#')[0].strip() != base_part]
            if len(lines) != len(new_lines):
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                print(f" 🧹 [УДАЛЕНИЕ] Сервер {base_part[:20]}... вырезан из {path}")

# --- НОВАЯ ФУНКЦИЯ ЗАГРУЗКИ КОНФИГА ---
def load_stress_config():
    config = {
        "timeout": 1.2,
        "dpi_sleep": 0.5,
        "recv_timeout": 0.9,
        "between_attempts_sleep": 0.2,
        "probe_attempts": 3,
        "min_success": 1,
        "torture_total_attempts": 20,
        "torture_min_success": 12,
        "torture_cycle_sleep": 60,
        "l7_min_success": 1,
        "l7_max_candidates": 2,
        "workers": 16,
        "probe_paths": ["/generate_204"],
        "mobile_header_profiles": list(DEFAULT_MOBILE_HEADER_PROFILES),
        "mobile_whitelist_enabled": True,
        "mobile_whitelist_fail_open": False,
        "mobile_whitelist_timeout_sec": 10.0,
        "mobile_whitelist_retries": 2,
        "mobile_whitelist_retry_sleep_sec": 1.0,
        "mobile_whitelist_domains_url": DEFAULT_MOBILE_WHITELIST["domains_url"],
        "mobile_whitelist_ips_url": DEFAULT_MOBILE_WHITELIST["ips_url"],
        "mobile_whitelist_cidrs_url": DEFAULT_MOBILE_WHITELIST["cidrs_url"],
    }
    data = load_stress_profile_file()
    if data:
        try:
            config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
            config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
            config["recv_timeout"] = float(data.get("recv_timeout", config["recv_timeout"]))
            config["between_attempts_sleep"] = float(data.get("between_attempts_sleep", config["between_attempts_sleep"]))
            config["probe_attempts"] = int(data.get("probe_attempts", config["probe_attempts"]))
            config["min_success"] = int(data.get("min_success", config["min_success"]))
            config["torture_total_attempts"] = int(data.get("torture_total_attempts", config["torture_total_attempts"]))
            config["torture_min_success"] = int(data.get("torture_min_success", config["torture_min_success"]))
            config["torture_cycle_sleep"] = int(data.get("torture_cycle_sleep", config["torture_cycle_sleep"]))
            config["l7_min_success"] = int(data.get("l7_min_success", config["l7_min_success"]))
            config["l7_max_candidates"] = int(data.get("l7_max_candidates", config["l7_max_candidates"]))
            config["workers"] = int(data.get("workers", config["workers"]))
            if isinstance(data.get("probe_paths"), list) and data.get("probe_paths"):
                config["probe_paths"] = [str(x) for x in data["probe_paths"] if str(x).strip()]
            if isinstance(data.get("mobile_header_profiles"), list) and data.get("mobile_header_profiles"):
                config["mobile_header_profiles"] = data.get("mobile_header_profiles")
            config["mobile_whitelist_enabled"] = bool(data.get("mobile_whitelist_enabled", config["mobile_whitelist_enabled"]))
            config["mobile_whitelist_fail_open"] = bool(data.get("mobile_whitelist_fail_open", config["mobile_whitelist_fail_open"]))
            config["mobile_whitelist_timeout_sec"] = float(data.get("mobile_whitelist_timeout_sec", config["mobile_whitelist_timeout_sec"]))
            config["mobile_whitelist_retries"] = int(data.get("mobile_whitelist_retries", config["mobile_whitelist_retries"]))
            config["mobile_whitelist_retry_sleep_sec"] = float(data.get("mobile_whitelist_retry_sleep_sec", config["mobile_whitelist_retry_sleep_sec"]))
            config["mobile_whitelist_domains_url"] = str(data.get("mobile_whitelist_domains_url", config["mobile_whitelist_domains_url"])).strip() or config["mobile_whitelist_domains_url"]
            config["mobile_whitelist_ips_url"] = str(data.get("mobile_whitelist_ips_url", config["mobile_whitelist_ips_url"])).strip() or config["mobile_whitelist_ips_url"]
            config["mobile_whitelist_cidrs_url"] = str(data.get("mobile_whitelist_cidrs_url", config["mobile_whitelist_cidrs_url"])).strip() or config["mobile_whitelist_cidrs_url"]
        except Exception:
            pass
    return config

def get_country(host):
    if not os.path.exists(COUNTRY_CACHE_FILE):
        cache = {}
    else:
        try:
            with open(COUNTRY_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    if host in cache:
        return cache[host]

    try:
        resp = requests.get(f"http://ip-api.com/json/{host}?fields=status,countryCode", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                code = data.get("countryCode", "??")
                cache[host] = code
                with open(COUNTRY_CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cache, f)
                return code
    except Exception:
        pass
    return "??"

# --- БРОНЕБОЙНЫЙ ИЗВЛЕКАТЕЛЬ ---
def extract_host_port(link: str):
    # Сначала пробуем IPv4/Домен, затем IPv6
    match = re.search(r'@([\w\.-]+):(\d+)(?=[/?#]|$)', link)
    if not match:
        match = re.search(r'@\[([0-9a-fA-F:]+)\]:(\d+)(?=[/?#]|$)', link)
    
    if match:
        host = match.group(1)
        try:
            port = int(match.group(2))
            return (host, port) if 1 <= port <= 65535 else (None, None)
        except Exception:
            pass
    return None, None


# --- ОБНОВЛЕННАЯ ПЫТКА ---
def torture_check(link, stress_config, resolved_ip):
    host, port = extract_host_port(link)
    if not host or not port:

        return False, 0, 0
    is_tls = "security=tls" in link.lower() or "security=reality" in link.lower()
    
    sni = re.search(r"sni=([^&?#]+)", link)
    server_hostname = sni.group(1) if sni else host

    # Юзер-агенты из профилей заголовков (единый источник правды).
    header_profiles = stress_config.get("mobile_header_profiles") or DEFAULT_MOBILE_HEADER_PROFILES
    user_agents = [str(p.get("user_agent", "")).strip() for p in header_profiles if isinstance(p, dict) and str(p.get("user_agent", "")).strip()]
    if not user_agents:
        user_agents = [p["user_agent"] for p in DEFAULT_MOBILE_HEADER_PROFILES]
    probe_paths = stress_config.get("probe_paths") or DEFAULT_PROBE_PATHS

    total_attempts = max(1, int(stress_config.get("torture_total_attempts", 20)))
    min_success = max(1, int(stress_config.get("torture_min_success", total_attempts)))
    min_success = min(min_success, total_attempts)

    success = 0
    for i in range(total_attempts):
        fallback_sni = extract_sni(link) or server_hostname
        l7_ok = l7_multi_probe(link, stress_config, fallback_sni)
        if l7_ok:
            success += 1
            if success >= min_success:
                return True, success, total_attempts
            if i < total_attempts - 1:
                time.sleep(stress_config.get("between_attempts_sleep", 0.35))
            continue

        ua = user_agents[i % len(user_agents)]
        path = probe_paths[i % len(probe_paths)]
        payload = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {server_hostname}\r\n"
            f"User-Agent: {ua}\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode()

        try:
            # Коннектимся строго по IP
            with socket.create_connection((resolved_ip, port), timeout=stress_config["timeout"]) as s:
                if is_tls:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                    with ctx.wrap_socket(s, server_hostname=server_hostname) as ssock:
                        # Каждую попытку шлем запрос (в тортурере халявы нет)
                        ssock.sendall(payload)
                        if stress_config["dpi_sleep"] > 0:
                            time.sleep(stress_config["dpi_sleep"])
                        ssock.settimeout(stress_config.get("recv_timeout", 1.7))
                        if not ssock.recv(8):
                            raise RuntimeError("Drop")
                else:
                    s.sendall(b'\x05\x01\x00')
                    s.settimeout(stress_config.get("recv_timeout", 1.7))
                    if not s.recv(2):
                        raise RuntimeError("Dead")

            success += 1
            if (i + 1) % 5 == 0:
                print(f"    ⛓️  Пытка {host[:15]}: {i + 1}/{total_attempts} OK")

            if success >= min_success:
                return True, success, total_attempts

            if i < total_attempts - 1:
                time.sleep(stress_config.get("torture_cycle_sleep", 60))
        except Exception:
            if i < total_attempts - 1:
                time.sleep(stress_config.get("between_attempts_sleep", 0.35))

    return False, success, total_attempts

def is_ipv6(host):
    """Проверяет наличие двоеточия, что характерно для IPv6"""
    return ":" in host if host else False

def main_torturer():
    # --- СНАЧАЛА ЗАГРУЖАЕМ ВСЁ ИЗ ФАЙЛОВ ---
    ranking_db = {}
    if os.path.exists(RANK_FILE):
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                ranking_db = {base: normalize_rank_entry(base, data) for base, data in loaded.items()}
            
    def load_lines(path):
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return [l.strip() for l in f if 'vless' in l]
        return []

    vetted_list = load_lines(VETTED_FILE)
    pinned_list = load_lines(PINNED_FILE)

    # --- ШАГ 1: ПОДГОТОВКА ---
    print("🚀 Начинаю работу...")

    # Проверка на дубликаты процесса
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            if proc.info['pid'] != os.getpid() and 'torture_bot.py' in ' '.join(proc.info['cmdline']):
                print("🛑 Бот уже запущен. Выхожу.")
                return
        except Exception: continue

    stress_config = load_stress_config()
    configure_go_probe_profiles(stress_config)
    mobile_whitelist = None
    if stress_config.get("mobile_whitelist_enabled", True):
        mobile_whitelist = get_mobile_whitelist(stress_config)

    working_for_base = list(ranking_db.keys())

    # --- В ЭТОЙ ВЕРСИИ БОТ ТОЛЬКО ИНСПЕКТИРУЕТ ---
    print("⏰ Перехожу к инспекции (пыткам)...")

    # --- ШАГ 4: ПЕРЕХОД К ПЫТКАМ ---
    if not ranking_db:
        print("⌛ База пуста. Пытать некого.")
        return
    # Подготовка множеств для пыток
    vetted_set = {l.split('#')[0].strip() for l in vetted_list}
    pinned_set = {l.split('#')[0].strip() for l in pinned_list}
    
    print(f"🕵️ Начинаю инспекцию для {len(ranking_db)} кандидатов...")
    # ... дальше твой ThreadPoolExecutor без изменений ...

    # Проверка кандидатов
    candidates = []
    seen_addresses = set() # Сюда пишем хост:порт

    for base, data in ranking_db.items():
        entry = normalize_rank_entry(base, data)
        rank = entry.get("rank", 0)
        link = entry.get("link", base)
        
        host, port = extract_host_port(base)
        if not host or not port:
            continue
        addr = f"{host}:{port}"

        if (rank >= THRESHOLD) and base not in vetted_set and base not in pinned_set:
            if addr not in seen_addresses:
                candidates.append((base, link))
                seen_addresses.add(addr)
            else:
                print(f"♻️ Пропуск дубля по IP: {addr}")

    # ВОТ СЮДА ВСТАВЛЯЙ:
    print(f"DEBUG: Всего кандидатов после фильтрации: {len(candidates)}")
    if not candidates:
        print("DEBUG: Пытать некого, все отфильтровано.")

    if candidates:
        def run_torture(item):
            base, full_link = item
            host, _ = extract_host_port(base)

            # --- ЖЕСТКИЙ ФИЛЬТР IPv6 В ИНСПЕКТОРЕ ---
            if host and is_ipv6(host):
                print(f"🚫 [INSPECTOR BANNED IPv6]: {host}")
                add_to_blacklist(base)
                remove_from_all(base)
                return base, full_link, False, "IPv6_BAN", 0, 0
            # ----------------------------------------
            
            try:
                if stress_config.get("mobile_whitelist_enabled", True):
                    if mobile_whitelist and mobile_whitelist.get("ok"):
                        if not is_link_in_mobile_whitelist(base, mobile_whitelist):
                            return base, full_link, False, "WL", 0, 0
                    elif not stress_config.get("mobile_whitelist_fail_open", False):
                        return base, full_link, False, "WL_UNAVAILABLE", 0, 0

                infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
                resolved_ip = infos[0][4][0] if infos else None
                if not resolved_ip:
                    return base, full_link, False, "ERROR", 0, 0

                if get_country(resolved_ip) not in ALLOWED_COUNTRIES:
                    return base, full_link, False, "GEO", 0, 0

                ok, success_hits, total_hits = torture_check(full_link, stress_config, resolved_ip)
                return base, full_link, ok, "OK", success_hits, total_hits
            except Exception:
                return base, full_link, False, "ERROR", 0, 0

        workers = max(1, int(stress_config.get("workers", 16)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # Передавай конфиг явно в каждый поток
            results = list(executor.map(run_torture, candidates))

        for base, full_link, success, status, success_hits, total_hits in results:
            if success:
                with file_lock:
                    # Считываем текущих элитариев, чтобы не плодить дубли
                    existing_vetted = set()
                    if os.path.exists(VETTED_FILE):
                        with open(VETTED_FILE, 'r', encoding='utf-8') as vf:
                            existing_vetted = {l.split('#')[0].strip() for l in vf if 'vless' in l}
                    
                    if base not in existing_vetted:
                        with open(VETTED_FILE, 'a', encoding='utf-8') as f:
                            f.write(f"{full_link} # Rank: ELITE | {time.strftime('%Y-%m-%d')}\n")
                        print(f"🏆 НОВАЯ ЭЛИТА: {base[:15]} [{success_hits}/{total_hits}]")
                    else:
                        print(f"♻️ СЕРВЕР УЖЕ В ЭЛИТЕ: {base[:15]}")

                if base in ranking_db:
                    del ranking_db[base]
            else:
                if status == "OK":
                    decrease_rank(ranking_db, base, delta=30, note=f"FAIL {success_hits}/{total_hits}")
                elif status in {"IPv6_BAN", "ERROR", "WL", "WL_UNAVAILABLE"}:
                    if base in ranking_db:
                        del ranking_db[base]
                    if status == "IPv6_BAN":
                        add_to_blacklist(base)
                    remove_from_all(base)
                

        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)
    else:
        print("⌛ Нет новых кандидатов для пыток.")

    print("✅ Инспекция завершена.")
    
if __name__ == "__main__":
    init_checker_lib()
    main_torturer()
