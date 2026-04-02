import json
import os
import re
import urllib.request
from datetime import datetime, timezone

UA_VERSIONS_FILE = "ua_versions.json"
DEFAULT_UA_VERSIONS = {
    "happ": {
        "version": "3.16.0",
        "build": "1741613",
        "source": "https://raw.githubusercontent.com/Happ-proxy/happ-android/refs/heads/main/release",
    },
    "v2rayng": {
        "version": "2.0.17",
        "okhttp": "4.12.0",
        "source": "https://raw.githubusercontent.com/2dust/v2rayNG/master/V2rayNG/app/build.gradle.kts",
    },
    "updated_at": "",
}


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _fetch_text(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_happ_version(source_url: str) -> str:
    raw = _fetch_text(source_url)
    m = re.search(r'"data"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"', raw)
    if m:
        return m.group(1)
    apk_block = re.search(r'"apk"\s*:\s*\{.*?"stable"\s*:\s*\{.*?"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"', raw, re.S)
    if apk_block:
        return apk_block.group(1)
    raise ValueError("Happ version not found")


def fetch_v2rayng_version(source_url: str) -> str:
    raw = _fetch_text(source_url)
    m = re.search(r'versionName\s*=\s*"([^"]+)"', raw)
    if not m:
        raise ValueError("v2rayNG versionName not found")
    return m.group(1).strip()


def refresh_ua_versions(path: str = UA_VERSIONS_FILE) -> dict:
    data = DEFAULT_UA_VERSIONS.copy()
    current = _read_json(path)
    if isinstance(current, dict):
        data.update(current)
        data["happ"] = {**DEFAULT_UA_VERSIONS["happ"], **dict(current.get("happ", {}))}
        data["v2rayng"] = {**DEFAULT_UA_VERSIONS["v2rayng"], **dict(current.get("v2rayng", {}))}

    data["happ"]["version"] = fetch_happ_version(data["happ"]["source"])
    data["v2rayng"]["version"] = fetch_v2rayng_version(data["v2rayng"]["source"])
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(path, data)
    return data


def load_ua_versions(path: str = UA_VERSIONS_FILE) -> dict:
    data = _read_json(path)
    if not isinstance(data, dict):
        data = DEFAULT_UA_VERSIONS
        _write_json(path, data)
    return data


def get_mobile_user_agents(path: str = UA_VERSIONS_FILE):
    cfg = load_ua_versions(path)
    happ = cfg.get("happ", {})
    v2 = cfg.get("v2rayng", {})
    happ_ua = f"Happ/{happ.get('version', '3.16.0')}/Android/{happ.get('build', '1741613')}"
    v2_ua = f"okhttp/{v2.get('okhttp', '4.12.0')} v2rayNG/{v2.get('version', '2.0.17')}"
    return happ_ua, v2_ua


def maybe_refresh_ua_versions(path: str = UA_VERSIONS_FILE, max_age_hours: int = 24) -> dict:
    try:
        if os.path.exists(path):
            age_hours = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)) / 3600.0
            if age_hours < max_age_hours:
                return load_ua_versions(path)
        return refresh_ua_versions(path)
    except Exception:
        return load_ua_versions(path)
