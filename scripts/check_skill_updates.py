"""探测第三方接入能力是否有新版本：python scripts/check_skill_updates.py

判定口径：npm 包看 registry 的 dist-tags.latest；Skill 包看来源平台的版本字段。
不发任何真实业务请求，只用 GET，可放 CI 或每周手动跑。
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "src" / "skills" / "sources.json"

# 本机有 HTTP 代理，必须绕开，否则国内平台会拿到 400
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def npm_latest(pkg: str) -> str | None:
    url = "https://registry.npmjs.org/" + pkg.replace("/", "%2f")
    try:
        with OPENER.open(url, timeout=20) as r:
            return json.load(r).get("dist-tags", {}).get("latest")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return f"ERR {type(e).__name__}"


def skillhub_latest(handle: str, slug: str) -> str | None:
    """SkillHub 的 /api/v1/* 只接受 POST，这里直接读页面内嵌的 __SSG_DATA__。"""
    url = f"https://skillhub.cn/skills/{handle}/{slug}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with OPENER.open(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        found = re.findall(r'"changelog":"([^"]*)","createdAt":\d+,"version":"([\d.]+)"', html)
        return next((v for c, v in found if c), found[0][1] if found else None)
    except Exception as e:  # noqa: BLE001 - 探测脚本任一来源挂掉都不该中断其余检查
        return f"ERR {type(e).__name__}"


def main() -> int:
    sources = json.loads(MANIFEST.read_text(encoding="utf-8"))["sources"]
    stale = 0
    for s in sources:
        pkg, pinned = s.get("package"), s.get("package_latest")
        if not pkg:
            continue
        live = npm_latest(pkg)
        flag = "OK" if live == pinned else "有新版本"
        stale += flag != "OK"
        print(f"[{flag:9}] {s['name']:34} {pkg:34} 清单={pinned}  registry={live}")

    handle, slug = "user_fe933096", "meituan-travel"
    live = skillhub_latest(handle, slug)
    print(f"[{'OK' if live == '1.0.7' else '有新版本':9}] {slug:34} {'skillhub':34} 清单=1.0.7  平台={live}")
    print(f"\n共 {stale} 项需要关注。更新后请同步改 sources.json 的 package_latest 与 sha256。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
