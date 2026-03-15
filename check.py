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

# Настройки путей
INPUT_FILE = 'test1/1.txt'
OUTPUT_FILE = 'kr/mob/wifi.txt'
STATUS_FILE = 'test1/status.json'
CACHE_FILE = 'test1/countries_cache.json' # Добавь эту константу для порядка
RANKING_FILE = 'test1/ranking.json'
VETTED_FILE = 'test1/vetted.txt'
PINNED_FILE = 'test1/pinned.txt'

EXTERNAL_SOURCE_URL = [
    "https://raw.githubusercontent.com/KRYYYYYYYYYYYYYYYYYYY/crazy_xray_checker/refs/heads/main/result/working.txt",
]

GRACE_PERIOD = 2 * 24 * 60 * 60 # 48 часов

HEADER = """# profile-title: 🏳️Мобильный инет🏳️
# remark: 🏳️Мобильный инет🏳️
# announce: Подписка для использования ТОЛЬКО на мобильном интернете в условиях БЕЛЫХ СПИСКОВ!
# profile-update-interval: 2
# subscription-userinfo: upload=0; download=0; expire=0
# shadowrocket-userinfo: upload=0; download=0; expire=0

"""

ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}

DEFAULT_MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; SM-A336B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]
DEFAULT_PROBE_PATHS = ["/", "/generate_204", "/favicon.ico"]


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
    for path in [INPUT_FILE, OUTPUT_FILE, VETTED_FILE, PINNED_FILE]:
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


def probe_server(host: str, port: int, base_part: str, stress_config: dict):
    """Проверка сервера с повторами и разными сценариями трафика (anti-DPI профиль)."""
    use_tls = "security=tls" in base_part.lower() or "security=reality" in base_part.lower()
    attempts = max(1, int(stress_config.get("probe_attempts", 4)))
    min_success = max(1, int(stress_config.get("min_success", 2)))
    user_agents = stress_config.get("user_agents") or DEFAULT_MOBILE_USER_AGENTS
    probe_paths = stress_config.get("probe_paths") or DEFAULT_PROBE_PATHS

    success = 0
    last_ip = None

    for attempt in range(attempts):
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            infos = []

        if not infos:
            time.sleep(0.2)
            continue

        ua = user_agents[attempt % len(user_agents)]
        path = probe_paths[attempt % len(probe_paths)]
        request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {ua}\r\nAccept: */*\r\nConnection: close\r\n\r\n"

        attempt_ok = False
        for info in infos:
            resolved_ip = info[4][0]
            last_ip = resolved_ip
            try:
                with socket.create_connection((resolved_ip, int(port)), timeout=stress_config["timeout"]) as sock:
                    if use_tls:
                        context = ssl.create_default_context()
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE
                        with context.wrap_socket(sock, server_hostname=host) as ssock:
                            ssock.sendall(request.encode())
                            if stress_config["dpi_sleep"] > 0:
                                time.sleep(stress_config["dpi_sleep"])
                            ssock.settimeout(stress_config.get("recv_timeout", 1.7))
                            head = ssock.recv(8)
                            if head:
                                attempt_ok = True
                                break
                    else:
                        sock.sendall(b'\x05\x01\x00')
                        sock.settimeout(stress_config.get("recv_timeout", 1.7))
                        if sock.recv(2):
                            attempt_ok = True
                            break
            except (socket.timeout, ConnectionResetError, ssl.SSLError, socket.error):
                continue

        if attempt_ok:
            success += 1
            if success >= min_success:
                return True, last_ip, success, attempts

        if attempt < attempts - 1:
            time.sleep(stress_config.get("between_attempts_sleep", 0.35))

    return False, last_ip, success, attempts
    
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
    # 1. Определяем IP. 
    # Если это домен — резолвим. Если IPv6 или IPv4 — оставляем как есть.
    ip = host
    if not is_ipv6(host):
        try:
            # Пытаемся резолвить только если это похоже на домен (нет двоеточий)
            # и это не чистый IPv4
            if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
                ip = socket.gethostbyname(host)
        except:
            ip = host

    # 2. Проверяем кэш (используем IP как ключ)
    if ip in cache:
        return cache[ip]

    # 3. Запрос к API
    try:
        # Пауза 0.5с — это хорошо, защищает от 429 Too Many Requests
        time.sleep(0.5) 
        
        # Для IPv6 в URL скобки не нужны, ip-api кушает их просто как строку
        clean_ip = ip.replace("[", "").replace("]", "")
        url = f"http://ip-api.com/json/{clean_ip}?fields=status,countryCode"
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "success":
                code = data.get("countryCode", "Unknown")
                cache[ip] = code 
                return code
    except Exception as e:
        # Печатаем ошибку только для отладки, если нужно
        # print(f"GeoIP Error: {e}")
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

    # --- БЛОК ЧТЕНИЯ КОМАНД ИЗ GITHUB ---
if token and repo:
    env_gh = {**os.environ, "GH_TOKEN": token}

    # 1. ЧЕРНЫЙ СПИСОК (LABEL: control) 💀
    try:
        cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'control', '--json', 'body', '--limit', '1']
        data = json.loads(subprocess.check_output(cmd, env=env_gh))
        if data:
            body = data[0]['body']
            # Мастер-кнопка: 💀 ПОДТВЕРДИТЬ_БАН
            if re.search(r'\[[xX]\]\s*💀\s*ПОДТВЕРДИТЬ_БАН', body):
                checked = re.findall(r'-\s*\[[xX]\]\s*\'(vless://[^\s\']+)\'', body)
                for link in checked:
                    base = link.split('#')[0].strip()
                    add_to_blacklist(base)
                    remove_from_all(base)
                    if base in ranking_db: del ranking_db[base]
                print(f"💀 [CONTROL] Забанено {len(checked)} серверов")
    except Exception as e: print(f"⚠️ Ошибка Control: {e}")

    # 2. ПИН/БАН (LABEL: pin_control) ✅
    try:
        cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'body', '--limit', '1']
        data = json.loads(subprocess.check_output(cmd, env=env_gh))
        if data:
            body = data[0]['body']
            # Мастер-кнопка: ✅ ПРИМЕНИТЬ_PIN_BAN
            if re.search(r'\[[xX]\]\s*✅\s*ПРИМЕНИТЬ_PIN_BAN', body):
                to_pin = re.findall(r'\[[xX]\]\s*PIN_(vless://[^\s#`]+)', body)
                to_ban = re.findall(r'\[[xX]\]\s*BAN_(vless://[^\s#`]+)', body)
                
                for s in to_pin:
                    base = s.split("#")[0].strip()
                    with open(PINNED_FILE, 'a', encoding='utf-8') as pf:
                        pf.write(base + "\n")
                
                for s in to_ban:
                    base = s.split("#")[0].strip()
                    add_to_blacklist(base)
                    remove_from_all(base)
                print(f"💎 [PIN_CONTROL] PIN: {len(to_pin)}, BAN: {len(to_ban)}")
    except Exception as e: print(f"⚠️ Ошибка Pin/Ban: {e}")

    # 3. РАЗЗАКРЕПЛЕНИЕ (LABEL: unpin_control) 🔓
    try:
        cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'unpin_control', '--json', 'body', '--limit', '1']
        data = json.loads(subprocess.check_output(cmd, env=env_gh))
        if data:
            body = data[0]['body']
            # Мастер-кнопка: 🔓 ПОДТВЕРДИТЬ_РАСПИН
            if re.search(r'\[[xX]\]\s*🔓\s*ПОДТВЕРДИТЬ_РАСПИН', body):
                to_unpin = re.findall(r'-\s*\[[xX]\]\s*\'(vless://[^\s\']+)\'', body)
                unpin_bases = [u.split("#")[0].strip() for u in to_unpin]
                pinned_list = [s for s in pinned_list if s.split("#")[0].strip() not in unpin_bases]
                with open(PINNED_FILE, 'w', encoding='utf-8') as pf:
                    pf.write("\n".join(pinned_list) + ("\n" if pinned_list else ""))
                print(f"🔓 [UNPIN] Раззакреплено: {len(to_unpin)}")
    except Exception as e: print(f"⚠️ Ошибка Unpin: {e}")

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

        if "type=ws" in base_part.lower() or "type=grpc" in base_part.lower():
            print(f"📡 Пропуск: Протокол WS/gRPC временно отключен ({host})")
            continue 

        if not re.search(r'[a-f0-9\-]{36}@', base_part):
            print(f"❓ Пропуск: Неверный формат UUID или ссылки ({host if host else 'unknown'})")
            continue
    
        endpoint, host, port = extract_host_port(base_part)
        if not endpoint or not host or not port:
            print(f"❌ Ошибка: Не удалось извлечь хост/порт из ссылки")
            continue

        # --- ЖЕСТКИЙ ФИЛЬТР IPv6 ---
        if is_ipv6(host):
            print(f"🚫 БАН (IPv6): {host} запрещен в мобильной сети.")
            add_to_blacklist(base_part) # Отправляем в черный список сразу
            remove_from_input_file(base_part) # Чистим из 1.txt
            continue

        # --- ПРОВЕРКА СОЕДИНЕНИЯ ---
        print(f"🔍 Тестирую: {host}...", end=" ", flush=True) # Печатаем без переноса строки

        checked_today += 1
        is_alive, resolved_ip, success_hits, total_hits = probe_server(host, int(port), base_part, stress_config)

        if resolved_ip and resolved_ip in seen_ips and not is_alive:
            print("♻️ Пропуск: IP уже встречался и сейчас недоступен")
            continue

        if is_alive and resolved_ip:
            seen_ips.add(resolved_ip)

        # --- ЭТАП 2: ЕСЛИ СЕРВЕР РАБОТАЕТ ---
        if is_alive:
            # Твоя логика сохранения (БЕЗ ИЗМЕНЕНИЙ СИСТЕМЫ ЗАКРЕПОВ)
            if "security=none" in base_part.lower():
                print(f"❌ НЕТ ШИФРОВАНИЯ: {host}")
                continue
    
            country = get_country_code(host, countries_cache)
            if country not in ALLOWED_COUNTRIES:
                print(f"🌍 МИМО: Страна {country} не в белом списке ({host})")
                continue

            remove_from_input_file(base_part)
    
            working_for_base.append(base_part)
            # ip_str = f"[{resolved_ip}]" if is_ipv6(resolved_ip) else resolved_ip
            # sub_link = base_part.replace(endpoint, f"@{ip_str}:{port}", 1)
            sub_link = base_part
            
            if "sni=" not in sub_link.lower() and not is_ipv6(host):
                sep = "&" if "?" in sub_link else "?"
                sub_link += f"{sep}sni={host}"
            
            final_link = rebuild_link_name(sub_link, f"mob {counter}")
            working_for_sub.append(final_link)
            
            print(f"✅ ОК {len(working_for_sub)}/200 ({country}): {host} -> {resolved_ip} [{success_hits}/{total_hits}] (mob {counter})")
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
                if country in ALLOWED_COUNTRIES:
                    # Сохраняем в базу (1.txt), чтобы проверить в следующий раз
                    working_for_base.append(base_part)
                    # Записываем в новую историю время падения (чтобы счетчик тикал дальше)
                    new_history[base_part] = fail_time
                    
                    # Добавляем в подписку с меткой ожидания
                    temp_link = rebuild_link_name(link, f"⏳ mob {counter}")
                    working_for_sub.append(temp_link)
                    
                    print(f"⏳ DOWN ({country}): {host} (оставлен шанс, mob {counter})")
                    counter += 1
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
    # Лимит 50 штук
    for l in all_pinned:
        if len(final_to_sub) >= 50: break
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
    
# 5. Сохраняем результат
    
    # Сначала сохраняем deferred.txt (очередь на потом)
    with open('test1/deferred.txt', "w", encoding="utf-8") as f:
        f.write("\n".join(deferred_final))
    
    # ФОРМИРУЕМ ПРАВИЛЬНЫЙ ТЕКСТ ДЛЯ ПОДПИСКИ
    # .strip() убирает случайные пробелы в начале/конце хедера
    # \n\n гарантирует, что между командами и ссылками будет пустая строка (важно для iPhone)
    final_content = HEADER.strip() + "\n\n" + "\n".join(final_to_sub)

    # ЗАПИСЫВАЕМ В ОСНОВНОЙ ФАЙЛ (kr/mob/wifi.txt)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(final_content)
        
    # Сохраняем рабочую базу ссылок для следующего запуска чекера
    os.makedirs(os.path.dirname(INPUT_FILE), exist_ok=True)
    with open(INPUT_FILE, "w", encoding="utf-8") as f: 
        f.write("\n".join(working_for_base))
    
    # Сохраняем историю и рейтинги
    with open(STATUS_FILE, "w") as f: 
        json.dump(new_history, f)
    with open('test1/ranking.json', "w") as f:
        json.dump(ranking_db, f)

    print(f"🏁 План выполнен: {len(final_to_sub)} в подписке. Остаток в базе: {len(deferred_final)}")
    # Базовые части закрепов
    pinned_bases = {p.split("#")[0].strip() for p in pinned_list}
    
    # Сколько закрепов реально попало в подписку
    count_pinned = sum(
        1 for l in final_to_sub
        if l.split("#")[0].strip() in pinned_bases
    )
    
    print(f"💎 Закрепленных в подписке: {count_pinned} (из лимита 50)")
    print(f"✅ Всего в wifi.txt: {len(final_to_sub)} (из лимита 200)")
    
    # 3. Сохранение (ТВОЙ БЛОК БЕЗ ИЗМЕНЕНИЙ НАДПИСЕЙ)
    os.makedirs(os.path.dirname(INPUT_FILE), exist_ok=True)
    with open(INPUT_FILE, "w", encoding="utf-8") as f: 
        f.write("\n".join(working_for_base))
    
    with open(STATUS_FILE, "w") as f: 
        json.dump(new_history, f)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        # ЗАМЕНИ ТУТ working_for_sub на final_to_sub
        f.write(HEADER + "\n".join(final_to_sub))

    with open(CACHE_FILE, 'w') as f:
        json.dump(countries_cache, f)

    print(f"🏁 Готово! Подписка обновлена.")
    # --- ОБНОВЛЕНИЕ ИНТЕРФЕЙСА С ГАЛОЧКАМИ ---
    if token and repo:
        # --- БЛОК ОБНОВЛЕНИЯ ПАНЕЛЕЙ (ФИНАЛ) ---
        try:
            update_time = time.strftime("%d.%m.%Y %H:%M:%S")
            
            # ПАНЕЛЬ 1: BLACKLIST (control)
            body_ctrl = f"### 🎮 Панель Blacklist\n🕒 `{update_time}`\n\n"
            body_ctrl += "- [ ] 💀 **ПОДТВЕРДИТЬ_БАН** (Нажми для запуска)\n\n---\n"
            for link in working_for_base[:50]:
                body_ctrl += f"- [ ] '{link}'\n"
            update_issue(repo, 'control', body_ctrl, env_gh)
        
            # ПАНЕЛЬ 2: КАНДИДАТЫ (pin_control)
            body_pin = f"### 💎 Кандидаты в Элиту\n🕒 `{update_time}`\n\n"
            body_pin += "- [ ] ✅ **ПРИМЕНИТЬ_PIN_BAN** (Нажми для запуска)\n\n---\n"
            if not vetted_list:
                body_pin += "_Пока кандидатов нет..._"
            else:
                for link in [v.split('#')[0].strip() for v in vetted_list]:
                    body_pin += f"📡 Элита:\n- [ ] PIN_{link}\n- [ ] BAN_{link}\n\n---\n"
            update_issue(repo, 'pin_control', body_pin, env_gh)
        
            # ПАНЕЛЬ 3: ЗАКРЕПЫ (unpin_control)
            body_unp = f"### 👑 Управление Закрепами\n🕒 `{update_time}`\n\n"
            body_unp += "- [ ] 🔓 **ПОДТВЕРДИТЬ_РАСПИН** (Нажми для запуска)\n\n---\n"
            for link in pinned_list:
                body_unp += f"- [ ] '{link}'\n"
            update_issue(repo, 'unpin_control', body_unp, env_gh)
        
        except Exception as e:
            print(f"⚠️ Ошибка отрисовки панелей: {e}")

if __name__ == "__main__":
    main()
