import socket, time, os, ssl, re, json, subprocess, requests
import psutil
from concurrent.futures import ThreadPoolExecutor
import threading

# --- КОНФИГУРАЦИЯ ---
ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}
RANK_FILE = 'test1/ranking.json'
PINNED_FILE = 'test1/pinned.txt'
VETTED_FILE = 'test1/vetted.txt'
BLACKLIST_FILE = 'test1/blacklist.txt'
WIFI_FILE = 'kr/mob/wifi.txt'
DEFERRED_FILE = 'test1/deferred.txt'
PROFILE_FILE = 'test1/stress_profile.json'
THRESHOLD = 50 

file_lock = threading.Lock()
HOST_PORT_RE = re.compile(r'@(?P<host>[A-Za-z0-9.-]+):(?P<port>\d+)')

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

def refresh_control_panel(token, repo):
    """
    Полностью пересоздает тело Issue на основе актуального vetted.txt
    """
    if not token or not repo: return
    
    try:
        # 1. Считываем свежий список проверенных
        vetted_links = []
        if os.path.exists(VETTED_FILE):
            with open(VETTED_FILE, 'r', encoding='utf-8') as f:
                vetted_links = [l.split('#')[0].strip() for l in f if 'vless://' in l]

        # 2. Формируем новое тело
        update_time = time.strftime('%d.%m.%Y %H:%M:%S')
        new_body = f"### 💎 Кандидаты в закреп и бан\n🕒 Обновлено: `{update_time}`\n\n"
        
        if not vetted_links:
            new_body += "_Пока элитных кандидатов нет. Все обработаны или список пуст._"
        else:
            for i, link in enumerate(vetted_links, 1):
                new_body += f"📡 **Элита {i}:**\n"
                new_body += f"- [ ] PIN_{link}\n"
                new_body += f"- [ ] BAN_{link}\n\n---\n\n"

        # 3. Находим номер Issue и обновляем его
        cmd_find = ['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'number', '--limit', '1']
        issue_data = json.loads(subprocess.check_output(cmd_find, env={**os.environ, "GH_TOKEN": token}))
        
        if issue_data:
            num = str(issue_data[0]['number'])
            with open("new_panel.txt", "w", encoding="utf-8") as f:
                f.write(new_body)
            
            subprocess.run(['gh', 'issue', 'edit', num, '--repo', repo, '--body-file', 'new_panel.txt'],
                           env={**os.environ, "GH_TOKEN": token})
            print(f"♻️ Панель управления синхронизирована с vetted.txt (осталось: {len(vetted_links)})")

    except Exception as e:
        print(f"⚠️ Ошибка обновления панели: {e}")

# --- ХИРУРГИЧЕСКОЕ УДАЛЕНИЕ ---
def remove_from_all(base_part):
    for path in [WIFI_FILE, DEFERRED_FILE]: 
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
    config = {"timeout": 2.5, "dpi_sleep": 0.5} # Дефолты
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, 'r') as f:
                data = json.load(f)
                config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
                config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
        except: pass
    return config

def process_pin_commands(token, repo, vetted_list, ranking_db):
    """
    Считывает команды из Issue и переносит серверы между списками, 
    очищая ranking_db от обработанных элементов.
    """
    if not token or not repo: return vetted_list
    
    try:
        # 1. Получаем тело Issue через GitHub CLI
        cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'body', '--limit', '1']
        pin_read = subprocess.check_output(cmd, env={**os.environ, "GH_TOKEN": token}).decode()
        
        if not pin_read or pin_read == "[]": return vetted_list
        body = json.loads(pin_read)[0]['body']
        
        # ОБНОВЛЕННЫЕ РЕГУЛЯРКИ ПОД НОВУЮ ПАНЕЛЬ
        to_pin = re.findall(r'\[[xX]\]\s*PIN_(vless://[^\s#`]+)', body)
        to_ban = re.findall(r'\[[xX]\]\s*BAN_(vless://[^\s#`]+)', body)

        if not to_pin and not to_ban: return vetted_list

        print(f"🕵️ Pin-Control: Найдено {len(to_pin)} PIN и {len(to_ban)} BAN (через клики [x])")
        affected_bases = set()

        # --- ОБРАБОТКА PIN (В закрепы) ---
        if to_pin:
            current_p = set()
            if os.path.exists(PINNED_FILE):
                with open(PINNED_FILE, 'r', encoding='utf-8') as f:
                    current_p = {l.strip().split('#')[0] for l in f if l.strip()}
            
            with open(PINNED_FILE, 'a', encoding='utf-8') as pf:
                for link in to_pin:
                    base = link.split('#')[0].strip()
                    if base not in current_p:
                        pf.write(base + "\n")
                        current_p.add(base) # Чтобы не записать один и тот же дважды за один проход
                        affected_bases.add(base)
                        print(f"📌 [PIN] Перенесен: {base[:25]}...")

        # --- ОБРАБОТКА BAN (В черный список) ---
        if to_ban:
            for link in to_ban:
                base = link.split('#')[0].strip()
                add_to_blacklist(base)
                remove_from_all(base)
                affected_bases.add(base)
                print(f"🚫 [BAN] Забанен: {base[:25]}...")

        # --- СИНХРОНИЗАЦИЯ (Очистка памяти и базы) ---
        if affected_bases:
            # 1. Удаляем из текущего списка vetted (в памяти)
            vetted_list = [v for v in vetted_list if v.split('#')[0].strip() not in affected_bases]
            
            # 2. Удаляем из базы рейтинга (чтобы монитор их не трогал)
            for base in affected_bases:
                if base in ranking_db:
                    del ranking_db[base]
            
            # 3. Сразу сохраняем чистый vetted.txt
            with open(VETTED_FILE, 'w', encoding='utf-8') as vf:
                if vetted_list:
                    vf.write("\n".join(vetted_list) + "\n")
                else:
                    vf.write("") # Если список стал пустым

    except Exception as e:
        print(f"⚠️ Ошибка в process_pin_commands: {e}")
    
    return vetted_list

def get_country(host):
    if not os.path.exists(COUNTRY_CACHE_FILE):
        cache = {}
    else:
        try:
            with open(COUNTRY_CACHE_FILE, 'r') as f: cache = json.load(f)
        except: cache = {}

    if host in cache: return cache[host]

    try:
        resp = requests.get(f"http://ip-api.com/json/{host}?fields=status,countryCode", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                code = data.get("countryCode", "??")
                cache[host] = code
                with open(COUNTRY_CACHE_FILE, 'w') as f: json.dump(cache, f)
                return code
    except: pass
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
        except: pass
    return None, None

# --- ОБНОВЛЕННАЯ ПЫТКА ---
def torture_check(link, stress_config, resolved_ip):
    host, port = extract_host_port(link)
    if not host or not port: return False
    is_tls = "security=tls" in link.lower() or "security=reality" in link.lower()
    
    sni = re.search(r"sni=([^&?#]+)", link)
    server_hostname = sni.group(1) if sni else host

    # Юзер-агенты для имитации реального трафика
    payload = b"GET / HTTP/1.1\r\nHost: " + server_hostname.encode() + b"\r\nUser-Agent: Mozilla/5.0\r\n\r\n"

    total_attempts = 20 
    for i in range(total_attempts):
        try:
            # Коннектимся строго по IP
            with socket.create_connection((resolved_ip, port), timeout=stress_config["timeout"]) as s:
                if is_tls:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                    with ctx.wrap_socket(s, server_hostname=server_hostname) as ssock:
                        # Каждую попытку шлем запрос (в тортурере халявы нет)
                        ssock.sendall(payload)
                        if stress_config["dpi_sleep"] > 0: time.sleep(stress_config["dpi_sleep"])
                        ssock.settimeout(2.0)
                        if not ssock.recv(1): raise Exception("Drop")
                else:
                    s.sendall(b'\x05\x01\x00')
                    if not s.recv(2): raise Exception("Dead")
            
            if (i + 1) % 5 == 0: print(f"    ⛓️  Пытка {host[:15]}: {i+1}/20 OK")
            if i < 19: time.sleep(60)
        except:
            return False
    return True

def main_torturer():
    # Проверка на дубликаты процесса
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            if proc.info['pid'] != os.getpid() and 'torture_bot.py' in ' '.join(proc.info['cmdline']):
                print("🛑 Бот уже запущен."); return
        except: continue

    stress_config = load_stress_config()
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

    # Загрузка базы
    ranking_db = {}
    if os.path.exists(RANK_FILE):
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            ranking_db = json.load(f)

# GitHub Контроль
    vetted_list = []
    if os.path.exists(VETTED_FILE):
        with open(VETTED_FILE, 'r', encoding='utf-8') as f:
            vetted_list = [l.strip() for l in f if 'vless' in l]

    # ВЫЗЫВАЕМ ТУТ И ПЕРЕДАЕМ СПИСОК, А НЕ ПУСТЫЕ СКОБКИ []
    vetted_list = process_pin_commands(os.getenv("GH_TOKEN"), os.getenv("GITHUB_REPOSITORY"), vetted_list, ranking_db)

    if not ranking_db:
        print("⌛ База пуста."); return

    # 2. Загрузка БД
    ranking_db = {}
    if os.path.exists(RANK_FILE):
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            ranking_db = json.load(f)

# Вместо резкого return используем проверку
    if not ranking_db: 
        print("⌛ База пуста. Пытки отменяются, но команды GitHub выполнены.")
        return

    pinned_set = {l.split('#')[0].strip() for l in open(PINNED_FILE, 'r') if 'vless' in l} if os.path.exists(PINNED_FILE) else set()
    vetted_set = {l.split('#')[0].strip() for l in vetted_list}

    # Проверка кандидатов
    candidates = []
    for base, data in ranking_db.items():
        rank = data.get("rank", 0) if isinstance(data, dict) else data
        link = data.get("link", base) if isinstance(data, dict) else base
        if (rank >= THRESHOLD or rank <= 0) and base not in vetted_set and base not in pinned_set:
            candidates.append((base, link))

    if candidates:
        def run_torture(item):
            base, full_link = item
            host, port = extract_host_port(base)
            try:
                resolved_ip = socket.gethostbyname(host)
                if get_country(resolved_ip) not in ALLOWED_COUNTRIES: return base, full_link, False, "GEO"
                return base, full_link, torture_check(full_link, stress_config, resolved_ip), "OK"
            except: return base, full_link, False, "ERROR"

        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(run_torture, candidates))

        for base, full_link, success, status in results:
            if success:
                with file_lock:
                    with open(VETTED_FILE, 'a', encoding='utf-8') as f:
                        f.write(f"{full_link} # Rank: ELITE | {time.strftime('%Y-%m-%d')}\n")
                if base in ranking_db: del ranking_db[base]
                print(f"🏆 ЭЛИТА: {base[:15]}")
            else:
                if base in ranking_db and status == "OK":
                    ranking_db[base]['rank'] = max(0, ranking_db[base].get('rank', 50) - 30)
                    ranking_db[base]['last_torture'] = "FAIL"

        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)
    else:
        print("⌛ Нет новых кандидатов для пыток.")

    # 5. ФИНАЛЬНЫЙ ШАГ: Всегда обновляем панель в конце
    if token and repo:
        refresh_control_panel(token, repo)
    else:
        print("⚠️ Пропуск обновления панели: нет токена или репозитория в ENV.")

if __name__ == "__main__":
    main_torturer()
