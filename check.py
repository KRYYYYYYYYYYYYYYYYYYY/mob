import socket
import re
import os
import ssl
import json
import urllib.parse
import urllib.request
import time
import subprocess
import ipaddress
import ctypes
from concurrent.futures import ThreadPoolExecutor, as_completed


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
ENDPOINT_CACHE_FILE = 'test1/endpoint_cache.json'

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
CHECK_WORKERS = 12
CHECK_BATCH_SIZE = 48
TCP_PRECHECK_TIMEOUT = 1.2
MAX_CANDIDATE_TIME = 18.0
PROBE_TIMEOUT = 3
STRICT_L7 = os.getenv("CHECK_STRICT_L7", "0").strip().lower() in {"1", "true", "yes"}
ENDPOINT_FAIL_THRESHOLD = 3
ENDPOINT_SKIP_HOURS = 8

# Подключаем новую библиотеку
go_lib = None

def log(message: str) -> None:
    """Единый логгер с принудительным flush для GitHub Actions."""
    print(message, flush=True)


def init_checker_lib() -> None:
    """Инициализирует Go-библиотеку проверки, если она доступна."""
    global go_lib
    lib_path = os.path.abspath("libchecker.so")
    if not os.path.exists(lib_path):
        log("❌ ОШИБКА: Библиотека libchecker.so не найдена!")
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

def probe_vless_l7(link, target_sni, timeout=5):
    """Парсит VLESS ссылку и возвращает пинг в мс (0 если ошибка)."""
    if go_lib is None:
        return 0
    try:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        
        _, host, port = extract_host_port(link)
        
        uuid = parsed.username if parsed.username else ""
        pbk = params.get('pbk', [''])[0]
        sid = params.get('sid', [''])[0]
        flow = params.get('flow', [''])[0]
        
        # Вызов Go (теперь возвращает int с миллисекундами)
        latency = go_lib.CheckVlessL7(
            host.encode('utf-8'),
            int(port),
            uuid.encode('utf-8'),
            target_sni.encode('utf-8'),
            pbk.encode('utf-8'),
            sid.encode('utf-8'),
            flow.encode('utf-8'),
            int(timeout)
        )
        return latency # Вернет 0 или время в мс
    except Exception as e:
        log(f"⚠️ Ошибка L7 чекера: {e}")
        return 0

def extract_sni(link):
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    return params.get("sni", [""])[0]

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
                    found = [line.strip() for line in content.splitlines() if "vless://" in line]
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

def dedupe_by_base(links):
    unique = []
    seen = set()
    for link in links:
        base = link.split('#')[0].strip()
        if not base or base in seen:
            continue
        seen.add(base)
        unique.append(link)
    return unique

def dedupe_by_endpoint(links):
    """Оставляет по одному конфигу на endpoint host:port (в порядке приоритета)."""
    unique = []
    seen = set()
    for link in links:
        base = link.split('#', 1)[0].strip()
        _, host, port = extract_host_port(base)
        if not host or not port:
            continue
        key = (host, port)
        if key in seen:
            continue
        seen.add(key)
        unique.append(link)
    return unique

def load_endpoint_cache(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_endpoint_cache(path: str, cache: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def l7_multi_probe(link: str, stress_config: dict):
    """Многократный L7-пробник: снижает ложные 'ОК', если сервер нестабилен в мобильной сети."""
    min_hits = max(1, int(stress_config.get("l7_min_success", 2)))
    max_candidates = max(1, int(stress_config.get("l7_max_candidates", 3)))
    probe_attempts = max(1, int(stress_config.get("probe_attempts", 4)))
    between_attempts_sleep = max(0.0, float(stress_config.get("between_attempts_sleep", 0.35)))
    timeout_sec = int(max(1, stress_config.get("timeout", 5)))
    max_candidate_time = max(
        4.0,
        float(os.getenv("CHECK_MAX_CANDIDATE_SEC", stress_config.get("max_candidate_sec", MAX_CANDIDATE_TIME))),
    )
    deadline = time.monotonic() + max_candidate_time

    candidates = extract_sni_candidates(link)
    if not candidates:
        native_sni = extract_sni(link)
        if native_sni:
            candidates = [native_sni]
    candidates = [c for c in candidates if c and not any(w in c.lower() for w in BAD_SNI_KEYWORDS)]
    candidates = candidates[:max_candidates]

    hits = 0
    best_latency = 0
    for candidate_sni in candidates:
        for _ in range(probe_attempts):
            if time.monotonic() >= deadline:
                return False, 0
            latency = probe_vless_l7(link, candidate_sni, timeout=timeout_sec)
            if latency > 0:
                hits += 1
                if best_latency == 0 or latency < best_latency:
                    best_latency = latency
                if hits >= min_hits:
                    return True, best_latency
            if latency > 0 and between_attempts_sleep > 0:
                time.sleep(between_attempts_sleep)
    return False, 0

def probe_link_latency(link: str) -> int:
    """Быстрый single-pass L7 пробник по SNI-кандидатам."""
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    _, host, port = extract_host_port(link)
    if not host or not port:
        return 0
    uuid = parsed.username if parsed.username else ""
    pbk = params.get('pbk', [''])[0]
    sid = params.get('sid', [''])[0]
    flow = params.get('flow', [''])[0]

    tried = set()
    candidates = []
    for cand_sni in extract_sni_candidates(link):
        cand_sni = cand_sni.strip()
        if not cand_sni or cand_sni in tried:
            continue
        tried.add(cand_sni)
        candidates.append(cand_sni)
    fallback_sni = extract_sni(link).strip()
    if fallback_sni and fallback_sni not in tried:
        candidates.append(fallback_sni)

    if go_lib is not None and candidates:
        try:
            latency = int(go_lib.CheckVlessL7(
                host.encode('utf-8'),
                int(port),
                uuid.encode('utf-8'),
                ",".join(candidates).encode('utf-8'),
                pbk.encode('utf-8'),
                sid.encode('utf-8'),
                flow.encode('utf-8'),
                int(PROBE_TIMEOUT),
            ) or 0)
            if latency > 0:
                return latency
        except Exception as e:
            log(f"⚠️ Multi L7 call failed: {e}")

    for cand_sni in candidates:
        latency = probe_vless_l7(link, cand_sni, timeout=PROBE_TIMEOUT)
        if latency > 0:
            return int(latency)
    return 0

def probe_tcp_latency(host: str, port: str, timeout_sec: float = 1.6) -> int:
    try:
        start = time.monotonic()
        with socket.create_connection((host, int(port)), timeout=timeout_sec):
            return int((time.monotonic() - start) * 1000)
    except Exception:
        return 0

def tcp_precheck(host: str, port: str) -> bool:
    """Быстрый фильтр мертвых endpoint'ов перед дорогой L7-проверкой."""
    try:
        with socket.create_connection((host, int(port)), timeout=TCP_PRECHECK_TIMEOUT):
            return True
    except Exception:
        return False

def probe_candidate(base_part: str, link: str, host: str, port: str, stress_config: dict, countries_cache: dict):
    """Проверка кандидата в worker-потоке: L7 + страна."""
    if not tcp_precheck(host, port):
        return {
            "base": base_part,
            "host": host,
            "port": port,
            "alive": False,
            "latency": 0,
            "country": "Unknown",
            "reason": "tcp_fail",
        }

    quick_latency = probe_link_latency(link)
    if quick_latency <= 0 and not STRICT_L7:
        tcp_latency = probe_tcp_latency(host, port, timeout_sec=1.6)
        if tcp_latency > 0:
            country = get_country_code(host, countries_cache)
            return {
                "base": base_part,
                "host": host,
                "port": port,
                "alive": True,
                "latency": int(tcp_latency + 900),
                "country": country,
                "reason": "tcp_fallback",
            }

    if quick_latency <= 0:
        is_alive, current_latency = l7_multi_probe(link, stress_config)
    else:
        is_alive, current_latency = True, quick_latency

    if not is_alive:
        return {
            "base": base_part,
            "host": host,
            "port": port,
            "alive": False,
            "latency": 0,
            "country": "Unknown",
            "reason": "l7_fail",
        }

    country = get_country_code(host, countries_cache)
    return {
        "base": base_part,
        "host": host,
        "port": port,
        "alive": True,
        "latency": int(current_latency),
        "country": country,
        "reason": "ok",
    }

def main():
    import subprocess
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

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
            pinned_list = [line.strip() for line in f if "vless://" in line]
    
    log(f"📦 Загружено закрепов: {len(pinned_list)}")

    # 2. Загружаем Фавориты (Favorites)
    fav_bases = set()
    fav_full_links = []
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if "vless://" in line:
                    link = line.strip()
                    fav_full_links.append(link)
                    fav_bases.add(link.split("#")[0].strip())
    log(f"⭐ Загружено фаворитов: {len(fav_bases)}")

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
    endpoint_cache = load_endpoint_cache(ENDPOINT_CACHE_FILE)

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
        immortals.append(f_link)
        seen_immortals.add(base)

    log(f"🛡️ Итого бессмертных в начале списка: {len(immortals)}")

    # --- [ШАГ 2: ГОТОВИМ ОЧЕРЕДЬ НА ПРОВЕРКУ] ---
    had_deferred_at_start = len(deferred_base) > 0
    external_loaded = False
    if had_deferred_at_start:
        # Пока есть отложенные — работаем только с ними + текущей базой.
        combined_potential = deferred_base + current_base
        log(f"📦 Режим deferred-first: deferred={len(deferred_base)}, external=postponed")
    else:
        raw_external = download_raw_data(EXTERNAL_SOURCE_URL)
        external_loaded = True
        # Если deferred пустой — сразу подключаем новые.
        combined_potential = raw_external + current_base
        log(f"📦 Режим normal: deferred=0, external={len(raw_external)}")
    
    check_queue = []
    seen_in_queue = set()
    for link in combined_potential:
        base = link.split('#')[0].strip()
        # Проверяем, что ссылки нет в бессмертных, она не дубликат в очереди и не в блэклисте
        if base not in seen_immortals and base not in seen_in_queue and base not in blacklist:
            check_queue.append(link)
            seen_in_queue.add(base)
    check_queue = dedupe_by_base(check_queue)
    check_queue.sort(key=lambda l: ranking_sort_key(l, ranking_db))
    before_endpoint_dedupe = len(check_queue)
    check_queue = dedupe_by_endpoint(check_queue)
    if len(check_queue) != before_endpoint_dedupe:
        log(f"🧹 endpoint-dedupe: {before_endpoint_dedupe} -> {len(check_queue)}")

    # --- [ШАГ 3: ПОДГОТОВКА К ЦИКЛУ] ---
    working_for_sub = immortals[:200] # Сразу забиваем подписку бессмертными
    working_for_base = []            # Сюда пойдут те, кто реально ответил
    
    now = time.time()
    counter = len(working_for_sub) + 1
    idx = 0
    checked_today = 0
    MAX_TO_CHECK = 300 
    ip_counts = {}
    seen_ips = set()
    seen_parts = set()
    checked_endpoints = set()

    # Настройки стресс-теста (твой блок 1-в-1)
    stress_config = {
        "timeout": 2.5, "dpi_sleep": 0.5, "target_mtu": 1280,
        "probe_attempts": 4, "min_success": 2, "recv_timeout": 1.7,
        "between_attempts_sleep": 0.35,
        "l7_min_success": 2,
        "l7_max_candidates": 3,
        "user_agents": list(DEFAULT_MOBILE_USER_AGENTS),
        "probe_paths": list(DEFAULT_PROBE_PATHS),
    }
    if os.path.exists('test1/stress_profile.json'):
        try:
            with open('test1/stress_profile.json', 'r') as f:
                data = json.load(f)
                stress_config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
                stress_config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
                stress_config["target_mtu"] = data.get("target_mtu", 1280)
                stress_config["probe_attempts"] = int(data.get("probe_attempts", stress_config["probe_attempts"]))
                stress_config["min_success"] = int(data.get("min_success", stress_config["min_success"]))
                stress_config["l7_min_success"] = int(data.get("l7_min_success", stress_config["l7_min_success"]))
                stress_config["l7_max_candidates"] = int(data.get("l7_max_candidates", stress_config["l7_max_candidates"]))
                stress_config["recv_timeout"] = float(data.get("recv_timeout", stress_config["recv_timeout"]))
                stress_config["between_attempts_sleep"] = float(data.get("between_attempts_sleep", stress_config["between_attempts_sleep"]))
                if isinstance(data.get("mobile_user_agents"), list) and data.get("mobile_user_agents"):
                    stress_config["user_agents"] = [str(x) for x in data["mobile_user_agents"] if str(x).strip()]
                elif isinstance(data.get("user_agents"), list) and data.get("user_agents"):
                    stress_config["user_agents"] = [str(x) for x in data["user_agents"] if str(x).strip()]
                if isinstance(data.get("probe_paths"), list) and data.get("probe_paths"):
                    stress_config["probe_paths"] = [str(x) for x in data["probe_paths"] if str(x).strip()]
        except: pass

    workers = max(1, int(os.getenv("CHECK_WORKERS", str(CHECK_WORKERS))))
    min_workers = max(2, workers // 2)
    max_workers = max(workers, int(os.getenv("CHECK_MAX_WORKERS", str(max(16, workers)))))
    adaptive_workers = workers
    skip_window_hours = max(1, int(os.getenv("ENDPOINT_SKIP_HOURS", str(ENDPOINT_SKIP_HOURS))))
    skip_window_sec = skip_window_hours * 3600
    fail_threshold = max(1, int(os.getenv("ENDPOINT_FAIL_THRESHOLD", str(ENDPOINT_FAIL_THRESHOLD))))
    log(f"📡 Начинаю добор до 200. В очереди: {len(check_queue)}. workers={adaptive_workers}")

    # --- [ШАГ 4: ЦИКЛ ПРОВЕРКИ] ---
    while len(working_for_sub) < 200 and idx < len(check_queue):
        if checked_today >= MAX_TO_CHECK:
            log("🛑 Лимит проверок исчерпан")
            break

        remaining_checks = MAX_TO_CHECK - checked_today
        batch_limit = min(CHECK_BATCH_SIZE, remaining_checks)
        to_probe = []

        while len(to_probe) < batch_limit:
            if idx >= len(check_queue):
                if had_deferred_at_start and not external_loaded:
                    log("🧩 Deferred исчерпаны -> догружаю external и продолжаю")
                    raw_external = download_raw_data(EXTERNAL_SOURCE_URL)
                    external_loaded = True
                    # Добавляем только то, чего еще не было.
                    check_queue = dedupe_by_base(check_queue + raw_external)
                    log(f"🌐 Догружено external: {len(raw_external)}, очередь={len(check_queue)}")
                    continue
                break

            link = check_queue[idx]
            idx += 1
            if is_sni_suspicious(link):
                continue

            clean_link = link.strip()
            base_part = clean_link.split("#", 1)[0].strip()
            if base_part in seen_parts:
                continue
            if not re.search(r'[a-f0-9\-]{36}@', base_part):
                continue

            endpoint, host, port = extract_host_port(base_part)
            if not endpoint or not host or not port:
                continue
            if is_ipv6(host):
                blacklist.add(base_part)
                continue
            endpoint_key_str = f"{host}:{port}"
            cached_ep = endpoint_cache.get(endpoint_key_str, {})
            skip_until = int(cached_ep.get("skip_until", 0) or 0)
            if skip_until > int(time.time()):
                continue
            endpoint_key = (host, port)
            if endpoint_key in checked_endpoints:
                continue
            checked_endpoints.add(endpoint_key)

            to_probe.append((base_part, clean_link, host, port))

        if not to_probe:
            continue

        log(f"⚙️ Батч проверки: {len(to_probe)} workers={adaptive_workers}")
        with ThreadPoolExecutor(max_workers=adaptive_workers) as executor:
            future_map = {
                executor.submit(probe_candidate, base_part, clean_link, host, port, stress_config, countries_cache): (base_part, clean_link, host, port)
                for base_part, clean_link, host, port in to_probe
            }
            batch_total = 0
            batch_dead = 0
            batch_l7_fail = 0
            for future in as_completed(future_map):
                if len(working_for_sub) >= 200:
                    break
                if checked_today >= MAX_TO_CHECK:
                    break

                base_part, clean_link, host, port = future_map[future]
                checked_today += 1
                batch_total += 1
                try:
                    result = future.result()
                except Exception as e:
                    log(f"💥 Ошибка worker {host}:{port} — {e}")
                    result = {
                        "base": base_part,
                        "host": host,
                        "port": port,
                        "alive": False,
                        "latency": 0,
                        "country": "Unknown",
                    }

                if not result["alive"]:
                    reason = result.get("reason", "fail")
                    log(f"💀 Мертв: {host}:{port} ({reason})")
                    batch_dead += 1
                    if reason == "l7_fail":
                        batch_l7_fail += 1
                    if base_part in ranking_db:
                        del ranking_db[base_part]
                    fail_time = history.get(base_part, now)
                    if now - fail_time > 86400:
                        blacklist.add(base_part)
                    ep_key = f"{host}:{port}"
                    ep_data = endpoint_cache.get(ep_key, {})
                    new_fail_count = int(ep_data.get("fail_count", 0) or 0) + 1
                    ep_data.update({
                        "fail_count": new_fail_count,
                        "last_status": reason,
                        "last_seen": int(time.time()),
                    })
                    if new_fail_count >= fail_threshold:
                        ep_data["skip_until"] = int(time.time()) + skip_window_sec
                    endpoint_cache[ep_key] = ep_data
                    continue

                resolved_ip = host
                ip_counts[resolved_ip] = ip_counts.get(resolved_ip, 0) + 1
                if ip_counts[resolved_ip] > 5:
                    log(f"♻️ Скип IP {resolved_ip} (лимит)")
                    continue

                country = result["country"]
                if country not in ALLOWED_COUNTRIES:
                    log(f"🌍 Мимо ({country}): {host}:{port}")
                    continue

                current_latency = int(result["latency"])
                ep_key = f"{host}:{port}"
                endpoint_cache[ep_key] = {
                    "fail_count": 0,
                    "skip_until": 0,
                    "last_status": result.get("reason", "ok"),
                    "last_seen": int(time.time()),
                    "last_latency": current_latency,
                }
                working_for_base.append(base_part)
                seen_parts.add(base_part)

                sub_link = base_part
                if "sni=" not in sub_link.lower() and not is_ipv6(host):
                    sep = "&" if "?" in sub_link else "?"
                    sub_link += f"{sep}sni={host}"

                ping_label = f"{current_latency}ms"
                final_link = rebuild_link_name(sub_link, f"mob {counter} [{ping_label}]")
                working_for_sub.append(final_link)
                log(f"✅ ОК {len(working_for_sub)}/200 ({country}): {host}:{port} {current_latency}ms")
                counter += 1
            if batch_total > 0:
                dead_rate = batch_dead / batch_total
                l7_fail_rate = batch_l7_fail / batch_total
                if dead_rate >= 0.8 and adaptive_workers < max_workers:
                    adaptive_workers = min(max_workers, adaptive_workers + 2)
                    log(f"📈 adaptive-workers up -> {adaptive_workers} (dead_rate={dead_rate:.2f})")
                elif l7_fail_rate >= 0.45 and adaptive_workers > min_workers:
                    adaptive_workers = max(min_workers, adaptive_workers - 1)
                    log(f"📉 adaptive-workers down -> {adaptive_workers} (l7_fail_rate={l7_fail_rate:.2f})")

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

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(countries_cache, f, ensure_ascii=False, indent=2)

    with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(blacklist)))
    save_endpoint_cache(ENDPOINT_CACHE_FILE, endpoint_cache)

    log(f"🏁 Завершено. Подписка: {len(working_for_sub)}, Очередь: {len(new_deferred)}")

if __name__ == "__main__":
    init_checker_lib()
    main()
