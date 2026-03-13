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
    existing = set()
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r') as f:
            existing = {line.strip() for line in f}
    if base_part not in existing:
        with open(BLACKLIST_FILE, 'a') as f:
            f.write(base_part + "\n")

def remove_from_all(base_part):
    # Список файлов, из которых нужно вырезать сервер
    for path in [WIFI_FILE, DEFERRED_FILE]: 
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Оставляем только те строки, где НЕТ этого сервера
            new_lines = [l for l in lines if base_part not in l]
            
            if len(lines) != len(new_lines):
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                print(f" 🧹 [УДАЛЕНИЕ] Сервер вырезан из {path}")

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

def process_pin_commands(token, repo, vetted_list):
    if not token or not repo: return vetted_list
    try:
        # 1. Читаем Issue с командами
        cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'body', '--limit', '1']
        pin_read = subprocess.check_output(cmd, env={**os.environ, "GH_TOKEN": token}).decode()
        
        if not pin_read or pin_read == "[]": return vetted_list
        
        body = json.loads(pin_read)[0]['body']
        
        # Ищем [x] или [v] в любом месте строки, после чего идет vless
        # Регулярка теперь не привязана к началу строки и дефису
        to_pin = re.findall(r'\[[xX]\]\s*(vless://[^\s#`]+)', body)
        to_ban = re.findall(r'\[[vV]\]\s*(vless://[^\s#`]+)', body)

        print(f"DEBUG: Текст из Issue: {body[:150]}...") 
        print(f"DEBUG: Найдено для PIN: {len(to_pin)}")
        print(f"DEBUG: Найдено для BAN: {len(to_ban)}")

        affected_bases = set()

        # Обработка закрепов
        if to_pin:
            current_p = []
            if os.path.exists(PINNED_FILE):
                with open(PINNED_FILE, 'r', encoding='utf-8') as f:
                    current_p = [l.strip().split('#')[0] for l in f]
            
            with open(PINNED_FILE, 'a', encoding='utf-8') as pf:
                for link in to_pin:
                    base = link.split('#')[0].strip()
                    if base not in current_p:
                        pf.write(base + "\n")
                        affected_bases.add(base)
                        print(f"📌 [PIN] Закреплен: {base[:20]}")

        # Обработка банов (вторая галочка)
        if to_ban:
            for link in to_ban:
                base = link.split('#')[0].strip()
                add_to_blacklist(base) # Твоя функция добавления в чс
                remove_from_all(base)   # Твоя функция удаления из файлов
                affected_bases.add(base)
                print(f"🚫 [BAN] Забанен через Issue: {base[:20]}")

        # Если были изменения — чистим текущий список, чтобы не дублировать
        if affected_bases:
            new_vetted = [v for v in vetted_list if v.split('#')[0].strip() not in affected_bases]
            # Перезаписываем vetted.txt, чтобы изменения сразу применились
            with open(VETTED_FILE, 'w', encoding='utf-8') as vf:
                vf.write("\n".join(new_vetted) + ("\n" if new_vetted else ""))
            return new_vetted

    except Exception as e:
        print(f"⚠️ Ошибка Pin-контроля: {e}")
    
    return vetted_list

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
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    with ctx.wrap_socket(s, server_hostname=server_hostname) as ssock:
                        # Каждую попытку шлем запрос (в тортурере халявы нет)
                        ssock.sendall(payload)
                        
                        if stress_config["dpi_sleep"] > 0:
                            time.sleep(stress_config["dpi_sleep"])
                            
                        ssock.settimeout(2.0)
                        # Ждем хотя бы 1 байт ответа
                        if not ssock.recv(1): raise Exception("Silent Drop")
                else:
                    s.sendall(b'\x05\x01\x00')
                    if not s.recv(2): raise Exception("Proxy Dead")
            
            if (i + 1) % 5 == 0:
                print(f"    ⛓️  Пытка: {i + 1}/{total_attempts} | {host[:15]} OK")
            
            # Интервал между ударами
            if i < total_attempts - 1:
                time.sleep(60) 
        except Exception as e:
            print(f"❌ [ПРОВАЛ] {host[:15]} на шаге {i+1}: {e}")
            return False
    return True

def main_torturer():
    # 1. Проверка на дубликаты
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = proc.info.get('cmdline')
            if proc.info['pid'] != current_pid and cmd and 'torture_bot.py' in ' '.join(cmd):
                print("🛑 Бот уже запущен.")
                return
        except: continue

    stress_config = load_stress_config()
    print(f"⚙️ Пытки с таймаутом {stress_config['timeout']}s")

    # --- ВОТ СЮДА ВСТАВЛЯЕМ ГИТХАБ-КОНТРОЛЬ ---
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

        # Загружаем текущий vetted для синхронизации
    vetted_for_sync = []
    if os.path.exists(VETTED_FILE):
        with open(VETTED_FILE, 'r', encoding='utf-8') as f:
            vetted_for_sync = [l.strip() for l in f if 'vless' in l]

    # ВЫЗЫВАЕМ ТУТ И ПЕРЕДАЕМ СПИСОК, А НЕ ПУСТЫЕ СКОБКИ []
    vetted_for_sync = process_pin_commands(token, repo, vetted_for_sync)
    print("✅ Команды GitHub выполнены")

    if not os.path.exists(RANK_FILE): return

    # 2. Загрузка БД
    ranking_db = {}
    if os.path.exists(RANK_FILE):
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            ranking_db = json.load(f)

# Вместо резкого return используем проверку
    if not ranking_db: 
        print("⌛ База пуста. Пытки отменяются, но команды GitHub выполнены.")
        return

        # 3. Списки исключений
    def load_set(path):
        if not os.path.exists(path): return set()
        with open(path, 'r', encoding='utf-8') as f:
            return {l.split('#')[0].strip() for l in f if 'vless://' in l}

    vetted_set = load_set(VETTED_FILE)
    pinned_set = load_set(PINNED_FILE)

    # Проверка кандидатов
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

    # Внутренняя функция для потока
    def run_torture(item):
        base, full_link = item
        host, port = extract_host_port(base)
        try:
            resolved_ip = socket.gethostbyname(host)
        except socket.gaierror:
            return base, full_link, False, "DNS_ERROR"

        if "type=ws" in full_link.lower() or "type=grpc" in full_link.lower():
            return base, full_link, False, "WEAK_PROTOCOL"
        
        country = get_country(host)
        if country not in ALLOWED_COUNTRIES and country != "??":
            return base, full_link, False, "WRONG_GEO"
        
        success = torture_check(full_link, stress_config, resolved_ip)
        return base, full_link, success, "DONE"

    # --- ЗАПУСК ПОТОКОВ (Тут была ошибка) ---
    with ThreadPoolExecutor(max_workers=5) as executor: # 5 потоков достаточно для 20-минутных пыток
        results = list(executor.map(run_torture, candidates))

    # 5. Обработка результатов
    for base, full_link, success, status in results:
        if status in ["WRONG_GEO", "WEAK_PROTOCOL", "DNS_ERROR"]:
            if base in ranking_db: del ranking_db[base]
            print(f"🧹 Чистка: {status} ({base[:15]})")
            continue

        if success:
            vetted_entry = f"{full_link} # Rank: ELITE | {time.strftime('%Y-%m-%d')}"
            with file_lock:
                with open(VETTED_FILE, 'a', encoding='utf-8') as f:
                    f.write(vetted_entry + "\n")
            if base in ranking_db:
                ranking_db[base]['rank'] = 0
                ranking_db[base]['last_torture'] = "PASS"
            print(f"🏆 ЭЛИТА: {base[:15]}")
        else:
            if base in ranking_db:
                old_rank = ranking_db[base].get('rank', 0)
                if old_rank <= 0:
                    del ranking_db[base]
                    print(f"💀 СМЕРТЬ: {base[:15]}")
                else:
                    ranking_db[base]['rank'] = max(0, old_rank - 30)
                    ranking_db[base]['last_torture'] = "FAIL"
                    print(f"📉 ШТРАФ: {base[:15]} ({old_rank}->{ranking_db[base]['rank']})")

    # 6. Авто-удаление "гнилых" (кто давно на нуле и провалил пытку)
    rotten = [b for b, d in ranking_db.items() 
              if isinstance(d, dict) and d.get('rank', 0) <= 0 and d.get('last_torture') == "FAIL"]
    for b in rotten: del ranking_db[b]
    if rotten: print(f"♻️ Утилизировано гнилых: {len(rotten)}")

    with open(RANK_FILE, 'w', encoding='utf-8') as f:
        json.dump(ranking_db, f, ensure_ascii=False, indent=4)
        
if __name__ == "__main__":
    main_torturer()
