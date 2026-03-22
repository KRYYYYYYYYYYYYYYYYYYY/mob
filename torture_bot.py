import socket
import time
import os
import ssl
import re
import json
import subprocess
import ctypes
import urllib.parse
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
    "Mozilla/5.0 (Linux; Android 13; SM-A336B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.179 Mobile Safari/537.36 happ/1.0",
    "okhttp/4.12.0 v2rayNG/1.9.28",
]
DEFAULT_PROBE_PATHS = ["/", "/generate_204", "/favicon.ico"]

file_lock = threading.Lock()
go_lib = None

def normalize_rank_entry(base: str, data):
    """Унифицирует формат ranking.json: dict(rank, link, ...)."""
    if isinstance(data, dict):
        rank = int(data.get("rank", 0) or 0)
        entry = dict(data)
        entry["rank"] = max(0, rank)
        entry["link"] = entry.get("link", base)
        return entry
    if isinstance(data, (int, float)):
        return {"rank": max(0, int(data)), "link": base}
    return {"rank": 0, "link": base}

def decrease_rank(ranking_db, base, delta=30, note="FAIL"):
    """Безопасно снижает ранг даже для legacy-формата (int)."""
    if base not in ranking_db:
        return
    entry = normalize_rank_entry(base, ranking_db[base])
    entry["rank"] = max(0, int(entry.get("rank", 0)) - int(delta))
    entry["last_torture"] = note
    ranking_db[base] = entry

def l7_multi_probe(link: str, stress_config: dict, fallback_sni: str) -> bool:
    """Проверка кворумом по нескольким SNI-кандидатам для лучшего совпадения с мобилкой."""
    timeout_sec = max(1, int(stress_config.get("timeout", 3)))
    min_hits = max(1, int(stress_config.get("l7_min_success", 2)))
    max_candidates = max(1, int(stress_config.get("l7_max_candidates", 3)))
    sni_candidates = extract_sni_candidates(link)
    if fallback_sni and fallback_sni not in sni_candidates:
        sni_candidates.append(fallback_sni)
    sni_candidates = sni_candidates[:max_candidates]
    hits = 0
    for cand_sni in sni_candidates:
        if probe_vless_l7(link, cand_sni, timeout_sec=timeout_sec) > 0:
            hits += 1
            if hits >= min_hits:
                return True
    return False

def init_checker_lib():
    """Подключает Go L7 checker (libchecker.so) для инспектора mob."""
    global go_lib
    lib_path = os.path.abspath("libchecker.so")
    if not os.path.exists(lib_path):
        print("⚠️ libchecker.so не найден, инспектор продолжит legacy-проверками.")
        return

    go_lib = ctypes.cdll.LoadLibrary(lib_path)
    go_lib.CheckVlessL7.argtypes = [
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int
    ]
    go_lib.CheckVlessL7.restype = ctypes.c_int


def extract_sni(link: str) -> str:
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    return params.get("sni", [""])[0]

def extract_sni_candidates(link: str):
    parsed = urllib.parse.urlparse(link)
    params = urllib.parse.parse_qs(parsed.query)
    candidates = []
    for key in ("sni", "host"):
        value = params.get(key, [""])[0].strip()
        if value and value not in candidates:
            candidates.append(value)
    if parsed.hostname and parsed.hostname not in candidates:
        candidates.append(parsed.hostname)
    return candidates

def probe_vless_l7(link: str, target_sni: str, timeout_sec: int = 5) -> int:
    if go_lib is None:
        return 0

    try:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        host, port = extract_host_port(link)
        if not host or not port:
            return 0
        uuid = parsed.username if parsed.username else ""
        pbk = params.get('pbk', [''])[0]
        sid = params.get('sid', [''])[0]
        flow = params.get('flow', [''])[0]
        return int(go_lib.CheckVlessL7(
            host.encode('utf-8'),
            int(port),
            uuid.encode('utf-8'),
            (target_sni or "").encode('utf-8'),
            pbk.encode('utf-8'),
            sid.encode('utf-8'),
            flow.encode('utf-8'),
            int(timeout_sec)
        ))
    except Exception:
        return 0

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
    
    # --- ШАГ 0: ГЛОБАЛЬНАЯ ПОДГОТОВКА ДАННЫХ ДЛЯ ВСЕХ ПАНЕЛЕЙ ---
    fav_list = []
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            fav_list = [l.strip() for l in f if 'vless' in l]
    
    # Мы создаем fav_bases здесь, в самом начале, 
    # чтобы переменная была видна во всей функции
    fav_bases = {l.split('#')[0].strip() for l in fav_list}

    # --- 1. ПАНЕЛЬ ЧЕРНОГО СПИСКА (control) ---
    body_ctrl = f"### 🎮 Панель Blacklist (Весь wifi.txt)\n🕒 `{update_time}`\n\n"
    body_ctrl += "- [ ] 💀 **ПОДТВЕРДИТЬ_БАН**\n\n---\n\n"
    
    # Теперь и fav_list, и pinned_list доступны для фильтрации
    wifi_to_ban = get_wifi_candidates(pinned_list, fav_list)
    
    if wifi_to_ban:
        for full_link in wifi_to_ban:
            body_ctrl += f"- [ ] {full_link.strip()}\n"
    else:
        body_ctrl += "_Список пуст_\n"

    # Сохранение и обновление Issue...
    with open('test1/issue_body.txt', 'w', encoding='utf-8') as f:
        f.write(body_ctrl)
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

    # --- 4. ПАНЕЛЬ ИЗБРАННОЕ ---
    body_fav = f"### ⭐ Избранные серверы\n🕒 `{update_time}`\n\n"
    body_fav += "- [ ] 🏆 **ПОДТВЕРДИТЬ_ИЗБРАННОЕ**\n\n---\n\n"
    
    # 1. Загружаем то, что уже лежит в избранном (база : полная_строка_со_звездой)
    fav_map = {}
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            for l in f:
                if 'vless://' in l:
                    base = l.split('#')[0].strip()
                    fav_map[base] = l.strip()

    # 2. Получаем всех кандидатов из wifi.txt (за вычетом закрепов)
    all_candidates = get_wifi_candidates(pinned_list, []) 
    
    for link in all_candidates:
        link_clean = link.strip()
        base = link_clean.split('#')[0].strip()
        
        # Если база сервера есть в нашем fav_map — значит он избранный
        if base in fav_map:
            # Рисуем КРЕСТИК и берем имя со звездой из карты
            body_fav += f"- [x] {fav_map[base]}\n"
        else:
            # Рисуем ПУСТОЙ чекбокс и обычную ссылку
            body_fav += f"- [ ] {link_clean}\n"
    
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
        "l7_min_success": 2,
        "l7_max_candidates": 3,
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
            config["l7_min_success"] = int(data.get("l7_min_success", config["l7_min_success"]))
            config["l7_max_candidates"] = int(data.get("l7_max_candidates", config["l7_max_candidates"]))
            if isinstance(data.get("mobile_user_agents"), list) and data.get("mobile_user_agents"):
                config["user_agents"] = [str(x) for x in data["mobile_user_agents"] if str(x).strip()]
            elif isinstance(data.get("user_agents"), list) and data.get("user_agents"):
                config["user_agents"] = [str(x) for x in data["user_agents"] if str(x).strip()]
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
            if "ПОДТВЕРДИТЬ_ИЗБРАННОЕ" in body and "[x]" in body.lower():
                new_fav_list = []
                checked_bases = {} # Словарь {база: новое_имя_со_звездой}

                for line in body.splitlines():
                    match = re.search(r'- \[[xX ]\]\s+(vless://[^\n\r#]+)(?:#([^\n\r]+))?', line)
                    if match:
                        base_part = match.group(1).strip()
                        raw_name = match.group(2).strip() if match.group(2) else "Server"
                        is_checked = '- [x]' in line.lower()
                        
                        clean_name = raw_name.replace('⭐', '').strip()
                        
                        if is_checked:
                            new_name = f"⭐ {clean_name}"
                            new_link = f"{base_part}#{new_name}"
                            new_fav_list.append(new_link)
                            checked_bases[base_part] = new_name

                # 1. Сохраняем в favorites.txt
                with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
                    f.write("\n".join(new_fav_list) + ("\n" if new_fav_list else ""))

                # 2. !!! ГЛАВНОЕ: Переименовываем серверы в wifi.txt !!!
                if os.path.exists(WIFI_FILE):
                    with open(WIFI_FILE, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    
                    new_wifi_lines = []
                    for l in lines:
                        if 'vless://' in l:
                            b = l.split('#')[0].strip()
                            if b in checked_bases:
                                # Если в Issue стоит галочка — ставим звезду в wifi.txt
                                new_wifi_lines.append(f"{b}#{checked_bases[b]}\n")
                            else:
                                # Если галочки нет — убираем звезду из wifi.txt
                                clean_l = l.replace('⭐', '').strip()
                                new_wifi_lines.append(clean_l + "\n")
                        else:
                            new_wifi_lines.append(l)
                            
                    with open(WIFI_FILE, 'w', encoding='utf-8') as f:
                        f.writelines(new_wifi_lines)

                print(f"⭐ Избранное синхронизировано с wifi.txt: {len(new_fav_list)} шт.")
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
        fallback_sni = extract_sni(link) or server_hostname
        l7_ok = l7_multi_probe(link, stress_config, fallback_sni)
        if l7_ok:
            success += 1
            if success >= min_success:
                return True, success, total_attempts
            if i < total_attempts - 1:
                time.sleep(stress_config.get("between_attempts_sleep", 0.35))
            continue

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
            loaded = json.load(f)
            if isinstance(loaded, dict):
                ranking_db = {base: normalize_rank_entry(base, data) for base, data in loaded.items()}
            
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

    # --- ВОТ СЮДА ВСТАВЛЯЕМ ПРИОРИТЕТ ВЫПОЛНЕНИЯ ISSUES ---

    # 1. Если была нажата кнопка (executed), мы уже всё сделали.
    # Выходим, чтобы не запускать пытки, которые длятся часами.
    if executed:
        print("✅ Команды из Issues выполнены, панели обновлены. Завершаю работу (Priority: Issues).")
        commit_and_push()
        return 

    # 2. Если это запуск по расписанию (schedule), то идем пытать.
    if is_scheduled:
        print("⏰ Запуск по расписанию. Перехожу к инспекции (пыткам)...")
    else:
        # Если это ручной запуск (workflow_dispatch) без нажатых кнопок — тоже выходим
        print("☕ Команд нет, расписания нет. Пытки не требуются. Выход.")
        commit_and_push() # На всякий случай пушим, если были мелкие правки
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
        entry = normalize_rank_entry(base, data)
        rank = entry.get("rank", 0)
        link = entry.get("link", base)
        
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

    # ВОТ СЮДА ВСТАВЛЯЙ:
    print(f"DEBUG: Всего кандидатов после фильтрации: {len(candidates)}")
    if not candidates:
        print("DEBUG: Пытать некого, все отфильтровано.")

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

        with ThreadPoolExecutor(max_workers=15) as executor:
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
                if status == "OK":
                    decrease_rank(ranking_db, base, delta=30, note=f"FAIL {success_hits}/{total_hits}")
                elif status in {"IPv6_BAN", "ERROR"}:
                    if base in ranking_db:
                        del ranking_db[base]
                    if status == "IPv6_BAN":
                        add_to_blacklist(base)
                    remove_from_all(base)
                

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
    refresh_all_panels(token, repo, ranking_db, vetted_list, pinned_list)
    commit_and_push()
    
if __name__ == "__main__":
    init_checker_lib()
    main_torturer()
