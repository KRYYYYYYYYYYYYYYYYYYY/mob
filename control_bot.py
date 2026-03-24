import json
import os

from torture_bot import (
    RANK_FILE,
    VETTED_FILE,
    PINNED_FILE,
    normalize_rank_entry,
    process_all_controls,
    refresh_all_panels,
    commit_and_push,
)


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


def main():
    token = os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        print("⚠️ GH_TOKEN or GITHUB_REPOSITORY is missing. Exiting.")
        return

    ranking_db = _load_ranking()
    vetted_list = _load_lines(VETTED_FILE)
    pinned_list = _load_lines(PINNED_FILE)

    vetted_list, pinned_list, executed = process_all_controls(
        token, repo, vetted_list, pinned_list, ranking_db
    )

    if not executed:
        print("☕ Подтвержденных команд нет. Ничего не меняю.")
        return

    with open(VETTED_FILE, 'w', encoding='utf-8') as vf:
        vf.write("\n".join(vetted_list) + ("\n" if vetted_list else ""))

    with open(RANK_FILE, 'w', encoding='utf-8') as f:
        json.dump(ranking_db, f, ensure_ascii=False, indent=4)

    refresh_all_panels(token, repo, ranking_db, vetted_list, pinned_list)
    commit_and_push()


if __name__ == "__main__":
    main()
