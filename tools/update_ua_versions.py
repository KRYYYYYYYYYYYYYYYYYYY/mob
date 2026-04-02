#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ua_versions import refresh_ua_versions

if __name__ == "__main__":
    data = refresh_ua_versions()
    happ_ver = data.get("happ", {}).get("version", "?")
    v2_ver = data.get("v2rayng", {}).get("version", "?")
    print(f"updated ua_versions.json: happ={happ_ver}, v2rayNG={v2_ver}")
