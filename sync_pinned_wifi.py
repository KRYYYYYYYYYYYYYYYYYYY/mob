import argparse
import os
import subprocess
from typing import List, Set, Tuple


PROTO_PREFIXES = ("vless://", "vmess://", "trojan://", "ss://")


def is_link_line(line: str) -> bool:
    low = line.lower().strip()
    return any(low.startswith(p) for p in PROTO_PREFIXES)


def base_of(link: str) -> str:
    return link.split("#", 1)[0].strip()


def parse_pinned_lines(text: str) -> List[str]:
    links = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if is_link_line(line):
            links.append(line)
    return links


def read_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_previous_pinned_from_git(pinned_path: str, ref: str) -> List[str]:
    cmd = ["git", "show", f"{ref}:{pinned_path}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return parse_pinned_lines(result.stdout)


def split_wifi(content: str) -> Tuple[List[str], List[str]]:
    header_lines: List[str] = []
    link_lines: List[str] = []
    for raw in content.splitlines():
        line = raw.rstrip("\n")
        if is_link_line(line.strip()):
            link_lines.append(line.strip())
        else:
            header_lines.append(line)
    return header_lines, link_lines


def merge_wifi(header: List[str], pinned: List[str], rest: List[str]) -> str:
    body = []
    body.extend(header)
    if body and body[-1].strip() != "":
        body.append("")
    body.extend(pinned)
    body.extend(rest)
    return "\n".join(body).rstrip() + "\n"


def sync_pinned(
    pinned_path: str,
    wifi_path: str,
    previous_ref: str,
    dry_run: bool = False,
) -> Tuple[bool, int, int]:
    current_pinned = parse_pinned_lines(read_text(pinned_path))
    current_bases: Set[str] = {base_of(x) for x in current_pinned}

    previous_pinned = load_previous_pinned_from_git(pinned_path, previous_ref)
    previous_bases: Set[str] = {base_of(x) for x in previous_pinned}

    wifi_text = read_text(wifi_path)
    header, wifi_links = split_wifi(wifi_text)

    # Удаляем старые pinned из wifi по базам из предыдущей версии pinned,
    # а также убираем текущие pinned-базы из хвоста, чтобы не было дублей.
    remove_bases = previous_bases | current_bases
    removed = 0
    rest_links: List[str] = []
    seen_bases: Set[str] = set()
    for link in wifi_links:
        b = base_of(link)
        if b in remove_bases:
            removed += 1
            continue
        if b in seen_bases:
            continue
        rest_links.append(link)
        seen_bases.add(b)

    new_content = merge_wifi(header, current_pinned, rest_links)
    changed = new_content != wifi_text
    if changed and not dry_run:
        with open(wifi_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    return changed, len(current_pinned), removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast pinned -> wifi sync")
    parser.add_argument("--pinned", default="test1/pinned.txt")
    parser.add_argument("--wifi", default="kr/mob/wifi.txt")
    parser.add_argument("--previous-ref", default="HEAD~1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changed, pinned_count, removed_count = sync_pinned(
        pinned_path=args.pinned,
        wifi_path=args.wifi,
        previous_ref=args.previous_ref,
        dry_run=args.dry_run,
    )
    mark = "CHANGED" if changed else "NO-CHANGES"
    print(
        f"📌 sync_pinned_wifi: {mark}; pinned={pinned_count}; "
        f"removed_old_or_dups={removed_count}; prev_ref={args.previous_ref}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
