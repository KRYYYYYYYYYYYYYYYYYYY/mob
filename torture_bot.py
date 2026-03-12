import socket, time, os, ssl, re, json, subprocess, requests
import psutil
from concurrent.futures import ThreadPoolExecutor
import threading

# --- КОНФИГУРАЦИЯ ---
ALLOWED_COUNTRIES = {"US", "DE", "NL", "GB", "FR", "FI", "SG", "JP", "PL", "TR", "RU"}
RANK_FILE = 'test1/ranking.json'
PINNED_FILE = 'test1/pinned.txt'
VETTED_FILE = 'test1/vetted.txt'
PROFILE_FILE = 'test1/stress_profile.json'
THRESHOLD = 50 

file_lock = threading.Lock()
HOST_PORT_RE = re.compile(r'@(?P<host>[A-Za-z0-9.-]+):(?P<port>\d+)')

# --- НОВАЯ ФУНКЦИЯ ЗАГРУЗКИ КОНФИГА ---
def load_stress_config():
    config = {"timeout": 2.5, "dpi_sleep": 0.1} # Дефолты
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, 'r') as f:
                data = json.load(f)
                config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
                config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
        except: pass
    return config

def get_country(host):
    try:
        resp = requests.get(f"http://ip-api.com/json/{host}?fields=status,countryCode", timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return data.get("countryCode", "??")
    except: pass
    return "??"

def extract_host_port(link: str):
    m = HOST_PORT_RE.search(link)
    if not m: return None, None
    host, port_str = m.group("host"), m.group("port")
    try:
        port = int(port_str)
        return (host, port) if 1 <= port <= 65535 else (None, None)
    except: return None, None

# --- ОБНОВЛЕННАЯ ПЫТКА ---
def torture_check(link, stress_config):
    host, port = extract_host_port(link)
    if not host or not port: return False
    is_tls = "security=tls" in link.lower() or "security=reality" in link.lower()
    sni = re.search(r"sni=([^&?#]+)", link)
    server_hostname = sni.group(1) if sni else host

    user_agents = [
        b"GET / HTTP/1.1\r\nHost: google.com\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: apple.com\r\nUser-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X)\r\n\r\n"
    ]

    total_attempts = 20 
    for i in range(total_attempts):
        try:
            # ИСПОЛЬЗУЕМ ЖЕСТКИЙ ТАЙМАУТ ИЗ ПРОФИЛЯ
            with socket.create_connection((host, port), timeout=stress_config["timeout"]) as s:
                if is_tls:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    with ctx.wrap_socket(s, server_hostname=server_hostname) as ssock:
                        # Каждую 5-ю попытку шлем реальный запрос
                        if (i + 1) % 5 == 0:
                            payload = user_agents[i % len(user_agents)]
                            ssock.sendall(payload)
                            
                            # Имитация задержки DPI
                            if stress_config["dpi_sleep"] > 0:
                                time.sleep(stress_config["dpi_sleep"])
                                
                            ssock.settimeout(2.0)
                            if not ssock.recv(5): raise Exception("Empty Resp")
                else:
                    s.sendall(b'\x05\x01\x00')
                    s.settimeout(2.0)
                    if not s.recv(2): raise Exception("Proxy Error")
            
            if (i + 1) % 5 == 0:
                print(f"    ⛓️  Пытка: {i + 1}/{total_attempts} | {host[:15]} OK")
            
            # Если это не последняя попытка — спим минуту
            if i < total_attempts - 1:
                time.sleep(60) 
        except Exception as e:
            print(f"❌ [ПРОВАЛ] Попытка {i+1}: {e}")
            return False
    return True

def main_torturer():
    # Проверка на дубликаты процесса
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = proc.info.get('cmdline')
            if proc.info['pid'] != current_pid and cmd and 'torture_bot.py' in ' '.join(cmd):
                print("🛑 Бот уже запущен.")
                return
        except: continue

    # Загружаем конфиг и данные
    stress_config = load_stress_config()
    print(f"⚙️ Пытки будут идти с таймаутом {stress_config['timeout']}s")
    
    ranking_db = {}
    if os.path.exists(RANK_FILE):
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            ranking_db = json.load(f)

    if not ranking_db: return

    # Загружаем списки исключений
    vetted_set = set()
    if os.path.exists(VETTED_FILE):
        with open(VETTED_FILE, 'r', encoding='utf-8') as f:
            vetted_set = {l.split('#')[0].strip() for l in f if 'vless://' in l}

    pinned_set = set()
    if os.path.exists(PINNED_FILE):
        with open(PINNED_FILE, 'r', encoding='utf-8') as f:
            pinned_set = {l.split('#')[0].strip() for l in f if 'vless://' in l}

    # Отбор кандидатов
    candidates = []
    for base, data in ranking_db.items():
        rank = data.get("rank", 0) if isinstance(data, dict) else data
        link = data.get("link", base) if isinstance(data, dict) else base
        
        if (rank >= THRESHOLD or rank <= 0) and base not in vetted_set and base not in pinned_set:
            candidates.append((base, link))

    if not candidates:
        print("⌛ Нет кандидатов для Инквизиции.")
        return

    print(f"🔥 Инквизиция: {len(candidates)} серверов.")

    def run_torture(item):
        base, full_link = item
        host, _ = extract_host_port(base)
        
        # Предварительная проверка ГЕО
        country = get_country(host)
        if country not in ALLOWED_COUNTRIES and country != "??":
            return base, full_link, False, "WRONG_GEO"
        
        # Запуск пытки с передачей конфига
        success = torture_check(full_link, stress_config)
        return base, full_link, success, "DONE"

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(run_torture, candidates))

    # Обработка результатов
    for base, full_link, success, status in results:
        if status == "WRONG_GEO":
            if base in ranking_db: del ranking_db[base]
            print(f"🌍 Удален (Гео: {base[:15]})")
            continue

        if success:
            # В элиту!
            vetted_entry = f"{full_link} # Rank: ELITE | {time.strftime('%Y-%m-%d')}"
            with file_lock:
                with open(VETTED_FILE, 'a', encoding='utf-8') as f:
                    f.write(vetted_entry + "\n")
            if base in ranking_db:
                ranking_db[base]['rank'] = 0 # Сбрасываем ранг, так как он уже в vetted
                ranking_db[base]['last_torture'] = "PASS"
        else:
            # Наказание
            if base in ranking_db:
                old_rank = ranking_db[base].get('rank', 0)
                if old_rank <= 0:
                    del ranking_db[base]
                    print(f"🧹 Удален навсегда: {base[:15]}")
                else:
                    ranking_db[base]['rank'] = max(0, old_rank - 30)
                    ranking_db[base]['last_torture'] = "FAIL"

    # Сохраняем обновленный рейтинг
    with open(RANK_FILE, 'w', encoding='utf-8') as f:
        json.dump(ranking_db, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main_torturer()
