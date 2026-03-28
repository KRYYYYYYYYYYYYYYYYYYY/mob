import socket
import re
import os
import ssl
import json
import uuid
import math
import urllib.parse
import urllib.request
import time
import subprocess
import ipaddress
import ctypes
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock



# Настройки путей
INPUT_FILE = 'test1/1.txt'
OUTPUT_FILE = 'kr/mob/wifi.txt'
STATUS_FILE = 'test1/status.json'
CACHE_FILE = 'test1/countries_cache.json' # Добавь эту константу для порядка
RANKING_FILE = 'test1/ranking.json'
VETTED_FILE = 'test1/vetted.txt'
PINNED_FILE = 'test1/pinned.txt'
FAVORITES_FILE = 'test1/favorites.txt'
BLACKLIST_FILE = 'test1/blacklist.txt'
REASONS_FILE = 'test1/reasons.json'
CHECK_LOG_FILE = 'test1/check_log.txt'
RUN_RESULT_FILE = 'test1/run_result.json'

EXTERNAL_SOURCE_URL = [
    "https://raw.githubusercontent.com/KRYYYYYYYYYYYYYYYYYYY/crazy_xray_checker/refs/heads/main/result/working.txt"
]

GRACE_PERIOD = 2 * 24 * 60 * 60 # 48 часов

HEADER = """# profile-title: 🏳️Мобильный инет🏳️
# remark: 🏳️Мобильный инет🏳️
# announce: Подписка для использования ТОЛЬКО на мобильном интернете в условиях БЕЛЫХ СПИСКОВ! P.s. Бесплатная подписка не гарантирует рабочих серверов, в общем, а уж 24/7 - тем более. Сугубо ваше право ее юзать.
# profile-update-interval: 2
"""

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

DEFAULT_MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 16; SM-A336B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.119 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    # Ближе к реальному трафику с Samsung A33 + Android-клиентов
    "Happ/3.15.1 (com.happproxy; Android 16; Samsung SM-A336B)",
    "okhttp/4.12.0 v2rayNG/1.12.28",
]
DEFAULT_PROBE_PATHS = ["/", "/generate_204", "/favicon.ico"]

# Подключаем новую библиотеку
go_lib = None
HOST_LOCKS = {}
HOST_LOCKS_GUARD = Lock()

def get_host_lock(host: str) -> Lock:
    with HOST_LOCKS_GUARD:
        lk = HOST_LOCKS.get(host)
        if lk is None:
            lk = Lock()
            HOST_LOCKS[host] = lk
        return lk

def l7_multi_probe_host_serialized(link: str, host: str, stress_config: dict):
    """
    Сериализует проверки в рамках одного host/IP.
    Это снижает ложные reject при параллельной долбежке одного сервера
    разными UUID в одном батче.
    """
    lock = get_host_lock(host)
    with lock:
        return l7_multi_probe(link, stress_config)


def init_checker_lib() -> None:
    """Инициализирует Go-библиотеку проверки, если она доступна."""
    global go_lib
    lib_path = os.path.abspath("libchecker.so")
    if not os.path.exists(lib_path):
        print("❌ ОШИБКА: Библиотека libchecker.so не найдена!")
        return

    go_lib = ctypes.cdll.LoadLibrary(lib_path)

    go_lib.CheckVlessL7.argtypes = [
        ctypes.c_char_p,  # addr (host)
        ctypes.c_int,     # port
        ctypes.c_char_p,  # uuid
        ctypes.c_char_p,  # sni
        ctypes.c_char_p,  # pbk
        ctypes.c_char_p,  # sid
        ctypes.c_char_p,  # flow
        ctypes.c_int      # timeout
    ]
    go_lib.CheckVlessL7.restype = ctypes.c_int

    # CheckAnyL7 — универсальный чекер из crazy_xray_checker:
    # vmess, vless (reality/tls), trojan, shadowsocks.
    # Включает перебор SNI-кандидатов внутри Go.
    go_lib.CheckAnyL7.argtypes = [
        ctypes.c_char_p,  # scheme   ("vless"/"vmess"/"trojan"/"shadowsocks")
        ctypes.c_char_p,  # addr
        ctypes.c_int,     # port
        ctypes.c_char_p,  # id       (uuid для vless/vmess, пароль для trojan)
        ctypes.c_char_p,  # security ("reality"/"tls"/"none")
        ctypes.c_char_p,  # sni
        ctypes.c_char_p,  # pbk      (только reality)
        ctypes.c_char_p,  # sid      (только reality)
        ctypes.c_char_p,  # fp       (fingerprint для reality/tls)
        ctypes.c_char_p,  # flow     (только vless+reality)
        ctypes.c_char_p,  # netType  ("tcp"/"ws")
        ctypes.c_char_p,  # path     (для ws)
        ctypes.c_char_p,  # hostHdr  (Host-заголовок для ws)
        ctypes.c_char_p,  # method   (только shadowsocks)
        ctypes.c_char_p,  # password (только shadowsocks)
        ctypes.c_int,     # timeout
    ]
    go_lib.CheckAnyL7.restype = ctypes.c_int

def probe_vless_l7(link, target_sni, timeout=5):
    """Парсит VLESS ссылку и возвращает пинг в мс (0 если ошибка)."""
    if go_lib is None:
        return 0
    try:
        pc = parse_vless_link(link)
        if not pc or not pc["addr"] or pc["port"] <= 0:
            return 0

        # Для vless-ветки передаем выбранный SNI-кандидат явно,
        # но используем универсальный CheckAnyL7 (с поддержкой fp).
        latency = go_lib.CheckAnyL7(
            b"vless",
            pc["addr"].encode("utf-8"),
            int(pc["port"]),
            pc["id"].encode("utf-8"),
            pc["security"].encode("utf-8"),
            (target_sni or pc.get("sni", "")).encode("utf-8"),
            pc.get("pbk", "").encode("utf-8"),
            pc.get("sid", "").encode("utf-8"),
            pc.get("fp", "").encode("utf-8"),
            pc.get("flow", "").encode("utf-8"),
            pc.get("net_type", "tcp").encode("utf-8"),
            pc.get("path", "").encode("utf-8"),
            pc.get("host_hdr", "").encode("utf-8"),
            b"",
            b"",
            int(timeout)
        )
        return int(latency) # Вернет 0 или время в мс
    except Exception as e:
        print(f"⚠️ Ошибка L7 чекера: {e}")
        return 0

def extract_sni(link):
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    return params.get("sni", [""])[0]

# ---------------------------------------------------------------------------
# ПАРСЕРЫ ПРОТОКОЛОВ (из crazy_xray_checker — поддержка vmess/trojan/ss)
# ---------------------------------------------------------------------------

SUPPORTED_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")

def get_link_scheme(link: str) -> str:
    """Возвращает scheme строчными буквами: 'vless', 'vmess', 'trojan', 'shadowsocks' или ''."""
    lw = link.lower()
    if lw.startswith("vless://"):      return "vless"
    if lw.startswith("vmess://"):      return "vmess"
    if lw.startswith("trojan://"):     return "trojan"
    if lw.startswith("ss://"):         return "shadowsocks"
    return ""

def parse_vmess_link(link: str) -> dict | None:
    """
    Декодирует vmess://base64(json) и возвращает унифицированный словарь.
    Вдохновлено parse.go из crazy_xray_checker.
    """
    import base64
    try:
        b64 = link[len("vmess://"):]
        # Пробуем разные варианты декодирования
        for b64v in [b64, b64 + "=" * (-len(b64) % 4)]:
            try:
                raw = base64.b64decode(b64v)
                break
            except Exception:
                continue
        else:
            return None
        m = json.loads(raw.decode("utf-8"))
        host = str(m.get("add", ""))
        port_raw = m.get("port", "443")
        port = int(port_raw) if str(port_raw).isdigit() else 443
        net_type = str(m.get("net", "tcp")).lower()
        if net_type == "httpupgrade":
            net_type = "ws"
        tls_val = str(m.get("tls", "")).lower()
        security = "tls" if tls_val == "tls" else "none"
        sni = str(m.get("sni", m.get("host", "")))
        return {
            "scheme":   "vmess",
            "addr":     host,
            "port":     port,
            "id":       str(m.get("id", "")),
            "security": security,
            "sni":      sni,
            "pbk":      "",
            "sid":      "",
            "fp":       "",
            "flow":     "",
            "net_type": net_type,
            "path":     str(m.get("path", "")),
            "host_hdr": str(m.get("host", "")),
            "method":   "",
            "password": "",
        }
    except Exception:
        return None

def parse_trojan_link(link: str) -> dict | None:
    """Парсит trojan://password@host:port?sni=...&security=tls"""
    try:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        host = parsed.hostname or ""
        port = parsed.port or 443
        password = parsed.username or ""
        sni = params.get("sni", [host])[0]
        security = params.get("security", ["tls"])[0].lower()
        net_type = params.get("type", ["tcp"])[0].lower()
        if net_type == "httpupgrade":
            net_type = "ws"
        return {
            "scheme":   "trojan",
            "addr":     host,
            "port":     int(port),
            "id":       password,
            "security": security,
            "sni":      sni,
            "pbk":      "",
            "sid":      "",
            "fp":       "",
            "flow":     "",
            "net_type": net_type,
            "path":     params.get("path", [""])[0],
            "host_hdr": params.get("host", [""])[0],
            "method":   "",
            "password": "",
        }
    except Exception:
        return None

def parse_ss_link(link: str) -> dict | None:
    """
    Парсит ss://base64(method:password)@host:port
    Вдохновлено parseSS из crazy_xray_checker.
    """
    import base64
    try:
        parsed = urllib.parse.urlparse(link)
        cred_b64 = parsed.username or ""
        try:
            dec = base64.b64decode(cred_b64 + "=" * (-len(cred_b64) % 4)).decode("utf-8")
        except Exception:
            dec = ""
        if ":" in dec:
            method, password = dec.split(":", 1)
        else:
            method, password = "aes-256-gcm", cred_b64
        host = parsed.hostname or ""
        port = parsed.port or 443
        return {
            "scheme":   "shadowsocks",
            "addr":     host,
            "port":     int(port),
            "id":       "",
            "security": "none",
            "sni":      "",
            "pbk":      "",
            "sid":      "",
            "fp":       "",
            "flow":     "",
            "net_type": "tcp",
            "path":     "",
            "host_hdr": "",
            "method":   method,
            "password": password,
        }
    except Exception:
        return None

def parse_vless_link(link: str) -> dict | None:
    """Парсит vless://uuid@host:port?security=...&sni=...&pbk=...&sid=..."""
    try:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        _, host, port = extract_host_port(link)
        if not host or not port:
            return None
        security = params.get("security", ["none"])[0].lower()
        net_type = params.get("type", ["tcp"])[0].lower()
        if net_type == "httpupgrade":
            net_type = "ws"
        return {
            "scheme":   "vless",
            "addr":     host,
            "port":     int(port),
            "id":       parsed.username or "",
            "security": security,
            "sni":      params.get("sni", [""])[0],
            "pbk":      params.get("pbk", [""])[0],
            "sid":      params.get("sid", [""])[0],
            "fp":       params.get("fp", [""])[0],
            "flow":     params.get("flow", [""])[0],
            "net_type": net_type,
            "path":     params.get("path", [""])[0],
            "host_hdr": params.get("host", [""])[0],
            "method":   "",
            "password": "",
        }
    except Exception:
        return None

def parse_any_link(link: str) -> dict | None:
    """Универсальный парсер ссылок всех поддерживаемых протоколов."""
    scheme = get_link_scheme(link)
    if scheme == "vless":        return parse_vless_link(link)
    if scheme == "vmess":        return parse_vmess_link(link)
    if scheme == "trojan":       return parse_trojan_link(link)
    if scheme == "shadowsocks":  return parse_ss_link(link)
    return None

def probe_any_l7(link: str, timeout: int = 5) -> int:
    """
    Универсальный L7-пробник для любого протокола через CheckAnyL7.
    Возвращает задержку в мс (>0) при успехе, -1 при L7-отказе, 0 при ошибке.
    Перебор SNI-кандидатов выполняется внутри Go (как в crazy_xray_checker).
    """
    if go_lib is None:
        return 0
    try:
        pc = parse_any_link(link)
        if not pc or not pc["addr"] or pc["port"] <= 0:
            return 0
        latency = go_lib.CheckAnyL7(
            pc["scheme"].encode(),
            pc["addr"].encode(),
            int(pc["port"]),
            pc["id"].encode(),
            pc["security"].encode(),
            pc["sni"].encode(),
            pc["pbk"].encode(),
            pc["sid"].encode(),
            pc.get("fp", "").encode(),
            pc["flow"].encode(),
            pc["net_type"].encode(),
            pc["path"].encode(),
            pc["host_hdr"].encode(),
            pc["method"].encode(),
            pc["password"].encode(),
            int(timeout),
        )
        return int(latency)
    except Exception as e:
        print(f"⚠️ Ошибка probe_any_l7: {e}")
        return 0

# --- НОВЫЙ БЛОК: ФИЛЬТРАЦИЯ И КАНДИДАТЫ ---
BAD_SNI_KEYWORDS = ['google', 'apple', 'microsoft', 'facebook', 'netflix', 'youtube']

def is_sni_suspicious(link):
    """Проверяет, нет ли в SNI мусорных доменов (для мобильных сетей это важно)."""
    sni = extract_sni(link).lower()
    if not sni: 
        return False
    for word in BAD_SNI_KEYWORDS:
        if word in sni:
            return True
    return False

def extract_sni_candidates(link):
    """Вытягивает список потенциальных SNI из разных частей ссылки."""
    candidates = []
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    
    # 1. Текущий SNI
    sni = params.get('sni', [''])[0]
    if sni: candidates.append(sni)
    
    # 2. Host Header
    host_hdr = params.get('host', [''])[0]
    if host_hdr and host_hdr not in candidates: candidates.append(host_hdr)
    
    # 3. Домен из адреса (если это не IP)
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname and parsed.hostname not in candidates:
            candidates.append(parsed.hostname)
            
    return candidates
# ------------------------------------------

def is_valid_vless_id(value: str) -> bool:
    """
    VLESS user-id:
    - обычный UUID
    - или непустая кастомная строка до 30 байт (legacy-режим)
    """
    if not value:
        return False
    try:
        uuid.UUID(value)
        return True
    except Exception:
        pass
    try:
        raw = value.encode("utf-8")
    except Exception:
        return False
    if not (0 < len(raw) <= 30):
        return False
    # Отсекаем совсем мусорные ID: пробелы по краям и управляющие символы.
    if value != value.strip():
        return False
    return all(ch.isprintable() and ch not in "\r\n\t" for ch in value)

def is_uuid_like(value: str) -> bool:
    """Совместимость со старым именем."""
    return is_valid_vless_id(value)

def validate_protocol_auth(link: str, link_scheme: str):
    """
    Лёгкая валидация учетки до L7-чека, чтобы не тратить пробы на заведомый мусор.
    Возвращает: (ok, reason)
    """
    pc = parse_any_link(link)
    if not pc:
        return False, "skip_bad_parsed_link"

    if link_scheme == "vless":
        # Для VLESS+REALITY не дублируем "жесткую" UUID-валидацию в Python:
        # финальная проверка достоверности должна идти через Go/xray L7.
        vless_id = str(pc.get("id", ""))
        if not vless_id.strip():
            return False, "skip_missing_vless_id"
        if len(vless_id.encode("utf-8", errors="ignore")) > 96:
            return False, "skip_bad_uuid_vless"
    elif link_scheme == "vmess":
        if not is_uuid_like(pc.get("id", "")):
            return False, "skip_bad_uuid_vmess"
    elif link_scheme == "trojan":
        pwd = pc.get("id", "")
        if not isinstance(pwd, str) or len(pwd.strip()) < 6:
            return False, "skip_bad_trojan_password"
    elif link_scheme == "shadowsocks":
        method = str(pc.get("method", "")).strip()
        password = str(pc.get("password", "")).strip()
        if not method or not password or len(password) < 4:
            return False, "skip_bad_ss_auth"
    return True, ""

def validate_transport_requirements(link: str):
    """
    Валидация transport/TLS/REALITY параметров перед запуском Go-чекера.
    Возвращает: (ok, reason)
    """
    pc = parse_any_link(link)
    if not pc:
        return False, "skip_bad_transport_parsed_link"

    security = str(pc.get("security", "none")).lower()
    net_type = str(pc.get("net_type", "tcp")).lower()
    sni = str(pc.get("sni", "")).strip()
    pbk = str(pc.get("pbk", "")).strip()

    if security in {"tls", "reality"} and not sni:
        return False, "skip_missing_sni_for_tls"
    if security == "reality" and not pbk:
        return False, "skip_missing_pbk_for_reality"
    if net_type == "ws" and not str(pc.get("path", "")).strip():
        return False, "skip_missing_ws_path"
    return True, ""

def build_recheck_stress_config(stress_config: dict) -> dict:
    """
    Более мягкий профиль повторной L7-проверки для спорных отказов.
    Нужен, чтобы уменьшить ложные fail_l7_reject из-за кратковременных сетевых сбоев.
    """
    cfg = dict(stress_config)
    base_timeout = float(stress_config.get("timeout", 5.0))
    cfg["timeout"] = max(base_timeout * 1.8, 6.0)
    cfg["probe_attempts"] = max(int(stress_config.get("probe_attempts", 4)), 3)
    cfg["l7_min_success"] = 1
    cfg["min_success"] = 1
    cfg["between_attempts_sleep"] = max(float(stress_config.get("between_attempts_sleep", 0.35)), 0.45)
    return cfg


def download_raw_data(urls):
    """
    Этап 1: Огороженная загрузка с защитой от сбоев DNS.
    """
    all_links = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    print("📥 ЭТАП 1: Загрузка сырых данных (Огороженный режим)")
    
    for url in urls:
        success = False
        # Извлекаем домен (например, raw.githubusercontent.com)
        try:
            hostname = urllib.parse.urlparse(url).netloc
        except:
            hostname = None
            
        for attempt in range(5): 
            try:
                # 1. Прогреваем DNS (пробиваем Errno -3)
                if hostname:
                    try:
                        socket.gethostbyname(hostname)
                    except:
                        pass # Если не вышло тут, попробует urllib

                print(f"📡 Попытка {attempt+1}: {url.split('/')[-1]}...", end=" ")
                req = urllib.request.Request(url.strip(), headers=headers)
                
                # 2. Загружаем данные
                with urllib.request.urlopen(req, timeout=30) as response:
                    content = response.read().decode("utf-8")
                    found = [
                        line.strip() for line in content.splitlines()
                        if any(p in line.lower() for p in ("vless://", "vmess://", "trojan://", "ss://"))
                    ]
                    all_links.extend(found)
                    print(f"✅ Найдено {len(found)} шт.")
                    success = True
                    break
            except Exception as e:
                # 3. Нарастающая пауза: 5с, 10с, 15с, 20с
                wait_time = (attempt + 1) * 5
                print(f"❌ Ошибка: {e}. Ждем {wait_time}с...")
                time.sleep(wait_time)
        
        if not success:
            print(f"⚠️ КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить {url}")
            
    return all_links

def rebuild_link_name(link: str, new_name: str) -> str:
    base, _, fragment = link.partition("#")

    # Если это уже закреп — не трогаем
    if fragment:
        frag = urllib.parse.unquote(fragment).upper()
        if "PINNED" in frag:
            return link

    if not fragment:
        return f"{base}#{urllib.parse.quote(new_name)}"

    fragment_dec = urllib.parse.unquote(fragment)

    # Пытаемся сохранить флаг/эмодзи
    match = re.match(r"^([^\w\s\d]|[^\x00-\x7F])+", fragment_dec)
    if match:
        prefix = match.group(0).strip()
        return f"{base}#{urllib.parse.quote(prefix + ' ' + new_name)}"

    return f"{base}#{urllib.parse.quote(new_name)}"

def remove_from_all(base_part: str):
    """Удаляет сервер по base_part из основных рабочих файлов."""
    for path in [INPUT_FILE, OUTPUT_FILE]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            new_lines = [line for line in lines if line.split('#')[0].strip() != base_part]
            if len(new_lines) != len(lines):
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
        except Exception as e:
            print(f"⚠️ Ошибка при очистке {path}: {e}")

    
def remove_from_input_file(base_to_remove: str):
    """Удаляет конкретную ссылку из 1.txt по её базовой части"""
    if not os.path.exists(INPUT_FILE):
        return
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Оставляем только те строки, у которых не совпадает базовая часть
        new_lines = [l for l in lines if l.split('#')[0].strip() != base_to_remove]
        
        if len(lines) != len(new_lines):
            with open(INPUT_FILE, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
    except Exception as e:
        print(f"⚠️ Ошибка при очистке {INPUT_FILE}: {e}")

def is_ipv6(host: str) -> bool:
    """
    Проверяет, является ли строка IPv6.
    Работает и со скобками (для URL), и без них (после парсинга).
    """
    if not host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return ":" in host
    except ValueError:
        return False

def extract_host_port(link: str):
    """
    Извлекает хост и порт. 
    Если это IPv6 в скобках, вернет чистый адрес без скобок.
    """
    pattern = r"@(?:\[([0-9a-fA-F:]+)\]|([\w.-]+)):(\d+)"
    match = re.search(pattern, link)
    if match:
        # group(1) — адрес из скобок, group(2) — обычный адрес
        host = match.group(1) or match.group(2)
        port = match.group(3)
        return match.group(0), host, port
    return None, None, None

def format_uri_host(host: str) -> str:
    """Упаковывает IPv6 в скобки для использования в ссылке vless."""
    if is_ipv6(host) and not host.startswith("["):
        return f"[{host}]"
    return host

def get_country_code(host, cache):
    # 1. Сразу определяем IP
    ip = host
    if not is_ipv6(host):
        try:
            if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
                ip = socket.gethostbyname(host)
        except:
            ip = host

    # 2. МГНОВЕННЫЙ ОТВЕТ: Если IP уже в кэше, выдаем результат БЕЗ пауз
    if ip in cache:
        return cache[ip]

    # 3. ЗАПРОС К API: Только если IP новый
    try:
        # Паузу делаем ТОЛЬКО перед реальным сетевым запросом
        time.sleep(0.5) 
        
        clean_ip = ip.replace("[", "").replace("]", "")
        url = f"http://ip-api.com/json/{clean_ip}?fields=status,countryCode"
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "success":
                code = data.get("countryCode", "Unknown")
                # Сразу сохраняем в кэш
                cache[ip] = code 
                return code
    except:
        pass
        
    return "Unknown"

def safe_gh_call(cmd, token):
    """Безопасно вызывает gh cli, пробуя 3 раза при сетевых сбоях."""
    import subprocess
    import time
    import os
    for attempt in range(3):
        try:
            # Выполняем команду
            return subprocess.check_output(
                cmd, 
                env={**os.environ, "GH_TOKEN": token}, 
                stderr=subprocess.STDOUT
            ).decode()
        except subprocess.CalledProcessError as e:
            err_output = e.output.decode().lower() if e.output else ""
            # Если это сетевая ошибка GitHub, ждем и повторяем
            if any(x in err_output for x in ["connection", "api.github.com", "timeout"]):
                print(f"⏳ Сетевой лаг GitHub (попытка {attempt+1}/3)... Ждем 5 сек.")
                time.sleep(5)
                continue
            # Если ошибка другая (например, нет прав), выходим
            print(f"❌ Ошибка GH CLI: {err_output[:100]}")
            break
    return "[]"

def add_to_blacklist(base_part):
    """Добавляет сервер в файл blacklist.txt, если его там нет."""
    current_bl = set()
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r') as f:
            current_bl = {line.strip() for line in f if line.strip()}
    
    if base_part not in current_bl:
        with open(BLACKLIST_FILE, 'a') as f:
            f.write(base_part + "\n")


def note_reason(reason_stats: dict, reason: str, base_part: str = "", extra: str = ""):
    """Пишет причину отсева/успеха в счетчик и в потоковый лог как в crazy_xray_checker."""
    reason_stats[reason] = int(reason_stats.get(reason, 0)) + 1
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} | {reason}"
    if base_part:
        line += f" | {base_part}"
    if extra:
        line += f" | {extra}"
    with open(CHECK_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def normalize_rank_entry(base_part: str, entry):
    """Приводит запись ranking.json к единому виду."""
    if isinstance(entry, dict):
        rank = int(entry.get("rank", 0) or 0)
        link = entry.get("link", base_part)
        return {"rank": max(0, rank), "link": link}
    if isinstance(entry, (int, float)):
        return {"rank": max(0, int(entry)), "link": base_part}
    return {"rank": 0, "link": base_part}

def ranking_sort_key(link: str, ranking_db: dict):
    """Сортировка: высокий rank → сначала, далее короткий base для стабильности."""
    base = link.split('#')[0].strip()
    rank = 0
    if base in ranking_db:
        rank = normalize_rank_entry(base, ranking_db[base]).get("rank", 0)
    return (-rank, base)

def l7_multi_probe(link: str, stress_config: dict):
    """
    Многократный L7-пробник. Поддерживает все протоколы.
    - vless: перебор SNI-кандидатов в Python + CheckVlessL7
    - vmess/trojan/shadowsocks: CheckAnyL7 (SNI-перебор внутри Go)
    """
    min_hits = max(1, int(stress_config.get("l7_min_success", 2)))
    max_candidates = max(1, int(stress_config.get("l7_max_candidates", 3)))
    probe_attempts = max(1, int(stress_config.get("probe_attempts", 4)))
    between_attempts_sleep = max(0.0, float(stress_config.get("between_attempts_sleep", 0.35)))
    timeout_sec = max(1, int(math.ceil(float(stress_config.get("timeout", 5)))))
    max_latency_ms = max(1, int(stress_config.get("max_latency_ms", 6000)))
    stability_max_spread_ms = max(1, int(stress_config.get("stability_max_spread_ms", 1200)))
    stability_max_ratio = max(1.0, float(stress_config.get("stability_max_ratio", 4.0)))
    stability_max_na = max(0, int(stress_config.get("stability_max_na", 0)))
    stability_max_jitter_ms = max(0, int(stress_config.get("stability_max_jitter_ms", 800)))
    stability_min_success_rate = min(1.0, max(0.0, float(stress_config.get("stability_min_success_rate", 0.5))))
    stability_max_loss_rate = min(1.0, max(0.0, float(stress_config.get("stability_max_loss_rate", 0.5))))
    stability_min_samples = max(1, int(stress_config.get("stability_min_samples", 3)))
    stability_p95_max_ms = max(1, int(stress_config.get("stability_p95_max_ms", max_latency_ms)))

    def is_unstable(latencies: list[int], na_count: int) -> bool:
        if len(latencies) < 2:
            return False
        min_latency = min(latencies)
        max_latency = max(latencies)
        spread = max_latency - min_latency
        ratio = (max_latency / min_latency) if min_latency > 0 else float("inf")
        if spread >= stability_max_spread_ms:
            return True
        if ratio >= stability_max_ratio:
            return True
        if na_count > stability_max_na:
            return True
        return False

    def is_unstable_extended(latencies: list[int], hits: int, total_attempts: int, na_count: int) -> bool:
        # Базовые проверки оставляем как есть
        if is_unstable(latencies, na_count):
            return True
        if total_attempts <= 0 or total_attempts < stability_min_samples:
            return False

        success_rate = hits / total_attempts
        loss_rate = na_count / total_attempts
        if success_rate < stability_min_success_rate:
            return True
        if loss_rate > stability_max_loss_rate:
            return True

        if latencies:
            sorted_lats = sorted(latencies)
            p95_idx = max(0, min(len(sorted_lats) - 1, math.ceil(len(sorted_lats) * 0.95) - 1))
            p95 = sorted_lats[p95_idx]
            if p95 > stability_p95_max_ms:
                return True
            if len(latencies) >= 2:
                jitter = max(abs(latencies[i] - latencies[i - 1]) for i in range(1, len(latencies)))
                if jitter > stability_max_jitter_ms:
                    return True
        return False

    scheme = get_link_scheme(link)

    # --- Унифицированный путь через CheckAnyL7 ---
    # Для vless тоже используем только Go-логику, чтобы не конфликтовали
    # Python-кандидаты SNI и Go-кандидаты (двойной перебор давал флаппинг).
    if scheme in ("vless", "vmess", "trojan", "shadowsocks"):
        hits = 0
        best_latency = 0
        latencies = []
        na_count = 0
        total_attempts = 0
        for attempt_idx in range(probe_attempts):
            total_attempts += 1
            latency = probe_any_l7(link, timeout=timeout_sec)
            if latency < 0:
                return False, -1
            if 0 < latency <= max_latency_ms:
                hits += 1
                latencies.append(latency)
                if best_latency == 0 or latency < best_latency:
                    best_latency = latency
                if is_unstable_extended(latencies, hits, total_attempts, na_count):
                    return False, -2
                if hits >= min_hits and attempt_idx == probe_attempts - 1:
                    if is_unstable_extended(latencies, hits, total_attempts, na_count):
                        return False, -2
                    return True, best_latency
            else:
                na_count += 1
                if hits > 0 and na_count > stability_max_na:
                    return False, -2

            remaining_attempts = probe_attempts - attempt_idx - 1
            if hits + remaining_attempts < min_hits:
                return False, 0

            # Даем шанс собрать min_hits, но на последней попытке требуем стабильность.
            if hits >= min_hits and remaining_attempts == 0:
                if is_unstable_extended(latencies, hits, total_attempts, na_count):
                    return False, -2
                return True, best_latency
            if between_attempts_sleep > 0:
                time.sleep(between_attempts_sleep)
        return False, 0

    # --- legacy vless path (оставлен как fallback, фактически не используется) ---
    candidates = extract_sni_candidates(link)
    if not candidates:
        native_sni = extract_sni(link)
        if native_sni:
            candidates = [native_sni]
    candidates = [c for c in candidates if c and not any(w in c.lower() for w in BAD_SNI_KEYWORDS)]
    candidates = candidates[:max_candidates]

    hits = 0
    best_latency = 0
    latencies = []
    na_count = 0
    total_attempts = 0
    for candidate_sni in candidates:
        for attempt_idx in range(probe_attempts):
            total_attempts += 1
            latency = probe_vless_l7(link, candidate_sni, timeout=timeout_sec)
            if latency < 0:
                # Жесткий отказ L7 при доступном TCP (например, отключенный UUID)
                return False, -1
            if latency > 0 and latency <= max_latency_ms:
                hits += 1
                latencies.append(latency)
                if best_latency == 0 or latency < best_latency:
                    best_latency = latency
                if is_unstable_extended(latencies, hits, total_attempts, na_count):
                    return False, -2
            else:
                na_count += 1
                if hits > 0 and na_count > stability_max_na:
                    return False, -2

            remaining_attempts = probe_attempts - attempt_idx - 1
            if hits + remaining_attempts < min_hits:
                break

            if hits >= min_hits and remaining_attempts == 0:
                if is_unstable_extended(latencies, hits, total_attempts, na_count):
                    return False, -2
                return True, best_latency
            if between_attempts_sleep > 0:
                time.sleep(between_attempts_sleep)
        if hits >= min_hits and not is_unstable_extended(latencies, hits, total_attempts, na_count):
            return True, best_latency
    return False, 0

def main():
    import subprocess
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    reason_stats = {}
    # сбрасываем лог текущего прогона
    with open(CHECK_LOG_FILE, "w", encoding="utf-8") as f:
        f.write("")

    # --- ЗАГРУЗКА КЭША СТРАН ---
    countries_cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f: countries_cache = json.load(f)
        except: countries_cache = {}

    # --- ЗАГРУЗКА СПИСКОВ И НАСТРОЕК ---
    blacklist = set()
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r') as f:
            blacklist = {line.strip() for line in f if line.strip()}

    ranking_db = {}
    if os.path.exists(RANKING_FILE):
        try:
            with open(RANKING_FILE, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    ranking_db = {
                        base: normalize_rank_entry(base, entry)
                        for base, entry in loaded.items()
                    }
        except: ranking_db = {}

    vetted_list = []
    if os.path.exists('test1/vetted.txt'):
        with open('test1/vetted.txt', 'r') as f:
            vetted_list = [line.strip() for line in f if line.strip()]

    # 1. Загружаем Закрепленные (Pinned)
    pinned_list = []
    if os.path.exists('test1/pinned.txt'):
        with open('test1/pinned.txt', 'r', encoding='utf-8') as f:
            pinned_list = [
                line.strip() for line in f
                if any(p in line.lower() for p in ("vless://", "vmess://", "trojan://", "ss://"))
            ]

    print(f"📦 Загружено закрепов: {len(pinned_list)}")

    # 2. Загружаем Фавориты (Favorites)
    fav_bases = set()
    fav_full_links = []
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if any(p in line.lower() for p in ("vless://", "vmess://", "trojan://", "ss://")):
                    link = line.strip()
                    fav_full_links.append(link)
                    fav_bases.add(link.split("#")[0].strip())
    print(f"⭐ Загружено фаворитов: {len(fav_bases)}")

    # 3. Загружаем Отложенные (Deferred)
    deferred_base = []
    if os.path.exists('test1/deferred.txt'):
        with open('test1/deferred.txt', 'r', encoding='utf-8') as f:
            deferred_base = [line.strip() for line in f if line.strip()]

    # 4. Загружаем текущую базу 1.txt
    current_base = []
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            current_base = f.read().splitlines()

    # 5. Загружаем историю статусов
    history = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f: history = json.load(f)
        except: history = {}

    # --- [ШАГ 1: ФОРМИРУЕМ СПИСОК БЕССМЕРТНЫХ] ---
    immortals = [] 
    seen_immortals = set()

    # Сначала закрепы (самый высокий приоритет)
    for p in pinned_list:
        base = p.split("#")[0].strip()
        if base not in seen_immortals:
            immortals.append(p)
            seen_immortals.add(base)

    # Потом фавориты
    for f_link in fav_full_links:
        base = f_link.split("#")[0].strip()
        if base not in seen_immortals:
            immortals.append(f_link)
            seen_immortals.add(base)

    print(f"🛡️ Итого бессмертных в начале списка: {len(immortals)}")
    note_reason(reason_stats, "immortals_loaded", extra=str(len(immortals)))

    # --- [ШАГ 2: ГОТОВИМ ОЧЕРЕДЬ НА ПРОВЕРКУ] ---
    check_queue = []
    seen_in_queue = set()
    def extend_check_queue(links):
        for link in links:
            base = link.split('#')[0].strip()
            # Проверяем, что ссылки нет в бессмертных, она не дубликат в очереди и не в блэклисте
            if base not in seen_immortals and base not in seen_in_queue and base not in blacklist:
                check_queue.append(link)
                seen_in_queue.add(base)

    # Порядок строго по требованиям:
    # 1) сначала текущая база (уже используемые в подписке/прошлых прогонах),
    # 2) потом отложенные,
    # 3) новые подтягиваем только если первых двух не хватило.
    extend_check_queue(sorted(current_base, key=lambda l: ranking_sort_key(l, ranking_db)))
    extend_check_queue(sorted(deferred_base, key=lambda l: ranking_sort_key(l, ranking_db)))

    # --- [ШАГ 3: ПОДГОТОВКА К ЦИКЛУ] ---
    working_for_sub = immortals[:200] # Сразу забиваем подписку бессмертными
    working_for_base = []            # Сюда пойдут те, кто реально ответил
    
    now = time.time()
    start_time = now
    counter = len(working_for_sub) + 1
    idx = 0
    checked_today = 0
    MAX_TO_CHECK = 500 
    ip_counts = {}
    seen_ips = set()
    seen_parts = set()
    runtime_blocked_hosts = {}
    host_precheck_counts = {}
    host_l7_reject_counts = {}

    # Настройки стресс-теста (твой блок 1-в-1)
    stress_config = {
        "timeout": 5.0, "dpi_sleep": 0.5, "target_mtu": 1280,
        "probe_attempts": 4, "min_success": 2, "recv_timeout": 1.7,
        "between_attempts_sleep": 0.35,
        "l7_min_success": 2,
        "l7_max_candidates": 3,
        "workers": 32,
        "max_latency_ms": 6000,
        "max_check_duration_sec": 5 * 60 * 60,
        "stability_max_spread_ms": 1200,
        "stability_max_ratio": 4.0,
        "stability_max_na": 0,
        "stability_max_jitter_ms": 800,
        "stability_min_success_rate": 0.5,
        "stability_max_loss_rate": 0.5,
        "stability_min_samples": 3,
        "stability_p95_max_ms": 6000,
        "user_agents": list(DEFAULT_MOBILE_USER_AGENTS),
        "probe_paths": list(DEFAULT_PROBE_PATHS),
    }
    if os.path.exists('test1/stress_profile.json'):
        try:
            with open('test1/stress_profile.json', 'r') as f:
                data = json.load(f)
                stress_config["timeout"] = max(4.5, data.get("max_handshake_ms", 2500) / 1000)
                stress_config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
                stress_config["target_mtu"] = data.get("target_mtu", 1280)
                stress_config["probe_attempts"] = int(data.get("probe_attempts", stress_config["probe_attempts"]))
                stress_config["min_success"] = int(data.get("min_success", stress_config["min_success"]))
                stress_config["l7_min_success"] = int(data.get("l7_min_success", stress_config["l7_min_success"]))
                stress_config["l7_max_candidates"] = int(data.get("l7_max_candidates", stress_config["l7_max_candidates"]))
                stress_config["workers"] = int(data.get("workers", stress_config["workers"]))
                stress_config["max_latency_ms"] = int(data.get("max_latency_ms", stress_config["max_latency_ms"]))
                stress_config["max_check_duration_sec"] = int(data.get("max_check_duration_sec", stress_config["max_check_duration_sec"]))
                stress_config["stability_max_spread_ms"] = int(data.get("stability_max_spread_ms", stress_config["stability_max_spread_ms"]))
                stress_config["stability_max_ratio"] = float(data.get("stability_max_ratio", stress_config["stability_max_ratio"]))
                stress_config["stability_max_na"] = int(data.get("stability_max_na", stress_config["stability_max_na"]))
                stress_config["stability_max_jitter_ms"] = int(data.get("stability_max_jitter_ms", stress_config["stability_max_jitter_ms"]))
                stress_config["stability_min_success_rate"] = float(data.get("stability_min_success_rate", stress_config["stability_min_success_rate"]))
                stress_config["stability_max_loss_rate"] = float(data.get("stability_max_loss_rate", stress_config["stability_max_loss_rate"]))
                stress_config["stability_min_samples"] = int(data.get("stability_min_samples", stress_config["stability_min_samples"]))
                stress_config["stability_p95_max_ms"] = int(data.get("stability_p95_max_ms", stress_config["stability_p95_max_ms"]))
                stress_config["recv_timeout"] = float(data.get("recv_timeout", stress_config["recv_timeout"]))
                stress_config["between_attempts_sleep"] = float(data.get("between_attempts_sleep", stress_config["between_attempts_sleep"]))
                if isinstance(data.get("mobile_user_agents"), list) and data.get("mobile_user_agents"):
                    stress_config["user_agents"] = [str(x) for x in data["mobile_user_agents"] if str(x).strip()]
                elif isinstance(data.get("user_agents"), list) and data.get("user_agents"):
                    stress_config["user_agents"] = [str(x) for x in data["user_agents"] if str(x).strip()]
                if isinstance(data.get("probe_paths"), list) and data.get("probe_paths"):
                    stress_config["probe_paths"] = [str(x) for x in data["probe_paths"] if str(x).strip()]
        except: pass

    raw_external_loaded = False
    print(f"📡 Начинаю добор до 200. В очереди (текущие+отложенные): {len(check_queue)}")

    # --- [ШАГ 4: ЦИКЛ ПРОВЕРКИ] ---
    workers = max(1, int(stress_config.get("workers", 32)))
    batch_size = max(20, workers * 2)
    max_check_duration_sec = max(60, int(stress_config.get("max_check_duration_sec", 5 * 60 * 60)))
    print(f"⚙️ Параллельная проверка: workers={workers}, batch={batch_size}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while len(working_for_sub) < 200:
            if time.time() - start_time >= max_check_duration_sec:
                print(f"⏱️ Достигнут лимит времени проверки ({max_check_duration_sec} сек)")
                note_reason(reason_stats, "limit_reached_time", extra=str(max_check_duration_sec))
                break
            if checked_today >= MAX_TO_CHECK:
                print("🛑 Лимит проверок исчерпан")
                note_reason(reason_stats, "limit_reached", extra=str(MAX_TO_CHECK))
                break

            # Если текущая и отложенная очереди закончились — один раз догружаем новые.
            if idx >= len(check_queue) and not raw_external_loaded:
                raw_external = download_raw_data(EXTERNAL_SOURCE_URL)
                extend_check_queue(sorted(raw_external, key=lambda l: ranking_sort_key(l, ranking_db)))
                raw_external_loaded = True
                print(f"🆕 Догружены новые кандидаты: +{len(raw_external)} (в очереди теперь {len(check_queue) - idx})")
                if idx >= len(check_queue):
                    note_reason(reason_stats, "no_candidates_after_external_load")
                    break
            elif idx >= len(check_queue):
                note_reason(reason_stats, "queue_exhausted")
                break

            batch = []
            while idx < len(check_queue) and len(batch) < batch_size and (checked_today + len(batch)) < MAX_TO_CHECK:
                link = check_queue[idx]
                idx += 1

                clean_link = link.strip()
                base_part = clean_link.split("#", 1)[0].strip()
                if base_part in seen_parts:
                    note_reason(reason_stats, "skip_duplicate_base", base_part)
                    continue

                link_scheme = get_link_scheme(base_part)
                if not link_scheme:
                    note_reason(reason_stats, "skip_unknown_scheme", base_part)
                    continue

                # SNI-фильтр — только для vless (у vmess/trojan/ss нет sni= в URL)
                if link_scheme == "vless" and is_sni_suspicious(link):
                    note_reason(reason_stats, "skip_sni_suspicious", base_part)
                    continue

                # Извлекаем хост/порт: для vmess из base64, иначе из URL
                if link_scheme == "vmess":
                    pc = parse_vmess_link(base_part)
                    if not pc or not pc["addr"] or not pc["port"]:
                        note_reason(reason_stats, "skip_bad_endpoint", base_part)
                        continue
                    host, port = pc["addr"], str(pc["port"])
                else:
                    endpoint, host, port = extract_host_port(base_part)
                    if not endpoint or not host or not port:
                        note_reason(reason_stats, "skip_bad_endpoint", base_part)
                        continue

                if host in runtime_blocked_hosts:
                    note_reason(reason_stats, "skip_runtime_blocked_host", base_part, runtime_blocked_hosts[host])
                    continue

                host_precheck_counts[host] = host_precheck_counts.get(host, 0) + 1
                if host_precheck_counts[host] > 5:
                    note_reason(reason_stats, "skip_host_precheck_limit", base_part, host)
                    continue

                if is_ipv6(host):
                    note_reason(reason_stats, "skip_ipv6", base_part)
                    add_to_blacklist(base_part)
                    remove_from_input_file(base_part)
                    continue

                auth_ok, auth_reason = validate_protocol_auth(base_part, link_scheme)
                if not auth_ok:
                    note_reason(reason_stats, auth_reason, base_part)
                    add_to_blacklist(base_part)
                    continue

                transport_ok, transport_reason = validate_transport_requirements(base_part)
                if not transport_ok:
                    note_reason(reason_stats, transport_reason, base_part)
                    add_to_blacklist(base_part)
                    continue

                batch.append((link, base_part, host))

            if not batch:
                continue

            futures = {
                pool.submit(l7_multi_probe_host_serialized, link, host, stress_config): (base_part, host)
                for link, base_part, host in batch
            }
            for fut in as_completed(futures):
                if len(working_for_sub) >= 200 or checked_today >= MAX_TO_CHECK:
                    break
                base_part, host = futures[fut]
                checked_today += 1
                remove_from_input_file(base_part)
                print(f"🔍 Тестирую: {host}...", end=" ", flush=True)
                try:
                    is_alive, current_latency = fut.result()
                except Exception:
                    is_alive, current_latency = False, 0

                if is_alive:
                    host_l7_reject_counts[host] = 0
                    ip_counts[host] = ip_counts.get(host, 0) + 1
                    if ip_counts[host] > 5:
                        print(f"♻️ Скип IP {host} (лимит)")
                        note_reason(reason_stats, "skip_ip_limit", base_part, host)
                        continue

                    country = get_country_code(host, countries_cache)
                    if country not in ALLOWED_COUNTRIES:
                        print(f"🌍 Мимо ({country})")
                        note_reason(reason_stats, "skip_country", base_part, country)
                        continue

                    working_for_base.append(base_part)
                    seen_parts.add(base_part)

                    sub_link = base_part
                    if "sni=" not in sub_link.lower() and not is_ipv6(host):
                        sep = "&" if "?" in sub_link else "?"
                        sub_link += f"{sep}sni={host}"

                    ping_label = f"{current_latency}ms"
                    final_link = rebuild_link_name(sub_link, f"mob {counter} [{ping_label}]")
                    working_for_sub.append(final_link)
                    print(f"✅ ОК {len(working_for_sub)}/200 ({country}): {current_latency}ms")
                    note_reason(reason_stats, "ok", base_part, f"{country},{current_latency}ms")
                    counter += 1
                else:
                    if current_latency < 0:
                        # Подтверждаем L7-отказ повторной проверкой с более мягкими параметрами,
                        # чтобы убрать ложные "reject" на пиках нагрузки/потерях.
                        recheck_alive, recheck_latency = l7_multi_probe_host_serialized(
                            base_part,
                            host,
                            build_recheck_stress_config(stress_config),
                        )
                        if recheck_alive:
                            host_l7_reject_counts[host] = 0
                            ip_counts[host] = ip_counts.get(host, 0) + 1
                            if ip_counts[host] > 5:
                                print(f"♻️ Скип IP {host} (лимит)")
                                note_reason(reason_stats, "skip_ip_limit", base_part, host)
                                continue

                            country = get_country_code(host, countries_cache)
                            if country not in ALLOWED_COUNTRIES:
                                print(f"🌍 Мимо ({country})")
                                note_reason(reason_stats, "skip_country", base_part, country)
                                continue

                            working_for_base.append(base_part)
                            seen_parts.add(base_part)

                            sub_link = base_part
                            if "sni=" not in sub_link.lower() and not is_ipv6(host):
                                sep = "&" if "?" in sub_link else "?"
                                sub_link += f"{sep}sni={host}"

                            ping_label = f"{recheck_latency}ms"
                            final_link = rebuild_link_name(sub_link, f"mob {counter} [{ping_label}]")
                            working_for_sub.append(final_link)
                            print(f"🟡 RECOVERED {len(working_for_sub)}/200 ({country}): {recheck_latency}ms")
                            note_reason(reason_stats, "ok_after_recheck", base_part, f"{country},{recheck_latency}ms")
                            counter += 1
                            continue

                        print("⛔ UUID/L7-доступ отклонен (подтверждено повторной проверкой)")
                        note_reason(reason_stats, "fail_l7_reject_confirmed", base_part)
                        host_l7_reject_counts[host] = host_l7_reject_counts.get(host, 0) + 1
                        # Не блочим хост после единичного L7-отказа:
                        # на одном IP могут жить как битые, так и рабочие UUID.
                        if host_l7_reject_counts[host] >= 3:
                            runtime_blocked_hosts[host] = "l7_reject_threshold"
                    elif current_latency == -2:
                        print("📉 Нестабильный сервер (сильный разброс/недоступность)")
                        note_reason(reason_stats, "fail_unstable_latency", base_part)
                        runtime_blocked_hosts[host] = "unstable"
                    else:
                        print("💀 Мертв")
                        note_reason(reason_stats, "fail_dead", base_part)
                        runtime_blocked_hosts[host] = "dead"

                    add_to_blacklist(base_part)
                    note_reason(reason_stats, "blacklisted_immediate", base_part, runtime_blocked_hosts.get(host, "failed"))
                    if base_part in ranking_db:
                        del ranking_db[base_part]

    # --- [ШАГ 5: ФИНАЛЬНОЕ СОХРАНЕНИЕ] ---
    
    # 1. Подписка (всегда до 200, закрепы в начале)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(HEADER.strip() + "\n\n" + "\n".join(working_for_sub))

    # 2. Очередь (те, кого не проверяли)
    new_deferred = check_queue[idx:]
    with open('test1/deferred.txt', "w", encoding="utf-8") as f:
        f.write("\n".join(new_deferred))

    # 3. База 1.txt (Бессмертные + те, кто прошел проверку сегодня)
    final_base_to_save = list(seen_immortals | set(working_for_base))
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(final_base_to_save))

    with open(RANKING_FILE, "w", encoding="utf-8") as f:
        json.dump(ranking_db, f, ensure_ascii=False, indent=4)
    with open(REASONS_FILE, "w", encoding="utf-8") as f:
        json.dump(reason_stats, f, ensure_ascii=False, indent=4)

    print(f"🏁 Завершено. Подписка: {len(working_for_sub)}, Очередь: {len(new_deferred)}")
    print(f"🧾 Reasons: {json.dumps(reason_stats, ensure_ascii=False)}")
    return {
        "subscription_size": len(working_for_sub),
        "checked_today": checked_today,
        "alive_today": len(working_for_base),
    }

if __name__ == "__main__":
    init_checker_lib()
    result = main()
    try:
        with open(RUN_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не удалось сохранить {RUN_RESULT_FILE}: {e}")

    # Для оркестрации из CI/YML:
    # при CHECK_FAIL_ON_ZERO_ALIVE=1 завершаем кодом 2, если не добавилось ни одного нового живого.
    if os.getenv("CHECK_FAIL_ON_ZERO_ALIVE", "0") == "1":
        if result.get("checked_today", 0) > 0 and result.get("alive_today", 0) == 0:
            raise SystemExit(2)
