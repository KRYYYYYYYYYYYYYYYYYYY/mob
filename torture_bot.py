import socket
import time
import os
import ssl
import re
import json
import subprocess
import requests
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
INPUT_FILE = 'test1/1.txt'
FAVORITES_FILE = 'test1/favorites.txt'
PROFILE_FILE = 'test1/stress_profile.json'
COUNTRY_CACHE_FILE = 'test1/countries_cache.json'
THRESHOLD = 50

DEFAULT_MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; SM-A336B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]
DEFAULT_PROBE_PATHS = ["/", "/generate_204", "/favicon.ico"]

file_lock = threading.Lock()

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

def commit_and_push():
    """Отправляет все измененные файлы обратно в репозиторий."""
    try:
        # Настройка пользователя (нужна для коммита)
        subprocess.run(['git', 'config', 'user.name', 'github-actions[bot]'], check=True)
        subprocess.run(['git', 'config', 'user.email', 'github-actions[bot]@users.noreply.github.com'], check=True)
        
        # Добавляем все измененные файлы
        subprocess.run(['git', 'add', WIFI_FILE, BLACKLIST_FILE, VETTED_FILE, RANK_FILE, PINNED_FILE, FAVORITES_FILE], check=True)
        
        # Проверяем, есть ли что коммитить
        status = subprocess.run(['git', 'diff', '--cached', '--quiet'])
        if status.returncode != 0:
            subprocess.run(['git', 'commit', '-m', '🤖 Автоматическое обновление списков и бан-листа'], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("✅ Все изменения успешно запушены в репозиторий!")
        else:
            print("yml Новых изменений для коммита не найдено.")
            
    except Exception as e:
        print(f"⚠️ Ошибка при выполнении Git Push: {e}")

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

def update_issue_from_file(repo, label, file_path, env):
    try:
        cmd_get = ['gh', 'issue', 'list', '--repo', repo, '--label', label, '--json', 'number']
        data = json.loads(subprocess.check_output(cmd_get, env=env))
        if data:
            num = str(data[0]['number'])
            subprocess.run([
                'gh', 'issue', 'edit', num, 
                '--repo', repo, 
                '--body-file', file_path
            ], env=env, check=True)
    except Exception as e:
        print(f"⚠️ Ошибка загрузки {file_path} в GitHub: {e}")


def refresh_all_panels(token, repo, ranking_db, vetted_list, pinned_list):
    update_time = time.strftime("%d.%m.%Y %H:%M:%S")
    env_gh = {**os.environ, "GH_TOKEN": token}

    # --- 0. ПРЕДВАРИТЕЛЬНАЯ ЗАГРУЗКА ИЗБРАННОГО ---
    # Это решает проблему UnboundLocalError
    fav_list = []
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            fav_list = [l.strip() for l in f if 'vless' in l]
    
    # --- 1. ПАНЕЛЬ ЧЕРНОГО СПИСКА ---
    body_ctrl = f"### 🎮 Панель Blacklist (Весь wifi.txt)\n🕒 `{update_time}`\n\n"
    body_ctrl += "- [ ] 💀 **ПОДТВЕРДИТЬ_БАН**\n\n---\n\n"
    # Теперь передаем оба списка, чтобы не предлагать забанить то, что в избранном или закрепе
    wifi_to_ban = get_wifi_candidates(pinned_list, fav_list)
    if wifi_to_ban:
        for full_link in wifi_to_ban:
            body_ctrl += f"- [ ] {full_link.strip()}\n"
    else:
        body_ctrl += "_Список пуст_\n"

    # СОХРАНЯЕМ В ТОТ САМЫЙ ФАЙЛ
    with open('test1/issue_body.txt', 'w', encoding='utf-8') as f:
        f.write(body_ctrl)
    
    # Обновляем Issue в GitHub, используя этот файл
    update_issue_from_file(repo, 'control', 'test1/issue_body.txt', env_gh)

   # --- 2. ПАНЕЛЬ КАНДИДАТОВ ---
    body_pin = f"### 💎 Кандидаты в Элиту\n🕒 `{update_time}`\n\n"
    body_pin += "- [ ] ✅ **ПРИМЕНИТЬ_PIN_BAN**\n\n---\n\n"
    for full_link in vetted_list:
        # УБИРАЕМ split('#')[0]. Нам нужна ПОЛНАЯ ссылка в чекбоксе.
        body_pin += f"📡 {full_link}\n"
        body_pin += f"- [ ] PIN: {full_link.strip()}\n" 
        body_pin += f"- [ ] BAN: {full_link.strip()}\n"
        body_pin += "\n---\n"
    update_issue(repo, 'pin_control', body_pin, env_gh)

    # --- 3. ПАНЕЛЬ ЗАКРЕПОВ ---
    body_unp = f"### 👑 Управление Закрепами\n🕒 `{update_time}`\n\n"
    body_unp += "- [ ] 🔓 **ПОДТВЕРДИТЬ_РАСПИН**\n\n---\n\n"
    for full_link in pinned_list:
        # УБРАЛИ КАВЫЧКИ
        body_unp += f"- [ ] {full_link.strip()}\n"
    update_issue(repo, 'unpin_control', body_unp, env_gh)

    # --- 4. ПАНЕЛЬ ИЗБРАННОЕ (Возможные) ---
    # Переменные fav_list и fav_bases уже должны быть загружены в начале функции
    
    body_fav = f"### ⭐ Избранные серверы (Возможные)\n🕒 `{update_time}`\n\n"
    body_fav += "- [ ] 🏆 **ПОДТВЕРДИТЬ_ИЗБРАННОЕ**\n\n---\n\n"
    
    # Используем нашу исправленную функцию, которая исключает закрепы и текущее избранное
    all_candidates = get_wifi_candidates(pinned_list, fav_list) 
    
    if all_candidates:
        for link in all_candidates:
            link_clean = link.strip()
            # Нам нужно проверить базу ссылки, чтобы понять, стоит ли уже крестик
            current_base = link_clean.split('#')[0].strip()
            
            # Если сервер по какой-то причине уже в fav_bases, ставим [x], иначе [ ]
            mark = "[x]" if current_base in fav_bases else "[ ]"
            body_fav += f"- {mark} {link_clean}\n"
    else:
        body_fav += "_Нет новых кандидатов для избранного_\n"
    
    update_issue(repo, 'fav_control', body_fav, env_gh)

# --- ХИРУРГИЧЕСКОЕ УДАЛЕНИЕ ---
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
        "timeout": 2.5,
        "dpi_sleep": 0.5,
        "recv_timeout": 1.7,
        "between_attempts_sleep": 0.35,
        "probe_attempts": 4,
        "min_success": 2,
        "torture_total_attempts": 20,
        "torture_min_success": 20,
        "torture_cycle_sleep": 60,
        "user_agents": list(DEFAULT_MOBILE_USER_AGENTS),
        "probe_paths": list(DEFAULT_PROBE_PATHS),
    }
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            config["timeout"] = data.get("max_handshake_ms", 2500) / 1000
            config["dpi_sleep"] = 0.5 if data.get("mimic_dpi_delay") else 0
            config["recv_timeout"] = float(data.get("recv_timeout", config["recv_timeout"]))
            config["between_attempts_sleep"] = float(data.get("between_attempts_sleep", config["between_attempts_sleep"]))
            config["probe_attempts"] = int(data.get("probe_attempts", config["probe_attempts"]))
            config["min_success"] = int(data.get("min_success", config["min_success"]))
            config["torture_total_attempts"] = int(data.get("torture_total_attempts", config["torture_total_attempts"]))
            config["torture_min_success"] = int(data.get("torture_min_success", config["torture_min_success"]))
            config["torture_cycle_sleep"] = int(data.get("torture_cycle_sleep", config["torture_cycle_sleep"]))
            if isinstance(data.get("mobile_user_agents"), list) and data.get("mobile_user_agents"):
                config["user_agents"] = [str(x) for x in data["mobile_user_agents"] if str(x).strip()]
            if isinstance(data.get("probe_paths"), list) and data.get("probe_paths"):
                config["probe_paths"] = [str(x) for x in data["probe_paths"] if str(x).strip()]
        except Exception:
            pass
    return config

def process_all_controls(token, repo, vetted_list, pinned_list, ranking_db):
    executed_any = False
    env_gh = {**os.environ, "GH_TOKEN": token}

    # ИСПРАВЛЕНО: теперь берем всё до конца строки [^\n\r], а не до пробела
    def find_checked_vless(text):
        found = re.findall(r'\[[xX]\]\s+(vless://[^\n\r`\'"]+)', text)
        return [l.strip().rstrip(':') for l in found]

    try:
        # --- 1. ПАНЕЛЬ ЧЕРНОГО СПИСКА (Label: control) ---
        out = subprocess.check_output(['gh', 'issue', 'list', '--repo', repo, '--label', 'control', '--json', 'body'], env=env_gh)
        data = json.loads(out)
        if data:
            body = data[0]['body']
            if "ПОДТВЕРДИТЬ_БАН" in body and "[x]" in body:
                links = find_checked_vless(body)
                for base_full in links:
                    base = base_full.split('#')[0].strip()
                    add_to_blacklist(base)
                    remove_from_all(base)
                    if base in ranking_db: del ranking_db[base]
                    executed_any = True

        # --- 2. PIN/BAN КАНДИДАТОВ (Label: pin_control) ---
        out = subprocess.check_output(['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'body'], env=env_gh)
        data = json.loads(out)
        if data:
            body = data[0]['body']
            if "ПРИМЕНИТЬ_PIN_BAN" in body and "[x]" in body:
                # ИСПРАВЛЕНО: Забираем всю строку целиком до конца
                to_pin = re.findall(r'\[[xX]\]\s+PIN:\s+(vless://[^\n\r`\'"]+)', body)
                to_ban = re.findall(r'\[[xX]\]\s+BAN:\s+(vless://[^\n\r`\'"]+)', body)
                
                for s in to_pin:
                    base_full = s.strip().rstrip(':')
                    base = base_full.split('#')[0].strip()
                    if all(base != p.split("#")[0].strip() for p in pinned_list):
                        with open(PINNED_FILE, 'a', encoding='utf-8') as pf:
                            pf.write(base_full + "\n")
                        pinned_list.append(base_full)
                    # Чистим из элиты после пина
                    vetted_list = [v for v in vetted_list if v.split('#')[0].strip() != base]
                    executed_any = True

                for s in to_ban:
                    base_full = s.strip().rstrip(':')
                    base = base_full.split('#')[0].strip()
                    add_to_blacklist(base)
                    remove_from_all(base)
                    vetted_list = [v for v in vetted_list if v.split('#')[0].strip() != base]
                    executed_any = True

        # --- 3. РАЗЗАКРЕПЛЕНИЕ (Label: unpin_control) ---
        out = subprocess.check_output(['gh', 'issue', 'list', '--repo', repo, '--label', 'unpin_control', '--json', 'body'], env=env_gh)
        data = json.loads(out)
        if data:
            body = data[0]['body']
            if "ПОДТВЕРДИТЬ_РАСПИН" in body and "[x]" in body:
                links = find_checked_vless(body)
                if links:
                    to_unpin_bases = {l.split('#')[0].strip() for l in links}
                    pinned_list = [s for s in pinned_list if s.split("#")[0].strip() not in to_unpin_bases]
                    with open(PINNED_FILE, 'w', encoding='utf-8') as pf:
                        pf.write("\n".join(pinned_list) + ("\n" if pinned_list else ""))
                    executed_any = True

        # --- 4. УПРАВЛЕНИЕ ИЗБРАННЫМ (Label: fav_control) ---
        out = subprocess.check_output(['gh', 'issue', 'list', '--repo', repo, '--label', 'fav_control', '--json', 'body'], env=env_gh)
        data = json.loads(out)
        if data:
            body = data[0]['body']
            # Проверяем кнопку подтверждения (любой регистр X)
            if "ПОДТВЕРДИТЬ_ИЗБРАННОЕ" in body and "[x]" in body.lower():
                new_fav_list = []
                
                for line in body.splitlines():
                    # Ищем строку с vless и чекбоксом через регулярку
                    match = re.search(r'- \[[xX ]\]\s+(vless://[^\n\r]+)', line)
                    if match:
                        full_link = match.group(1).strip()
                        is_checked = '- [x]' in line.lower()
                        
                        # Разбираем ссылку на адрес и имя
                        if '#' in full_link:
                            base_part, name_part = full_link.split('#', 1)
                        else:
                            base_part = full_link
                            name_part = "Избранное"
                        
                        # Чистим имя от старых звезд и пробелов
                        name_part = name_part.replace('⭐', '').strip()
                        
                        if is_checked:
                            # Лепим звезду только тем, кто отмечен
                            new_link = f"{base_part}#⭐ {name_part}"
                            new_fav_list.append(new_link)

                # Перезаписываем файл избранного
                with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
                    f.write("\n".join(new_fav_list) + ("\n" if new_fav_list else ""))
                
                print(f"⭐ Обновлено избранное: {len(new_fav_list)} серверов.")
                executed_any = True

                # Сохраняем обновленный список избранного
                with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
                    f.write("\n".join(new_fav_list) + ("\n" if new_fav_list else ""))
                executed_any = True

    except Exception as e:
        print(f"⚠️ Ошибка обработки команд: {e}")

    return vetted_list, pinned_list, executed_any

def update_issue(repo, label, body, env):
    """Техническая функция для редактирования Issue."""
    try:
        # 1. Получаем номер issue
        cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', label, '--json', 'number']
        # Используем decode('utf-8') для безопасности
        output = subprocess.check_output(cmd, env=env).decode('utf-8')
        data = json.loads(output)
        
        if data:
            num = str(data[0]['number'])
            
            # 2. Пишем во временный файл
            tmp_file = f"tmp_body_{label}.txt" # Уникальное имя на случай гонки потоков
            with open(tmp_file, "w", encoding="utf-8") as f: 
                f.write(body)
            
            # 3. Редактируем через файл
            subprocess.run([
                'gh', 'issue', 'edit', num, 
                '--repo', repo, 
                '--body-file', tmp_file
            ], env=env, check=True)
            
            # 4. Подчищаем за собой
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
    except Exception as e:
        print(f"⚠️ Ошибка обновления панели {label}: {e}")

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

    # Юзер-агенты для имитации реального трафика
    user_agents = stress_config.get("user_agents") or DEFAULT_MOBILE_USER_AGENTS
    probe_paths = stress_config.get("probe_paths") or DEFAULT_PROBE_PATHS

    total_attempts = max(1, int(stress_config.get("torture_total_attempts", 20)))
    min_success = max(1, int(stress_config.get("torture_min_success", total_attempts)))
    min_success = min(min_success, total_attempts)

    success = 0
    for i in range(total_attempts):
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
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

    # --- СНАЧАЛА ЗАГРУЖАЕМ ВСЁ ИЗ ФАЙЛОВ ---
    ranking_db = {}
    if os.path.exists(RANK_FILE):
        with open(RANK_FILE, 'r', encoding='utf-8') as f:
            ranking_db = json.load(f)

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

    working_for_base = list(ranking_db.keys())

    # --- ШАГ 2: ВЫПОЛНЕНИЕ КОМАНД ---
    vetted_list, pinned_list, executed = process_all_controls(
        token, repo, vetted_list, pinned_list, ranking_db
    )

    is_scheduled = os.getenv("GITHUB_EVENT_NAME") == "schedule"
    is_manual = os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"

    # --- ПРЕДОХРАНИТЕЛЬ ---
    # Если это клик по Issue (событие edited), но кнопка "Подтвердить" НЕ нажата:
    if not executed and not is_scheduled and not is_manual:
        print("☕ Кнопка подтверждения не нажата. Бот уходит тихо, не трогая GitHub.")
        # МЫ НЕ ВЫЗЫВАЕМ refresh_all_panels здесь!
        # Твои галочки остаются висеть в GitHub, бот их не затирает.
        return 

    # Если мы здесь, значит либо нажата кнопка, либо это запуск по расписанию
    if executed:
        print("🧹 Команды выполнены, фиксирую изменения в файлы...")
        with open(VETTED_FILE, 'w', encoding='utf-8') as vf:
            vf.write("\n".join(vetted_list) + ("\n" if vetted_list else ""))
        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)

    # Обновляем панели ТОЛЬКО если что-то реально произошло
    print("📝 Обновляю панели в GitHub...")
    refresh_all_panels(token, repo, ranking_db, vetted_list, pinned_list)
    
    # Если это не расписание и команд не было — на пытки не идем, но панели уже обновлены!
    if not executed and not is_scheduled:
        print("☕ Панели обновлены. Пытки пропущены (нет команд/расписания).")
        return 

    # --- ШАГ 4: ДАЛЬШЕ ИДУТ ПЫТКИ ---
    print("🚀 Начинаю инспекцию серверов...")

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
        rank = data.get("rank", 0) if isinstance(data, dict) else data
        link = data.get("link", base) if isinstance(data, dict) else base
        
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

        with ThreadPoolExecutor(max_workers=5) as executor:
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
                if status == "OK" and base in ranking_db and isinstance(ranking_db[base], dict):
                    ranking_db[base]['rank'] = max(0, ranking_db[base].get('rank', 50) - 30)
                    ranking_db[base]['last_torture'] = f"FAIL {success_hits}/{total_hits}"
                elif status in {"IPv6_BAN", "ERROR"}:
                    if base in ranking_db:
                        del ranking_db[base]
                    if status == "IPv6_BAN":
                        add_to_blacklist(base)
                    remove_from_all(base)
                
                # Если сервер просто не прошел пытку (статус OK, но success False)
                elif status == "OK":
                    if base in ranking_db:
                        ranking_db[base]['rank'] = max(0, ranking_db[base].get('rank', 50) - 30)
                        ranking_db[base]['last_torture'] = "FAIL"

        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)
    else:
        print("⌛ Нет новых кандидатов для пыток.")

    # ПЕРЕЧИТЫВАЕМ актуальный список элиты, 
    # потому что туда добавились новые серверы во время пыток
    if os.path.exists(VETTED_FILE):
        with open(VETTED_FILE, 'r', encoding='utf-8') as f:
            vetted_list = [l.strip() for l in f if 'vless' in l]
    
    # ФИНАЛЬНЫЙ СИНХРОН С GITHUB
    print("🔄 Финальное обновление панелей после инспекции...")
    refresh_all_panels(token, repo, list(ranking_db.keys()), vetted_list, pinned_list)
    commit_and_push()
    
if __name__ == "__main__":
    main_torturer()
