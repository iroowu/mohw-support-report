#!/usr/bin/env python3
"""報備支援小幫手：使用者設定檔工具（只用 Python 標準函式庫）。

設定檔固定存在使用者家目錄 ~/.mohw-support-report/config.json，
絕不放在 skill 資料夾內（skill 資料夾會被打包分享出去）。

用法：
  python3 config_tool.py path                 印出設定檔位置
  python3 config_tool.py show                 印出目前設定（含檢查結果）
  python3 config_tool.py validate [檔案]      檢查設定檔（預設檢查家目錄那份）
  python3 config_tool.py init --from 草稿.json  檢查草稿後寫入家目錄（已存在會先備份舊檔）
  python3 config_tool.py add-clinic --from 診所.json  在現有設定加一家診所
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".mohw-support-report"
CONFIG_PATH = CONFIG_DIR / "config.json"

HHMM = re.compile(r"^(?:[01]\d|2[0-3])[03]0$|^2400$")


def load(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check(cfg):
    """回傳 (錯誤清單, 提醒清單)。"""
    errors, warns = [], []
    if not isinstance(cfg, dict):
        return ["設定檔最外層必須是物件"], warns
    clinics = cfg.get("clinics")
    if not isinstance(clinics, list) or not clinics:
        errors.append("clinics 至少要有一家診所")
        clinics = []
    seen_kw, seen_code = {}, {}
    for i, c in enumerate(clinics, 1):
        tag = f"第 {i} 家（{c.get('alias', '未命名')}）"
        for key in ("alias", "keywords", "code", "official_name", "county", "town"):
            if not c.get(key):
                errors.append(f"{tag} 缺少 {key}")
        code = str(c.get("code", ""))
        if code and not re.fullmatch(r"\d{10}", code):
            errors.append(f"{tag} 機構代碼必須是 10 位數字，目前是「{code}」")
        if code in seen_code:
            warns.append(f"{tag} 與 {seen_code[code]} 用同一個機構代碼，請確認是否重複設定")
        seen_code[code] = tag
        kws = c.get("keywords") or []
        if not isinstance(kws, list):
            errors.append(f"{tag} keywords 必須是清單")
            kws = []
        for kw in kws:
            if len(kw) < 2:
                warns.append(f"{tag} 關鍵字「{kw}」只有一個字，容易誤判其他行程")
            for other_kw, other_tag in seen_kw.items():
                if other_tag != tag and (kw in other_kw or other_kw in kw):
                    errors.append(f"{tag} 的關鍵字「{kw}」與 {other_tag} 的「{other_kw}」互相包含，日曆事件會分不出是哪一家")
            seen_kw[kw] = tag
        for b in c.get("breaks") or []:
            if not (isinstance(b, list) and len(b) == 2 and all(HHMM.match(str(x)) for x in b) and b[0] < b[1]):
                errors.append(f"{tag} 休息時段 {b} 格式不對，要像 [\"1230\", \"1400\"]，且只能是整點或半點")
        for v in c.get("variants") or []:
            if not v.get("keyword"):
                errors.append(f"{tag} 的固定時段規則缺少 keyword")
            for k in ("start", "end"):
                if not HHMM.match(str(v.get(k, ""))):
                    errors.append(f"{tag} 固定時段「{v.get('keyword')}」的 {k} 要是整點或半點的四碼時間，例如 1000")
            if v.get("start", "") >= v.get("end", ""):
                errors.append(f"{tag} 固定時段「{v.get('keyword')}」開始時間要早於結束時間")
    if not isinstance(cfg.get("skip_keywords", []), list):
        errors.append("skip_keywords 必須是清單")
    rm = cfg.get("round_minutes", 30)
    if rm not in (30, 60):
        errors.append("round_minutes 只能是 30 或 60（報備系統是 30 分鐘一格）")
    if not cfg.get("calendar_name"):
        warns.append("calendar_name 沒填：每月整理清單時會改請您貼班表")
    if not cfg.get("archive_dir"):
        warns.append("archive_dir 沒填：送審後的申請書不會自動歸檔")
    return errors, warns


def report(cfg, path, label="設定檔"):
    errors, warns = check(cfg)
    print(f"{label}：{path}")
    for c in cfg.get("clinics", []):
        extra = []
        if c.get("breaks"):
            extra.append("扣除休息 " + "、".join(f"{a}~{b}" for a, b in c["breaks"]))
        for v in c.get("variants") or []:
            extra.append(f"標題含「{v.get('keyword')}」固定報 {v.get('start')}~{v.get('end')}")
        print(f"  {c.get('alias')}｜關鍵字 {'、'.join(c.get('keywords', []))}｜{c.get('code')} {c.get('official_name')}｜"
              f"{c.get('county')} {c.get('town')}" + ("｜" + "；".join(extra) if extra else ""))
    for w in warns:
        print("  提醒：" + w)
    for e in errors:
        print("  錯誤：" + e)
    print("檢查結果：" + ("PASS" if not errors else f"FAIL（{len(errors)} 項錯誤）"))
    return not errors


def write(cfg):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        bak = CONFIG_DIR / f"config.backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copy(CONFIG_PATH, bak)
        print(f"已備份舊設定：{bak}")
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_PATH)
    # 寫完回讀，確認內容一致
    if load() != cfg:
        sys.exit("寫入後回讀內容不一致，請重試")
    print(f"已寫入：{CONFIG_PATH}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("path")
    sub.add_parser("show")
    p = sub.add_parser("validate")
    p.add_argument("file", nargs="?")
    p = sub.add_parser("init")
    p.add_argument("--from", dest="src", required=True)
    p = sub.add_parser("add-clinic")
    p.add_argument("--from", dest="src", required=True)
    a = ap.parse_args()

    if a.cmd == "path":
        print(CONFIG_PATH)
        print("（存在）" if CONFIG_PATH.exists() else "（尚未建立，請先跑首次設定）")
        return
    if a.cmd in ("show", "validate"):
        path = Path(a.file) if getattr(a, "file", None) else CONFIG_PATH
        if not path.exists():
            sys.exit(f"找不到設定檔：{path}（請先跑首次設定）")
        sys.exit(0 if report(load(path), path) else 1)
    if a.cmd == "init":
        cfg = load(a.src)
        cfg.setdefault("version", 1)
        cfg.setdefault("round_minutes", 30)
        cfg.setdefault("skip_keywords", ["請假", "取消", "停診"])
        if not report(cfg, a.src, label="檢查草稿"):
            sys.exit("草稿有錯，未寫入。")
        write(cfg)
        return
    if a.cmd == "add-clinic":
        if not CONFIG_PATH.exists():
            sys.exit("還沒有設定檔，請先用 init 建立。")
        cfg = load()
        clinic = load(a.src)
        cfg.setdefault("clinics", []).append(clinic)
        if not report(cfg, CONFIG_PATH):
            sys.exit("加入後檢查有錯，未寫入。")
        write(cfg)


if __name__ == "__main__":
    main()
