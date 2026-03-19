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

# Настройки путей
INPUT_FILE = 'test1/1.txt'
OUTPUT_FILE = 'kr/mob/wifi.txt'
STATUS_FILE = 'test1/status.json'
CACHE_FILE = 'test1/countries_cache.json' # Добавь эту константу для порядка
RANKING_FILE = 'test1/ranking.json'
VETTED_FILE = 'test1/vetted.txt'
PINNED_FILE = 'test1/pinned.txt'
FAVORITES_FILE = 'test1/favorites.txt'

EXTERNAL_SOURCE_URL = [
    "https://raw.githubusercontent.com/KRYYYYYYYYYYYYYYYYYYY/crazy_xray_checker/refs/heads/main/result/working.txt"
]

GRACE_PERIOD = 2 * 24 * 60 * 60 # 48 часов

HEADER = """# profile-title: 🏳️Мобильный инет🏳️
# remark: 🏳️Мобильный инет🏳️
# announce: Подписка для использования ТОЛЬКО на мобильном интернете в условиях БЕЛЫХ СПИСКОВ!
# profile-update-interval: 2
"""

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

DEFAULT_MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; SM-A336B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]
DEFAULT_PROBE_PATHS = ["/", "/generate_204", "/favicon.ico"]

# Подключаем новую библиотеку
lib_path = os.path.abspath("libchecker.so")
if not os.path.exists(lib_path):
    print("❌ ОШИБКА: Библиотека libchecker.so не найдена!")
else:
    go_lib = ctypes.cdll.LoadLibrary(lib_path)
    
    # Описываем аргументы для функции CheckVlessL7
    go_lib.CheckVlessL7.argtypes = [
        ctypes.c_char_p, # addr (host)
        ctypes.c_int,    # port
        ctypes.c_char_p, # uuid
        ctypes.c_char_p, # sni
        ctypes.c_char_p, # pbk
        ctypes.c_char_p, # sid
        ctypes.c_char_p, # flow
        ctypes.c_int     # timeout
    ]
    go_lib.CheckVlessL7.restype = ctypes.c_int

def probe_vless_l7(link, target_sni, timeout=5):
    """Парсит VLESS ссылку и возвращает пинг в мс (0 если ошибка)."""
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
    # Твоя текущая функция
    match = re.search(r"sni=([^&?#\s]+)", link)
    return match.group(1) if match else ""

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
        
        # Оставляем только те строки, которые НЕ содержат этот base_part
        new_lines = [l for l in lines if base_to_remove not in l]
        
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
    if not host: return False
    return ":" in host

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
    if os.path.exists('test1/blacklist.txt'):
        with open('test1/blacklist.txt', 'r') as f:
            current_bl = {line.strip() for line in f if line.strip()}
    
    if base_part not in current_bl:
        with open('test1/blacklist.txt', 'a') as f:
            f.write(base_part + "\n")

def main():
    import subprocess
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

    # --- ЗАГРУЗКА КЭША СТРАН (важно для get_country_code) ---
    countries_cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f: countries_cache = json.load(f)
        except: countries_cache = {}

    blacklist = set()
    pinned_list = []
    deferred_base = []
    current_base = []
    external_servers = []
    ranking_db = {}
    vetted_list = []
    
    blacklist = set()
    if os.path.exists('test1/blacklist.txt'):
        with open('test1/blacklist.txt', 'r') as f:
            blacklist = {line.strip() for line in f if line.strip()}

        # Загружаем "рейтинг выслуги"
    ranking_file = 'test1/ranking.json'
    ranking_db = {}
    if os.path.exists(ranking_file):
        try:
            with open(ranking_file, "r") as f: ranking_db = json.load(f)
        except: ranking_db = {}

    # Загружаем текущих проверенных (чтобы не дублировать)
    vetted_list = []
    if os.path.exists('test1/vetted.txt'):
        with open('test1/vetted.txt', 'r') as f:
            vetted_list = [line.strip() for line in f if line.strip()]


    # --- ДОБАВЛЯЕМ ЗАГРУЗКУ СПЕЦФАЙЛОВ ТУТ ---
    
    # 1. Загружаем Закрепленные (Pinned)
    pinned_list = []
    if os.path.exists('test1/pinned.txt'):
        with open('test1/pinned.txt', 'r', encoding='utf-8') as f:
            # Читаем всё целиком, убираем пустые строки
            pinned_list = [line.strip() for line in f if "vless://" in line]
    
    print(f"📦 Загружено закрепов из файла: {len(pinned_list)}")

    clean_pinned = {}
    for p in pinned_list:
        base = p.split("#")[0].strip()
        clean_pinned[base] = p  # последний вариант перезапишет предыдущий

    pinned_list = list(clean_pinned.values())

    # 2. Загружаем Фавориты (НОВЫЙ БЛОК)
    fav_bases = set()
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if "vless://" in line:
                    # Извлекаем только базу, чтобы сравнивать
                    fav_bases.add(line.split("#")[0].strip())
    print(f"⭐ Загружено фаворитов для защиты: {len(fav_bases)}")

    # 2. Загружаем Отложенные (Deferred)
    deferred_base = []
    if os.path.exists('test1/deferred.txt'):
        with open('test1/deferred.txt', 'r', encoding='utf-8') as f:
            deferred_base = [line.strip() for line in f if line.strip()]

    # ------------------------------------------

    # Дальше твоя стандартная загрузка
    current_base = []
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            current_base = f.read().splitlines()

    raw_external = download_raw_data(EXTERNAL_SOURCE_URL)
    # СОБИРАЕМ ОЧЕРЕДЬ: База + Отложенные + Новые
    # Это гарантирует, что "старички" из очереди проверятся раньше новичков
    combined_queue = pinned_list + deferred_base + raw_external + current_base

    # Убираем дубликаты, сохраняя этот новый приоритетный порядок
    unique_links = []
    seen_bases = set()
    for link in combined_queue:
        base = link.split('#')[0].strip()
        if base not in seen_bases:
            unique_links.append(link)
            seen_bases.add(base)

    # --- ПЕРЕЗАГРУЗКА ИСТОРИИ ПЕРЕД ПРОВЕРКОЙ ---
    history = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f: history = json.load(f)
        except: history = {}

    all_lines = pinned_list + deferred_base + external_servers + current_base

    # 1. Загрузка базы и истории
    current_base = []
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            current_base = f.read().splitlines()

    history = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f: history = json.load(f)
        except: history = {}
    
    working_for_base = []
    working_for_sub = []
    new_deferred = []   # <--- ДОБАВЬ ЭТО (сюда пойдут те, кто не влез в лимит)
    new_history = {}
    now = time.time()
    counter = 1
    checked_today = 0   # <--- ДОБАВЬ ЭТО (счетчик реальных проверок)
    MAX_TO_CHECK = 300  # <--- ДОБАВЬ ЭТО (лимит, чтобы скрипт не шел до конца очереди вечно)
    seen_ips = set()
    # ----------------------------------------------------------
    # --- ЦИКЛ ПРОВЕРКИ (ИЩЕМ 200 РАБОЧИХ) ---

    # --- ПЕРЕД ЦИКЛОМ: Создаем реестр IP ---
    ip_counts = {} 
    print(f"📡 Начинаю проверку. Цель: 200 серверов. Всего в очереди: {len(unique_links)}")
    
    
    seen_parts = set()
    
    idx = 0

    # --- НАСТРОЙКИ СТРЕСС-ТЕСТА (Интеграция твоего JSON) ---
    stress_config = {
        "timeout": 2.5,                # Дефолт
        "dpi_sleep": 0.5,              # Дефолт
        "target_mtu": 1280,            # Для мобильных сетей
        "probe_attempts": 4,           # Сколько разных сценариев пробуем
        "min_success": 2,              # Сколько успешных попыток нужно
        "recv_timeout": 1.7,
        "between_attempts_sleep": 0.35,
        "user_agents": list(DEFAULT_MOBILE_USER_AGENTS),
        "probe_paths": list(DEFAULT_PROBE_PATHS),
    }
    
    if os.path.exists('test1/stress_profile.json'):
        try:
            with open('test1/stress_profile.json', 'r') as f:
                data = json.load(f)
                # Берем 1800ms из твоего конфига и превращаем в секунды (1.8)
                stress_config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
                # Если mimic_dpi_delay: true, ставим паузу 0.5 сек (имитация лага мобилы)
                stress_config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
                stress_config["target_mtu"] = data.get("target_mtu", 1280)
                stress_config["probe_attempts"] = int(data.get("probe_attempts", stress_config["probe_attempts"]))
                stress_config["min_success"] = int(data.get("min_success", stress_config["min_success"]))
                stress_config["recv_timeout"] = float(data.get("recv_timeout", stress_config["recv_timeout"]))
                stress_config["between_attempts_sleep"] = float(data.get("between_attempts_sleep", stress_config["between_attempts_sleep"]))
                if isinstance(data.get("mobile_user_agents"), list) and data.get("mobile_user_agents"):
                    stress_config["user_agents"] = [str(x) for x in data["mobile_user_agents"] if str(x).strip()]
                if isinstance(data.get("probe_paths"), list) and data.get("probe_paths"):
                    stress_config["probe_paths"] = [str(x) for x in data["probe_paths"] if str(x).strip()]
        except: 
            pass

    
    # Работаем, пока не набрали 200 в подписку ИЛИ пока не кончились ссылки в unique_links
    while len(working_for_sub) < 200 and idx < len(unique_links):
        if checked_today >= MAX_TO_CHECK:
            print(f"🛑 Достигнут лимит проверок за запуск: {MAX_TO_CHECK}")
            break
        link = unique_links[idx]
        idx += 1 # Сдвигаем указатель

        # --- ДОБАВЛЯЕМ ТУТ ---
        if is_sni_suspicious(link):
            print(f"🚫 Скип (Bad SNI): {link.split('sni=')[-1][:20]}...")
            continue
        # ---------------------
        
        clean_link = link.strip()
        base_part = clean_link.split("#", 1)[0].strip()

        endpoint, host, port = extract_host_port(base_part)
        
        if base_part in seen_parts and not any(base_part in p for p in pinned_list):
            continue
        
        # --- БЛОК ЗАКРЕПОВ (PINNED) ---
        found_pinned_full = None
        for p in pinned_list:
            if base_part == p.split("#")[0].strip():
                found_pinned_full = p
                break

        if found_pinned_full:
            seen_parts.add(base_part)
        
            # 1. Достаём только флаг из старого имени
            #raw_pinned_name = found_pinned_full.split("#")[-1].strip()
            #original_label = urllib.parse.unquote(raw_pinned_name)
        
            #emoji_match = re.match(r'^([^\w\s\d]+)', original_label)
            #flag = emoji_match.group(1).strip() if emoji_match else ""
            final_linkk = found_pinned_full.strip()
            # 2. Полностью перезаписываем имя
            #new_name = f"{flag} 💎 [PINNED] {counter}"
        
            # 3. Чистим базу
            #clean_base = base_part.split("#")[0].strip()
        
            # 4. Собираем финальную ссылку
            #final_linkk = f"{clean_base}#{urllib.parse.quote(new_name)}"
        
            working_for_sub.append(final_linkk)
            #print(f"💎 [PINNED] {counter} с флагом '{flag}' готов")
            print(f"💎 [PINNED] {counter} добавлен без изменений")
            
            counter += 1
            continue
            
        # --- ФИЛЬТРЫ И ПРОВЕРКИ ---
        if base_part in blacklist:
            print(f"🚫 Пропуск: Сервер в черном списке ({host})")
            continue

        # if "type=ws" in base_part.lower() or "type=grpc" in base_part.lower():
        #     print(f"📡 Пропуск: Протокол WS/gRPC временно отключен ({host})")
        #     continue 

        if not re.search(r'[a-f0-9\-]{36}@', base_part):
            print(f"❓ Пропуск: Неверный формат UUID или ссылки ({host if host else 'unknown'})")
            continue
    
        endpoint, host, port = extract_host_port(base_part)
        if not endpoint or not host or not port:
            print(f"❌ Ошибка: Не удалось извлечь хост/порт из ссылки")
            continue

        # --- ЖЕСТКИЙ ФИЛЬТР IPv6 ---
        if is_ipv6(host):
            print(f"🚫 БАН (IPv6): {host}.")
            add_to_blacklist(base_part) # Отправляем в черный список сразу
            remove_from_input_file(base_part) # Чистим из 1.txt
            continue

        # --- ПРОВЕРКА СОЕДИНЕНИЯ (ОБНОВЛЕННЫЙ БЛОК) ---
        print(f"🔍 Тестирую: {host}:{port}...", end=" ", flush=True)

        # --- УМНАЯ ПРОВЕРКА L7 С ПЕРЕБОРОМ SNI ---
        candidates = extract_sni_candidates(link)
        is_alive = False
        final_used_sni = ""
        current_latency = 0 # Сюда сохраним пинг

        for cand_sni in candidates:
            if any(word in cand_sni.lower() for word in BAD_SNI_KEYWORDS):
                continue
            
            # Вызываем обновленную функцию
            latency = probe_vless_l7(link, cand_sni, timeout=int(stress_config.get("timeout", 5)))
            
            if latency > 0:
                is_alive = True
                current_latency = latency
                final_used_sni = cand_sni
                break 

        if not is_alive:
            # Последняя попытка с родным SNI
            native_sni = extract_sni(link)
            if native_sni:
                latency = probe_vless_l7(link, native_sni, timeout=int(stress_config.get("timeout", 5)))
                if latency > 0:
                    is_alive = True
                    current_latency = latency
                    final_used_sni = native_sni

        # Настраиваем переменные для совместимости с твоим кодом ниже
        checked_today += 1
        resolved_ip = host 
        success_hits = 1 if is_alive else 0
        total_hits = 1

        # Удаляем из входного файла, так как мы его уже "потрогали"
        remove_from_input_file(base_part)

        # 1. Сначала отсекаем мертвые IP, которые уже видели (твоя логика)
        if resolved_ip and resolved_ip in seen_ips and not is_alive:
            print("♻️ Пропуск: IP уже встречался и сейчас недоступен")
            continue

        # 2. Если сервер ЖИВ, проверяем лимит на количество (НОВЫЙ БЛОК)
        if is_alive and resolved_ip:
            # Увеличиваем счетчик для этого IP
            ip_counts[resolved_ip] = ip_counts.get(resolved_ip, 0) + 1
            
            # Если это уже 6-й живой сервер на одном IP — скипаем
            if ip_counts[resolved_ip] > 5:
                print(f"♻️ Пропуск: IP {resolved_ip} переполнен (лимит 5 конфигов)")
                continue 

            # Если лимит прошел — добавляем в список увиденных
            seen_ips.add(resolved_ip)

        # --- ЭТАП 2: ЕСЛИ СЕРВЕР РАБОТАЕТ ---
        if is_alive:
            print(f"✅ {host}:{port} — РАБОТАЕТ (через Go)")
            # Твоя логика сохранения (БЕЗ ИЗМЕНЕНИЙ СИСТЕМЫ ЗАКРЕПОВ)
            if "security=none" in base_part.lower():
                print(f"❌ НЕТ ШИФРОВАНИЯ: {host}")
                continue
    
            country = get_country_code(host, countries_cache)
            if country not in ALLOWED_COUNTRIES:
                print(f"🌍 МИМО: Страна {country} не в белом списке ({host})")
                continue
    
            working_for_base.append(base_part)
            # ip_str = f"[{resolved_ip}]" if is_ipv6(resolved_ip) else resolved_ip
            # sub_link = base_part.replace(endpoint, f"@{ip_str}:{port}", 1)
            sub_link = base_part
            
            if "sni=" not in sub_link.lower() and not is_ipv6(host):
                sep = "&" if "?" in sub_link else "?"
                sub_link += f"{sep}sni={host}"
            # Собираем красивое имя с пингом
            ping_label = f"{current_latency}ms"
            final_link = rebuild_link_name(sub_link, f"mob {counter} [{ping_label}]")
            working_for_sub.append(final_link)
            
            # Красивый лог в консоль
            print(f"✅ ОК {len(working_for_sub)}/200 ({country}): {host} -> {current_latency}ms (mob {counter})")
            counter += 1
    
        # --- ЭТАП 3: ЕСЛИ СЕРВЕР НЕ ОТВЕЧАЕТ ---
        else:
            print(f"💀 МЕРТВ: Не удалось подключиться или таймаут ({host})")
            # Чистим из активных списков, так как сейчас он не работает
            if base_part in ranking_db:
                del ranking_db[base_part]
            
            fail_time = history.get(base_part, now)
            
            if now - fail_time > 86400: 
                print(f"🗑️ УДАЛЕН И ЗАБЛОКИРОВАН (оффлайн > 24ч): {host}")
                # Пишем в блэклист, чтобы чекер больше его никогда не трогал
                with open('test1/blacklist.txt', 'a') as bl:
                    bl.write(base_part + "\n")
                # continue прерывает работу с этой ссылкой. 
                # Она НЕ попадет в working_for_base и working_for_sub -> ИСЧЕЗНЕТ из файлов.
                continue
    
            # 3. СЦЕНАРИЙ: "ШАНС" (Упал недавно, попадает в GRACE_PERIOD)
            if now - fail_time < GRACE_PERIOD:
                country = get_country_code(host, countries_cache)
                # Оставляем только если страна нам подходит
                # if country in ALLOWED_COUNTRIES:
                #     # Сохраняем в базу (1.txt), чтобы проверить в следующий раз
                #     # working_for_base.append(base_part)
                #     # Записываем в новую историю время падения (чтобы счетчик тикал дальше)
                #     new_history[base_part] = fail_time
                    
                #     # Добавляем в подписку с меткой ожидания
                #     # temp_link = rebuild_link_name(link, f"⏳ mob {counter}")
                #     # working_for_sub.append(temp_link)
                    
                #     print(f"⏳ DOWN ({country}): {host} (пошел нахуй)")
                #     counter += 1
            else:
                print(f"🗑️ Удален (тайм-аут): {host}")

        # --- ВСЕ, ЧТО НЕ УСПЕЛИ ПРОВЕРИТЬ (если набрали 200 раньше конца списка) ---
        new_deferred = unique_links[idx:] 
    # --- КОНЕЦ ЦИКЛА ПРОВЕРКИ ---
    # --- ЛОГИКА ОЧЕРЕДИ И ЛИМИТОВ (ИСПРАВЛЕНО) ---
        
     #   1. Разделяем то, что нашли, на две кучи
    all_pinned = [l for l in working_for_sub if "💎 [PINNED]" in l]
    all_others = [l for l in working_for_sub if "💎 [PINNED]" not in l]
    
    final_to_sub = []
    seen_in_final = set()# То самое "сито" для адресов
    
    # 2. Сначала берем закрепы (Приоритет №1)
    # Лимит 130 штук
    for l in all_pinned:
        if len(final_to_sub) >= 130: break
        base = l.split("#")[0].strip()
        if base not in seen_in_final:
            final_to_sub.append(l)
            seen_in_final.add(base)
    # 3. Добираем обычные сервера, пока не станет 200 (Приоритет №2)
    # Но только те, которых еще НЕТ в закрепах
    for l in all_others:
        if len(final_to_sub) >= 200: break
        base = l.split("#")[0].strip()
        if base not in seen_in_final: # ВОТ ОНА — ЗАЩИТА ОТ ДУБЛЯ
            final_to_sub.append(l)
            seen_in_final.add(base)
    
    # 4. Формируем deferred.txt (остатки)
    # Сюда идет то, что не влезло + то, что вообще не проверялось 
    leftover_from_others = [l for l in all_others if l.split("#")[0].strip() not in seen_in_final]
    deferred_final = new_deferred + leftover_from_others
    
    # --- ИТОГОВОЕ СОХРАНЕНИЕ (ЕДИНЫЙ БЛОК) ---

    # 1. Сохраняем подписку (wifi.txt) - то, что идет пользователю
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        # Используем .strip() для хедера и объединяем с финальным списком
        f.write(HEADER.strip() + "\n\n" + "\n".join(final_to_sub))

    # 2. Сохраняем рабочую базу (1.txt) - С ЗАЩИТОЙ
    # Собираем: те, кто выжил сегодня + те, кто в фаворитах + те, кто в закрепах
    # set() уберет дубликаты
    final_base_to_save = list(set(working_for_base) | fav_bases | set(clean_pinned.keys()))
    
    os.makedirs(os.path.dirname(INPUT_FILE), exist_ok=True)
    with open(INPUT_FILE, "w", encoding="utf-8") as f: 
        f.write("\n".join(final_base_to_save))

    # 3. Сохраняем очередь на будущее (deferred.txt)
    with open('test1/deferred.txt', "w", encoding="utf-8") as f:
        f.write("\n".join(deferred_final))
    
    # 4. Сохраняем системные файлы (история, рейтинги, кэш)
    with open(STATUS_FILE, "w") as f: 
        json.dump(new_history, f)
    with open('test1/ranking.json', "w") as f:
        json.dump(ranking_db, f)
    with open(CACHE_FILE, 'w') as f:
        json.dump(countries_cache, f)

    # Итоговые отчеты в консоль
    print(f"\n🏁 План выполнен!")
    print(f"✅ Всего в wifi.txt: {len(final_to_sub)} (из лимита 200)")
    print(f"💾 В базе 1.txt сохранено: {len(final_base_to_save)} серверов (включая защиту фаворитов)")
    print(f"⏳ В очереди deferred.txt: {len(deferred_final)}")

if __name__ == "__main__":
    main()
