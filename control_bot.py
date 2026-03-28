import json
import os
import re
import subprocess
import time

from torture_bot import (
    RANK_FILE,
    PINNED_FILE,
    VETTED_FILE,
    WIFI_FILE,
    FAVORITES_FILE,
    DEFERRED_FILE,
    INPUT_FILE,
    normalize_rank_entry,
    remove_from_all,
    add_to_blacklist,
    get_wifi_candidates,
)

CONTROL_PRIMARY_LABEL = os.getenv("CONTROL_PANEL_LABEL", "menu1")
CONTROL_LABEL_CANDIDATES = [CONTROL_PRIMARY_LABEL, "control"]
CONTROL_LABEL_CANDIDATES = list(dict.fromkeys([x for x in CONTROL_LABEL_CANDIDATES if x]))
CONTROL_BODY_FILE = "test1/menu1.txt"
CHECK_WORKFLOW_FILE = os.getenv("CHECK_WORKFLOW_FILE", "main.yml")


def _load_lines(path: str):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if 'vless' in line]


def _load_ranking():
    if not os.path.exists(RANK_FILE):
        return {}
    with open(RANK_FILE, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        return {}
    return {base: normalize_rank_entry(base, data) for base, data in loaded.items()}


def update_issue(repo, label, body, env):
    try:
        cmd = ['gh', 'issue', 'list', '--repo', repo, '--label', label, '--json', 'number']
        output = subprocess.check_output(cmd, env=env).decode('utf-8')
        data = json.loads(output)
        if data:
            num = str(data[0]['number'])
            tmp_file = f"tmp_body_{label}.txt"
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(body)
            subprocess.run([
                'gh', 'issue', 'edit', num,
                '--repo', repo,
                '--body-file', tmp_file
            ], env=env, check=True)
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
    except Exception as e:
        print(f"⚠️ Ошибка обновления панели {label}: {e}")


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


def _get_issue_body_by_labels(repo, labels, env):
    """Возвращает (body, label) для первой найденной issue из списка labels."""
    for label in labels:
        try:
            out = subprocess.check_output(
                ['gh', 'issue', 'list', '--repo', repo, '--label', label, '--json', 'body'],
                env=env
            )
            data = json.loads(out)
            if data:
                return data[0]['body'], label
        except Exception:
            continue
    return "", None


def _is_checkbox_command_checked(body: str, marker: str) -> bool:
    for line in body.splitlines():
        if marker in line and "[x]" in line.lower():
            return True
    return False


def _full_replace_non_immortals(pinned_list, fav_list):
    """
    Удаляет не-pinned/non-favorite/non-vetted из wifi и синхронно вырезает из 1.txt
    только те базы, которые были удалены именно из wifi (vetted не трогаем).
    """
    keep_bases = {x.split("#")[0].strip() for x in pinned_list}
    keep_bases.update({x.split("#")[0].strip() for x in fav_list})
    if os.path.exists(VETTED_FILE):
        with open(VETTED_FILE, "r", encoding="utf-8") as f:
            keep_bases.update(
                line.strip().split("#")[0].strip()
                for line in f
                if any(p in line.lower() for p in ("vless://", "vmess://", "trojan://", "ss://"))
            )

    # 1) wifi.txt: оставляем только служебные строки и pinned/favorites/vetted
    removed_from_wifi = set()
    if os.path.exists(WIFI_FILE):
        with open(WIFI_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if "vless://" not in stripped and "vmess://" not in stripped and "trojan://" not in stripped and "ss://" not in stripped:
                new_lines.append(line)
                continue
            base = stripped.split("#")[0].strip()
            if base in keep_bases:
                new_lines.append(line)
            else:
                removed_from_wifi.add(base)
        with open(WIFI_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    # 2) 1.txt: удаляем только те базы, которые были удалены из wifi
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            bases = [line.strip() for line in f if line.strip()]
        filtered = [b for b in bases if b.split("#")[0].strip() not in removed_from_wifi]
        with open(INPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(filtered))

    # 3) deferred.txt: та же логика, что и 1.txt — удаляем только то, что вычищено из wifi
    if os.path.exists(DEFERRED_FILE):
        with open(DEFERRED_FILE, "r", encoding="utf-8") as f:
            deferred_lines = [line.strip() for line in f if line.strip()]
        deferred_filtered = [
            line for line in deferred_lines
            if line.split("#")[0].strip() not in removed_from_wifi
        ]
        with open(DEFERRED_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(deferred_filtered))


def _cancel_running_workflow_runs(repo: str, workflow_file: str, env: dict):
    """Отменяет queued/in_progress ранны указанного workflow."""
    try:
        out = subprocess.check_output(
            [
                "gh", "run", "list",
                "--repo", repo,
                "--workflow", workflow_file,
                "--json", "databaseId,status",
                "--limit", "30",
            ],
            env=env
        )
        runs = json.loads(out)
        for run in runs:
            rid = run.get("databaseId")
            status = (run.get("status") or "").lower()
            if rid and status in {"queued", "in_progress", "waiting", "requested"}:
                subprocess.run(
                    ["gh", "run", "cancel", str(rid), "--repo", repo],
                    env=env,
                    check=False
                )
    except Exception as e:
        print(f"⚠️ Не удалось отменить старые ранны {workflow_file}: {e}")


def _dispatch_workflow(repo: str, workflow_file: str, env: dict):
    """Запускает workflow вручную после сигнала панели."""
    try:
        subprocess.run(
            ["gh", "workflow", "run", workflow_file, "--repo", repo],
            env=env,
            check=True
        )
    except Exception as e:
        print(f"⚠️ Не удалось запустить workflow {workflow_file}: {e}")


def refresh_all_panels(token, repo, ranking_db, vetted_list, pinned_list):
    update_time = time.strftime("%d.%m.%Y %H:%M:%S")
    env_gh = {**os.environ, "GH_TOKEN": token}

    fav_list = []
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            fav_list = [l.strip() for l in f if 'vless' in l]
    fav_bases = {l.split('#')[0].strip() for l in fav_list}

    body_ctrl = f"### 🎮 Панель меню (Весь wifi.txt)\n🕒 `{update_time}`\n\n"
    body_ctrl += "- [ ] ✅ **ПОДТВЕРДИТЬ**\n\n---\n\n"
    body_ctrl += "- [ ] ♻️ **ПОЛНАЯ_ЗАМЕНА**\n\n---\n\n"
    with open(CONTROL_BODY_FILE, 'w', encoding='utf-8') as f:
        f.write(body_ctrl)
    update_issue_from_file(repo, CONTROL_PRIMARY_LABEL, CONTROL_BODY_FILE, env_gh)

    body_pin = f"### 💎 Кандидаты в Элиту\n🕒 `{update_time}`\n\n"
    body_pin += "- [ ] ✅ **ПРИМЕНИТЬ_PIN_BAN**\n\n---\n\n"
    for full_link in vetted_list:
        body_pin += f"📡 {full_link}\n"
        body_pin += f"- [ ] PIN: {full_link.strip()}\n"
        body_pin += f"- [ ] BAN: {full_link.strip()}\n"
        body_pin += "\n---\n"
    update_issue(repo, 'pin_control', body_pin, env_gh)

    body_unp = f"### 👑 Управление Закрепами\n🕒 `{update_time}`\n\n"
    body_unp += "- [ ] 🔓 **ПОДТВЕРДИТЬ_РАСПИН**\n\n---\n\n"
    for full_link in pinned_list:
        body_unp += f"- [ ] {full_link.strip()}\n"
    update_issue(repo, 'unpin_control', body_unp, env_gh)

    body_fav = f"### ⭐ Избранные серверы\n🕒 `{update_time}`\n\n"
    body_fav += "- [ ] 🏆 **ПОДТВЕРДИТЬ_ИЗБРАННОЕ**\n\n---\n\n"
    all_candidates = get_wifi_candidates(pinned_list, [])
    if all_candidates:
        for full_link in all_candidates:
            base = full_link.split('#')[0].strip()
            is_fav = base in fav_bases
            checkbox = "[x]" if is_fav else "[ ]"
            display_name = full_link.split('#', 1)[1] if '#' in full_link else "Server"
            body_fav += f"- {checkbox} {base}#{display_name}\n"
    else:
        body_fav += "_Кандидатов нет_\n"

    update_issue(repo, 'fav_control', body_fav, env_gh)


def process_all_controls(token, repo, vetted_list, pinned_list, ranking_db):
    executed_any = False
    env_gh = {**os.environ, "GH_TOKEN": token}

    def find_checked_vless(text):
        found = re.findall(r'\[[xX]\]\s+(vless://[^\n\r`\'"]+)', text)
        return [l.strip().rstrip(':') for l in found]

    try:
        body, _ = _get_issue_body_by_labels(repo, CONTROL_LABEL_CANDIDATES, env_gh)
        if body:
            confirm_checked = _is_checkbox_command_checked(body, "ПОДТВЕРДИТЬ")
            full_replace_checked = _is_checkbox_command_checked(body, "ПОЛНАЯ_ЗАМЕНА")
            legacy_full_replace_checked = _is_checkbox_command_checked(body, "ПОДТВЕРДИТЬ_ПОЛНУЮ_ЗАМЕНУ")

            if confirm_checked and (full_replace_checked or legacy_full_replace_checked):
                fav_list = []
                if os.path.exists(FAVORITES_FILE):
                    with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
                        fav_list = [
                            l.strip() for l in f
                            if any(p in l.lower() for p in ("vless://", "vmess://", "trojan://", "ss://"))
                        ]
                _full_replace_non_immortals(pinned_list, fav_list)
                # Приоритетный сигнал: гасим текущий run чекера и запускаем новый.
                _cancel_running_workflow_runs(repo, CHECK_WORKFLOW_FILE, env_gh)
                _dispatch_workflow(repo, CHECK_WORKFLOW_FILE, env_gh)
                executed_any = True

            if (
                (confirm_checked and not full_replace_checked)
                or ("ПОДТВЕРДИТЬ_БАН" in body and "[x]" in body)
            ):
                links = find_checked_vless(body)
                for base_full in links:
                    base = base_full.split('#')[0].strip()
                    add_to_blacklist(base)
                    remove_from_all(base)
                    if base in ranking_db:
                        del ranking_db[base]
                    executed_any = True

        out = subprocess.check_output(['gh', 'issue', 'list', '--repo', repo, '--label', 'pin_control', '--json', 'body'], env=env_gh)
        data = json.loads(out)
        if data:
            body = data[0]['body']
            if "ПРИМЕНИТЬ_PIN_BAN" in body and "[x]" in body:
                to_pin = re.findall(r'\[[xX]\]\s+PIN:\s+(vless://[^\n\r`\'"]+)', body)
                to_ban = re.findall(r'\[[xX]\]\s+BAN:\s+(vless://[^\n\r`\'"]+)', body)

                for s in to_pin:
                    base_full = s.strip().rstrip(':')
                    base = base_full.split('#')[0].strip()
                    if all(base != p.split("#")[0].strip() for p in pinned_list):
                        with open(PINNED_FILE, 'a', encoding='utf-8') as pf:
                            pf.write(base_full + "\n")
                        pinned_list.append(base_full)
                    vetted_list = [v for v in vetted_list if v.split('#')[0].strip() != base]
                    executed_any = True

                for s in to_ban:
                    base_full = s.strip().rstrip(':')
                    base = base_full.split('#')[0].strip()
                    add_to_blacklist(base)
                    remove_from_all(base)
                    vetted_list = [v for v in vetted_list if v.split('#')[0].strip() != base]
                    executed_any = True

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

        out = subprocess.check_output(['gh', 'issue', 'list', '--repo', repo, '--label', 'fav_control', '--json', 'body'], env=env_gh)
        data = json.loads(out)
        if data:
            body = data[0]['body']
            if "ПОДТВЕРДИТЬ_ИЗБРАННОЕ" in body and "[x]" in body.lower():
                new_fav_list = []
                checked_bases = {}

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

                with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
                    f.write("\n".join(new_fav_list) + ("\n" if new_fav_list else ""))

                if os.path.exists(WIFI_FILE):
                    with open(WIFI_FILE, 'r', encoding='utf-8') as f:
                        lines = f.readlines()

                    new_wifi_lines = []
                    for l in lines:
                        if 'vless://' in l:
                            b = l.split('#')[0].strip()
                            if b in checked_bases:
                                new_wifi_lines.append(f"{b}#{checked_bases[b]}\n")
                            else:
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


def _commit_and_push():
    try:
        subprocess.run(['git', 'config', '--local', 'user.name', 'github-actions[bot]'], check=True)
        subprocess.run(['git', 'config', '--local', 'user.email', 'github-actions[bot]@users.noreply.github.com'], check=True)
        subprocess.run(['git', 'add', WIFI_FILE, VETTED_FILE, PINNED_FILE, RANK_FILE, FAVORITES_FILE, 'test1/blacklist.txt'], check=True)
        status = subprocess.run(['git', 'diff', '--cached', '--quiet'])
        if status.returncode != 0:
            subprocess.run(['git', 'commit', '-m', '🎛️ Apply control-panel actions [skip ci]'], check=True)
            subprocess.run(['git', 'pull', '--rebase', '-X', 'theirs', 'origin', 'main'], check=True)
            subprocess.run(['git', 'push', 'origin', 'main'], check=True)
            print('✅ Control changes pushed.')
        else:
            print('ℹ️ No control changes to commit.')
    except Exception as e:
        print(f'⚠️ Commit/push failed: {e}')


def main():
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    event_name = os.getenv("GITHUB_EVENT_NAME", "")

    if not token or not repo:
        print("⚠️ GH_TOKEN or GITHUB_REPOSITORY is missing. Exiting.")
        return

    ranking_db = _load_ranking()
    vetted_list = _load_lines(VETTED_FILE)
    pinned_list = _load_lines(PINNED_FILE)

    vetted_list, pinned_list, executed = process_all_controls(
        token, repo, vetted_list, pinned_list, ranking_db
    )

    if executed:
        with open(VETTED_FILE, 'w', encoding='utf-8') as vf:
            vf.write("\n".join(vetted_list) + ("\n" if vetted_list else ""))
        with open(RANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(ranking_db, f, ensure_ascii=False, indent=4)

    if executed or event_name in {"schedule", "workflow_dispatch"}:
        refresh_all_panels(token, repo, ranking_db, vetted_list, pinned_list)

    if executed:
        _commit_and_push()
    else:
        print("☕ Подтвержденных команд нет. Панели обновлены только при schedule/workflow_dispatch.")


if __name__ == "__main__":
    main()
