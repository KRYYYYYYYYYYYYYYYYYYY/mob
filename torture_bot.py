import socket, time, os, ssl, re, json, subprocess
import psutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Настройки путей
RANK_FILE = 'test1/ranking.json'
VETTED_FILE = 'test1/vetted.txt'
THRESHOLD = 50 

# Блокировка для безопасной записи в файлы из потоков
file_lock = threading.Lock()

HOST_PORT_RE = re.compile(r'@(?P<host>[A-Za-z0-9.-]+):(?P<port>\d+)')

def extract_host_port(link: str) -> tuple[str | None, int | None]:
    m = HOST_PORT_RE.search(link)
    if not m: return None, None
    host, port_str = m.group("host"), m.group("port")
    if ":" in host or "[" in host or "]" in host: return None, None
    try:
        port = int(port_str)
        return (host, port) if 1 <= port <= 65535 else (None, None)
    except: return None, None

def build_tls_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def torture_check(link):
    host, port = extract_host_port(link)
    if not host or not port: return False
    is_tls = "security=tls" in link.lower() or "security=reality" in link.lower()
    sni = re.search(r"sni=([^&?#]+)", link)
    server_hostname = sni.group(1) if sni else host

    user_agents = [
        b"GET / HTTP/1.1\r\nHost: google.com\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: bing.com\r\nUser-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)\r\n\r\n"
    ]

    total_attempts = 20 
    for i in range(total_attempts):
        try:
            with socket.create_connection((host, port), timeout=7) as s:
                if is_tls:
                    ctx = build_tls_context()
                    with ctx.wrap_socket(s, server_hostname=server_hostname) as ssock:
                        if (i + 1) % 5 == 0:
                            payload = user_agents[i % len(user_agents)]
                            ssock.sendall(payload)
                            ssock.settimeout(3)
                            if not ssock.recv(5): raise Exception("No Data")
                else:
                    s.sendall(b'\x05\x01\x00')
                    s.settimeout(3)
                    if not s.recv(2): raise Exception("No Proxy Resp")
            
            if (i + 1) % 5 == 0:
                print(f"    ⛓️  Пытка: {i + 1}/{total_attempts} | {host[:15]} OK")
            
            if i < total_attempts - 1:
                time.sleep(60) 
        except Exception as e:
            print(f"❌ [ПРОВАЛ {host[:15]}] Попытка {i+1}: {e}")
            return False
    return True

def load_ranking():
    if not os.path.exists(RANK_FILE): return {}
    try:
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return {}

def load_vetted():
    if not os.path.exists(VETTED_FILE): return set()
    try:
        with open(VETTED_FILE, 'r', encoding='utf-8') as f:
            return {line.split('#')[0].strip() for line in f if 'vless://' in line}
    except: return set()

def main_torturer():
    # --- ЗАЩИТА ОТ ДУБЛИКАТОВ ПРОЦЕССА ---
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] != current_pid:
                cmd = proc.info.get('cmdline')
                if cmd and 'torture_bot.py' in ' '.join(cmd):
                    print(f"🛑 Близнец (PID {proc.info['pid']}) уже работает. Самоликвидация.")
                    return
        except (psutil.NoSuchProcess, psutil.AccessDenied): continue

    ranking_db = load_ranking()
    vetted_set = load_vetted() 
    
    print(f"📊 Всего в ranking.json: {len(ranking_db)} записей.")
    print(f"🛡️ Уже в vetted.txt: {len(vetted_set)} записей.")

    if not ranking_db:
        print("📭 Рейтинг пуст, пытать некого.")
        return

    # Отбираем кандидатов: Элита (>=THRESHOLD) и Подозрительные (<=0)
    candidates = []
    for base, data in ranking_db.items():
        rank = data.get("rank", 0) if isinstance(data, dict) else data
        link = data.get("link", base) if isinstance(data, dict) else base
        
        # Пытаем если высокий ранг ИЛИ если он уже 0 (проверка на удаление)
        if (rank >= THRESHOLD or rank <= 0) and base not in vetted_set:
            candidates.append((base, link))

    if not candidates:
        print(f"⌛ Нет подходящих кандидатов (нужен ранг >= {THRESHOLD} или <= 0). Завершаю работу.")
        return

    print(f"🔥 Инквизиция: Пытаем {len(candidates)} серверов в 5 потоков.")

    dead_to_remove = [] # Список на полное удаление из JSON

    def run_torture(item):
        base, full_link = item
        # Добавил принт начала процесса для каждого потока
        print(f"⛓️  Начинаю пытку {base[:25]}...")
        success = torture_check(full_link)
        return base, full_link, success

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(run_torture, candidates))

    for base, full_link, success in results:
        if success:
            vetted_entry = f"{full_link} # Rank: ELITE | {time.strftime('%Y-%m-%d')}"
            with file_lock:
                with open(VETTED_FILE, 'a', encoding='utf-8') as f:
                    f.write(vetted_entry + "\n")
            
            if isinstance(ranking_db.get(base), dict):
                ranking_db[base]['rank'] = 0 # Сбрасываем, так как он уже в Vetted
                ranking_db[base]['last_torture'] = "PASS"
            print(f"🎖️ {base[:25]}... ПРОШЕЛ ПЫТКИ!")
        else:
            if isinstance(ranking_db.get(base), dict):
                old_rank = ranking_db[base].get('rank', 0)
                # ЛОГИКА УДАЛЕНИЯ: Если он уже был 0 или меньше и снова упал - удаляем
                if old_rank <= 0:
                    dead_to_remove.append(base)
                    print(f"🧹 {base[:25]}... окончательно удален (стабильный 0).")
                else:
                    ranking_db[base]['rank'] = max(0, old_rank - 30)
                    ranking_db[base]['last_torture'] = "FAIL"
                    print(f"❌ {base[:25]}... СЛОМАЛСЯ (Штраф -30, текущий ранг: {ranking_db[base]['rank']}).")

    # Удаляем "мертвецов"
    if dead_to_remove:
        print(f"💀 Всего удалено из базы: {len(dead_to_remove)}")
        for dead_base in dead_to_remove:
            if dead_base in ranking_db:
                del ranking_db[dead_base]

    # Сохраняем итоги
    with open(RANK_FILE, 'w', encoding='utf-8') as f:
        json.dump(ranking_db, f, ensure_ascii=False, indent=4)
    print("💾 База данных сохранена. Инквизиция окончена.")

if __name__ == "__main__":
    main_torturer()
