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
        print(f"⚠️ Ошибка L7 чекера: {e}")
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
    """Многократный L7-пробник: снижает ложные 'ОК', если сервер нестабилен в мобильной сети."""
    min_hits = max(1, int(stress_config.get("l7_min_success", 2)))
    max_candidates = max(1, int(stress_config.get("l7_max_candidates", 3)))
    probe_attempts = max(1, int(stress_config.get("probe_attempts", 4)))
    between_attempts_sleep = max(0.0, float(stress_config.get("between_attempts_sleep", 0.35)))
    timeout_sec = int(max(1, stress_config.get("timeout", 5)))
    max_latency_ms = max(1, int(stress_config.get("max_latency_ms", 6000)))

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
            latency = probe_vless_l7(link, candidate_sni, timeout=timeout_sec)
            if latency < 0:
                # Жесткий отказ L7 при доступном TCP (например, отключенный UUID) — нет смысла долбить дальше.
                return False, -1
            if latency > 0 and latency <= max_latency_ms:
                hits += 1
                if best_latency == 0 or latency < best_latency:
                    best_latency = latency
                if hits >= min_hits:
                    return True, best_latency
            if between_attempts_sleep > 0:
                time.sleep(between_attempts_sleep)
    return False, 0

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
    
    print(f"📦 Загружено закрепов: {len(pinned_list)}")

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

    # --- [ШАГ 2: ГОТОВИМ ОЧЕРЕДЬ НА ПРОВЕРКУ] ---
    raw_external = download_raw_data(EXTERNAL_SOURCE_URL)
    # Собираем всё в одну очередь по приоритету: Отложенные -> Новые -> Старая база
    combined_potential = deferred_base + raw_external + current_base
    
    check_queue = []
    seen_in_queue = set()
    for link in combined_potential:
        base = link.split('#')[0].strip()
        # Проверяем, что ссылки нет в бессмертных, она не дубликат в очереди и не в блэклисте
        if base not in seen_immortals and base not in seen_in_queue and base not in blacklist:
            check_queue.append(link)
            seen_in_queue.add(base)
    check_queue.sort(key=lambda l: ranking_sort_key(l, ranking_db))

    # --- [ШАГ 3: ПОДГОТОВКА К ЦИКЛУ] ---
    working_for_sub = immortals[:200] # Сразу забиваем подписку бессмертными
    working_for_base = []            # Сюда пойдут те, кто реально ответил
    
    now = time.time()
    counter = len(working_for_sub) + 1
    idx = 0
    checked_today = 0
    MAX_TO_CHECK = 500 
    ip_counts = {}
    seen_ips = set()
    seen_parts = set()

    # Настройки стресс-теста (твой блок 1-в-1)
    stress_config = {
        "timeout": 2.5, "dpi_sleep": 0.5, "target_mtu": 1280,
        "probe_attempts": 4, "min_success": 2, "recv_timeout": 1.7,
        "between_attempts_sleep": 0.35,
        "l7_min_success": 2,
        "l7_max_candidates": 3,
        "workers": 32,
        "max_latency_ms": 6000,
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
                stress_config["workers"] = int(data.get("workers", stress_config["workers"]))
                stress_config["max_latency_ms"] = int(data.get("max_latency_ms", stress_config["max_latency_ms"]))
                stress_config["recv_timeout"] = float(data.get("recv_timeout", stress_config["recv_timeout"]))
                stress_config["between_attempts_sleep"] = float(data.get("between_attempts_sleep", stress_config["between_attempts_sleep"]))
                if isinstance(data.get("mobile_user_agents"), list) and data.get("mobile_user_agents"):
                    stress_config["user_agents"] = [str(x) for x in data["mobile_user_agents"] if str(x).strip()]
                elif isinstance(data.get("user_agents"), list) and data.get("user_agents"):
                    stress_config["user_agents"] = [str(x) for x in data["user_agents"] if str(x).strip()]
                if isinstance(data.get("probe_paths"), list) and data.get("probe_paths"):
                    stress_config["probe_paths"] = [str(x) for x in data["probe_paths"] if str(x).strip()]
        except: pass

    print(f"📡 Начинаю добор до 200. В очереди: {len(check_queue)}")

    # --- [ШАГ 4: ЦИКЛ ПРОВЕРКИ] ---
    workers = max(1, int(stress_config.get("workers", 32)))
    batch_size = max(20, workers * 2)
    print(f"⚙️ Параллельная проверка: workers={workers}, batch={batch_size}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while len(working_for_sub) < 200 and idx < len(check_queue):
            if checked_today >= MAX_TO_CHECK:
                print("🛑 Лимит проверок исчерпан")
                break

            batch = []
            while idx < len(check_queue) and len(batch) < batch_size and (checked_today + len(batch)) < MAX_TO_CHECK:
                link = check_queue[idx]
                idx += 1

                if is_sni_suspicious(link):
                    continue

                clean_link = link.strip()
                base_part = clean_link.split("#", 1)[0].strip()
                if base_part in seen_parts:
                    continue

                # Основные фильтры формата (uuid + endpoint)
                if not re.search(r'[a-f0-9\-]{36}@', base_part):
                    continue
                endpoint, host, port = extract_host_port(base_part)
                if not endpoint or not host or not port:
                    continue
                if is_ipv6(host):
                    add_to_blacklist(base_part)
                    remove_from_input_file(base_part)
                    continue

                batch.append((link, base_part, host))

            if not batch:
                continue

            futures = {
                pool.submit(l7_multi_probe, link, stress_config): (base_part, host)
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
                    ip_counts[host] = ip_counts.get(host, 0) + 1
                    if ip_counts[host] > 5:
                        print(f"♻️ Скип IP {host} (лимит)")
                        continue

                    country = get_country_code(host, countries_cache)
                    if country not in ALLOWED_COUNTRIES:
                        print(f"🌍 Мимо ({country})")
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
                    counter += 1
                else:
                    if current_latency < 0:
                        print("⛔ UUID/доступ отклонен провайдером")
                    else:
                        print("💀 Мертв")
                    if base_part in ranking_db:
                        del ranking_db[base_part]
                    fail_time = history.get(base_part, now)
                    if now - fail_time > 86400:
                        with open(BLACKLIST_FILE, 'a') as bl:
                            bl.write(base_part + "\n")

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

    print(f"🏁 Завершено. Подписка: {len(working_for_sub)}, Очередь: {len(new_deferred)}")

if __name__ == "__main__":
    init_checker_lib()
    main()
