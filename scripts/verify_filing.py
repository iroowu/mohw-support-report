#!/usr/bin/env python3
"""報備支援小幫手：申請書 PDF 程式驗收（只用 Python 標準函式庫）。

做三件事，每件都印出 PASS/FAIL 明細：
  一、逐筆比對：本次申請書的每一段時間，與 plan.json 核可清單一一對應（多報、漏報、時間不符都會抓出來）。
  二、跨診所時段重疊：把本次申請書與「同月份其他仍有效的案件」合起來，找出同一天兩家診所時間重疊。
  三、反向查漏報：日曆整理出的每一段班，在全部有效案件裡都要找得到。

用法：
  python3 verify_filing.py 申請書.pdf --plan plan.json
  python3 verify_filing.py 申請書.pdf --plan plan.json --active 上個月送的.pdf --exclude 3500000000:2026-11-18:1000-1700
  python3 verify_filing.py 申請書.txt --plan plan.json        （沒有 pdftotext 時，先把 PDF 文字存成 .txt）
  python3 verify_filing.py 申請書.pdf                         （不給 plan，只列出申請書內容並做重疊檢查）

參數：
  --active   同一段期間其他仍有效的案件申請書（可多份）。只用來做第二、三項，不做逐筆比對。
  --exclude  已經撤案或註銷的段，格式 機構代碼:YYYY-MM-DD:HHMM-HHMM，可重複。
             舊申請書 PDF 不會因為撤案而改變，所以撤掉的段要在這裡告訴程式。
  --json     改輸出機器可讀的 JSON。

有任何 FAIL 時結束碼為 1。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SEG = re.compile(r"(\d{3})/(\d{2})/(\d{2})\s*\(.\)\s*(\d{4})\s*~\s*(\d{3})/(\d{2})/(\d{2})\s*\(.\)\s*(\d{4})")
CODE_LINE = re.compile(r"^\s*(\d{10})\s+(\S+)")


def pdf_text(path):
    p = Path(path)
    if p.suffix.lower() == ".txt":
        return p.read_text(encoding="utf-8")
    exe = shutil.which("pdftotext")
    if not exe:
        sys.exit("找不到 pdftotext。請安裝 poppler（macOS：brew install poppler），"
                 "或先用其他讀 PDF 的工具把文字存成 .txt 再傳進來。")
    out = subprocess.run([exe, "-layout", str(p), "-"], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"pdftotext 讀取失敗：{out.stderr.strip()}")
    return out.stdout


def parse(path):
    """回傳 {'case_no','category','segments':[{code,name,date,start,end}]}。"""
    text = pdf_text(path)
    case = re.search(r"案件編號[:：]\s*(\d+)", text)
    cat = re.search(r"申請類別[:：]\s*(\S+)", text)
    segs, code, name = [], None, None
    # 以「支援機構代號」表頭切塊，每塊一家被支援機構；跨頁時沿用上一家
    blocks = re.split(r"支援機構代號", text)
    for blk in blocks[1:]:
        lines = blk.splitlines()
        for ln in lines[1:8]:
            m = CODE_LINE.match(ln)
            if m:
                code, name = m.group(1), m.group(2)
                break
        for m in SEG.finditer(blk):
            y1, mo1, d1, t1, y2, mo2, d2, t2 = m.groups()
            if (y1, mo1, d1) != (y2, mo2, d2):
                print(f"提醒：{path} 有跨日的時段 {m.group(0)}，只以開始日期計", file=sys.stderr)
            segs.append({"code": code, "name": name, "date": f"{int(y1) + 1911}-{mo1}-{d1}",
                         "start": t1, "end": t2, "source": Path(path).name})
    if not segs:
        sys.exit(f"{path} 裡找不到任何報備時段，請確認這是「醫事人員報備支援申請書」。")
    return {"file": str(path), "case_no": case.group(1) if case else "", "category": cat.group(1) if cat else "",
            "segments": segs}


def key(s):
    return (s["code"], s["date"], s["start"], s["end"])


def m(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("filing", nargs="+", help="本次送審的申請書 PDF（或 .txt），可多份")
    ap.add_argument("--plan", help="build_list.py 產出、使用者確認過的 plan.json")
    ap.add_argument("--active", nargs="*", default=[], help="同期間其他仍有效的案件申請書")
    ap.add_argument("--exclude", action="append", default=[], help="已撤案的段 代碼:YYYY-MM-DD:HHMM-HHMM")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    results = {"pass": [], "fail": [], "info": []}
    P = lambda s: results["pass"].append(s)
    F = lambda s: results["fail"].append(s)
    I = lambda s: results["info"].append(s)

    current = [parse(p) for p in a.filing]
    active = [parse(p) for p in a.active]
    excluded = set()
    for x in a.exclude:
        try:
            code, d, rng = x.split(":")
            s, e = rng.split("-")
            excluded.add((code, d, s, e))
        except ValueError:
            sys.exit(f"--exclude 格式不對：{x}（要像 3500000000:2026-11-18:1000-1700）")

    for c in current + active:
        I(f"讀到 {Path(c['file']).name}：案件編號 {c['case_no']}，申請類別 {c['category']}，{len(c['segments'])} 段")
        if c["category"] and c["category"] != "登錄":
            I(f"  注意：申請類別是「{c['category']}」，不是一般登錄案件，請確認這份要不要算進有效時段")

    cur_segs = [s for c in current for s in c["segments"]]
    all_segs = [s for s in cur_segs + [s for c in active for s in c["segments"]] if key(s) not in excluded]
    for k in excluded:
        if not any(key(s) == k for s in cur_segs + [s for c in active for s in c["segments"]]):
            I(f"--exclude {k} 在申請書裡找不到，請確認代碼與時間有沒有打錯")

    # 一、逐筆比對
    plan_items = []
    if a.plan:
        plan = json.load(open(a.plan, encoding="utf-8"))
        plan_items = plan.get("items", [])
        approved = [x for x in plan_items if x.get("status", "planned") == "planned"]
        for x in plan_items:
            if x.get("status") == "already_filed":
                I(f"[逐筆] {x.get('alias', x['code'])} {x['date']} {x['start']}~{x['end']}：先前案件已報，不在本次比對（反查時會確認）")
        want = {key(x): x for x in approved}
        got = {}
        for s in cur_segs:
            if key(s) in got:
                F(f"[逐筆] 重複申報：{s['code']} {s['date']} {s['start']}~{s['end']}")
            got[key(s)] = s
        for k, x in sorted(want.items(), key=lambda kv: (kv[0][1], kv[0][2])):
            label = f"{x.get('alias', x['code'])} {x['date']} {x['start']}~{x['end']}"
            if k in got:
                P(f"[逐筆] {label}")
            else:
                near = [s for s in cur_segs if s["code"] == x["code"] and s["date"] == x["date"]]
                if near:
                    F(f"[逐筆] {label} 時間不符，申請書寫的是 " + "、".join(f"{s['start']}~{s['end']}" for s in near))
                else:
                    F(f"[逐筆] {label} 漏報：申請書裡沒有這一段")
        for k, s in got.items():
            if k not in want:
                if not any(x["code"] == s["code"] and x["date"] == s["date"] for x in approved):
                    F(f"[逐筆] 多報：申請書有 {s['code']} {s['name']} {s['date']} {s['start']}~{s['end']}，核可清單沒有")
        I(f"逐筆比對：核可清單 {len(want)} 段，申請書 {len(got)} 段")
    else:
        I("沒有提供 --plan，略過逐筆比對；以下列出申請書內容供人工核對")
        for s in sorted(cur_segs, key=lambda s: (s["date"], s["start"])):
            I(f"  {s['date']} {s['start']}~{s['end']} {s['code']} {s['name']}")

    # 二、跨診所時段重疊
    segs = sorted(all_segs, key=lambda s: (s["date"], m(s["start"])))
    found = False
    for i, x in enumerate(segs):
        for y in segs[i + 1:]:
            if y["date"] != x["date"]:
                break
            if m(y["start"]) < m(x["end"]) and m(x["start"]) < m(y["end"]):
                found = True
                same = "同一家診所時段重疊" if x["code"] == y["code"] else "兩家診所時段重疊"
                F(f"[重疊] {x['date']} {same}：{x['name']} {x['start']}~{x['end']}（{x['source']}）"
                  f" 與 {y['name']} {y['start']}~{y['end']}（{y['source']}）")
    if not found:
        P(f"[重疊] {len(segs)} 段有效時段，沒有任何兩段重疊")

    # 三、反向查漏報
    if plan_items:
        miss = 0
        for x in plan_items:
            label = f"{x.get('alias', x['code'])} {x['date']} {x['start']}~{x['end']}"
            if x.get("status") == "user_excluded":
                I(f"[反查] {label}：使用者確認時決定不報，略過")
                continue
            cover = [s for s in all_segs if s["code"] == x["code"] and s["date"] == x["date"]
                     and m(s["start"]) <= m(x["start"]) and m(x["end"]) <= m(s["end"])]
            if not cover:
                miss += 1
                hint = "（標為先前已報，請用 --active 附上那份申請書）" if x.get("status") == "already_filed" else ""
                F(f"[反查] {label}：日曆有這段班，但所有有效案件裡都找不到{hint}")
        if not miss:
            n = len([x for x in plan_items if x.get("status") != "user_excluded"])
            P(f"[反查] 日曆整理出的 {n} 段班，在有效案件裡全部找得到")

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for s in results["info"]:
            print("說明  " + s)
        for s in results["pass"]:
            print("PASS  " + s)
        for s in results["fail"]:
            print("FAIL  " + s)
        print(f"\n總結：PASS {len(results['pass'])} 項、FAIL {len(results['fail'])} 項 → "
              + ("全部通過" if not results["fail"] else "有問題，請逐項處理"))
    sys.exit(1 if results["fail"] else 0)


if __name__ == "__main__":
    main()
