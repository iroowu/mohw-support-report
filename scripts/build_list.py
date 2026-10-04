#!/usr/bin/env python3
"""報備支援小幫手：把日曆事件整理成「申報清單」（只用 Python 標準函式庫）。

輸入的事件檔可以是：
  1. Google 日曆連接器 list_events 的原始回應（含 "events" 清單），直接存成 JSON 即可。
  2. 簡化格式：[{"summary": "晨光", "start": "2026-11-02T14:00", "end": "2026-11-02T20:30"}, ...]
     沒寫時區的時間一律視為台灣時間。使用者貼班表文字或截圖時，先轉成這個格式。

用法：
  python3 build_list.py events.json --month 2026-11 --out plan.json
  python3 build_list.py events.json --from 2026-11-01 --to 2026-11-30 --config 其他設定.json

輸出：
  - 螢幕上印出 Markdown 清單（給使用者確認用）。
  - --out 指定的 plan.json：每一段申報時段一筆，之後填表與 verify_filing.py 驗收都讀它。
    使用者確認時決定不報的段，把該筆的 "status" 改成 "user_excluded"，不要直接刪掉。
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TW = timezone(timedelta(hours=8))
WEEKDAY = "一二三四五六日"
DEFAULT_CONFIG = Path.home() / ".mohw-support-report" / "config.json"


def parse_dt(v):
    """回傳 (datetime 或 None, 是否全天)。"""
    if isinstance(v, dict):
        if v.get("dateTime"):
            v = v["dateTime"]
        elif v.get("date"):
            return datetime.fromisoformat(v["date"][:10]).replace(tzinfo=TW), True
        else:
            return None, False
    if not v:
        return None, False
    s = str(v).replace("Z", "+00:00")
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=TW), True
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TW)
    return dt.astimezone(TW), False


def to_min(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def to_hhmm(m):
    return f"{m // 60:02d}{m % 60:02d}"


def subtract_breaks(seg, breaks):
    parts = [seg]
    for b0, b1 in breaks:
        b0, b1 = to_min(b0), to_min(b1)
        nxt = []
        for s, e in parts:
            if b1 <= s or b0 >= e:
                nxt.append((s, e))
                continue
            if s < b0:
                nxt.append((s, b0))
            if b1 < e:
                nxt.append((b1, e))
        parts = nxt
    return [(s, e) for s, e in parts if e > s]


def load_events(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("events") or data.get("items") or []
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("events", help="事件 JSON 檔")
    ap.add_argument("--month", help="申報月份，例如 2026-11")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", help="輸出 plan.json 的路徑")
    ap.add_argument("--user-excluded", action="append", default=[], metavar="機構代碼:YYYY-MM-DD",
                    help="使用者確認時決定這一天不報（可重複）")
    ap.add_argument("--already-filed", action="append", default=[], metavar="機構代碼:YYYY-MM-DD",
                    help="這一天已經在先前的案件報過，這次不重報（可重複）")
    a = ap.parse_args()

    if not Path(a.config).exists():
        sys.exit(f"找不到設定檔 {a.config}，請先跑首次設定。")
    cfg = json.load(open(a.config, encoding="utf-8"))
    rnd = int(cfg.get("round_minutes", 30))
    skip_kw = cfg.get("skip_keywords", ["請假", "取消", "停診"])
    clinics = cfg["clinics"]

    if a.month:
        y, m = map(int, a.month.split("-"))
        d_from = date(y, m, 1)
        d_to = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    elif a.date_from and a.date_to:
        d_from, d_to = date.fromisoformat(a.date_from), date.fromisoformat(a.date_to)
    else:
        sys.exit("請用 --month 或 --from/--to 指定申報期間。")

    items, skipped, review, others = [], [], [], []
    for ev in load_events(a.events):
        title = (ev.get("summary") or "").strip()
        st, allday = parse_dt(ev.get("start"))
        en, _ = parse_dt(ev.get("end"))
        if st is None:
            continue
        d = st.date()
        if not (d_from <= d <= d_to):
            continue
        hits = [c for c in clinics if any(k in title for k in c["keywords"])]
        raw = "全天" if allday else f"{st:%H:%M}～{en:%H:%M}"
        base = {"date": d.isoformat(), "weekday": WEEKDAY[d.weekday()], "title": title, "calendar_time": raw}
        if not hits:
            others.append(base)
            continue
        if len(hits) > 1:
            review.append({**base, "reason": "標題同時符合 " + "、".join(c["alias"] for c in hits)})
            continue
        c = hits[0]
        base.update(code=c["code"], alias=c["alias"], official_name=c["official_name"])
        if ev.get("status") == "cancelled":
            skipped.append({**base, "reason": "行事曆事件已取消"})
            continue
        sk = [k for k in skip_kw if k in title]
        if sk:
            skipped.append({**base, "reason": "標題含「" + "、".join(sk) + "」"})
            continue
        if allday:
            review.append({**base, "reason": "全天事件沒有起訖時間，請告訴我實際上診時間"})
            continue
        if en.date() != d:
            review.append({**base, "reason": "事件跨過午夜，請確認要怎麼報"})
            continue
        notes = []
        var = next((v for v in c.get("variants") or [] if v["keyword"] in title), None)
        if var:
            s, e = to_min(var["start"]), to_min(var["end"])
            notes.append(f"依設定「{var['keyword']}」固定報 {var['start']}~{var['end']}")
        else:
            s0, e0 = st.hour * 60 + st.minute, en.hour * 60 + en.minute
            s, e = s0 // rnd * rnd, -(-e0 // rnd) * rnd
            if (s, e) != (s0, e0):
                notes.append(f"系統只能選 {rnd} 分鐘一格，已把時間往外取整")
        segs = subtract_breaks((s, e), c.get("breaks") or [])
        if len(segs) > 1:
            notes.append("依設定扣除休息時段，分段申報")
        for s, e in segs:
            items.append({**base, "id": f"{c['code']}-{d.isoformat()}-{to_hhmm(s)}", "start": to_hhmm(s),
                          "end": to_hhmm(e), "status": "planned", "note": "；".join(notes)})

    items.sort(key=lambda x: (x["date"], x["start"]))
    marks = {}
    for status, lst in (("user_excluded", a.user_excluded), ("already_filed", a.already_filed)):
        for m in lst:
            code, _, d = m.partition(":")
            hit = [x for x in items if x["code"] == code and x["date"] == d]
            if not hit:
                sys.exit(f"{m} 在清單裡找不到，請確認機構代碼與日期")
            for x in hit:
                x["status"] = status
                x["note"] = (x["note"] + "；" if x["note"] else "") + ("使用者決定不報" if status == "user_excluded" else "先前案件已報，這次不重報")
    for lst in (skipped, review, others):
        lst.sort(key=lambda x: (x["date"], x["calendar_time"]))
    # 事前檢查：清單內不同診所同一天時段重疊
    overlaps = []
    todo = [x for x in items if x["status"] == "planned"]
    for i, x in enumerate(todo):
        for y in todo[i + 1:]:
            if y["date"] != x["date"]:
                break
            if x["code"] != y["code"] and to_min(y["start"]) < to_min(x["end"]) and to_min(x["start"]) < to_min(y["end"]):
                overlaps.append(f"{x['date']} {x['alias']} {x['start']}~{x['end']} 與 {y['alias']} {y['start']}~{y['end']} 重疊")

    title = a.month or f"{d_from}～{d_to}"
    print(f"# 申報清單（{title}）\n")
    for c in clinics:
        rows = [x for x in items if x["code"] == c["code"]]
        if not rows:
            continue
        days = len({x["date"] for x in rows})
        print(f"## {c['alias']}（系統正式名稱 {c['official_name']}｜機構代碼 {c['code']}｜{c['county']} {c['town']}）")
        print(f"{days} 天、{len(rows)} 段\n")
        print("| 日期 | 星期 | 行事曆原始時間 | 建議申報時段 | 說明 |")
        print("|---|---|---|---|---|")
        for x in rows:
            print(f"| {x['date'][5:].replace('-', '/')} | {x['weekday']} | {x['calendar_time']} | {x['start']}~{x['end']} | {x['note']} |")
        print()
    if skipped:
        print("## 不申報（行事曆標示請假或取消）\n")
        for x in skipped:
            print(f"- {x['date']}（{x['weekday']}）{x['title']} {x['calendar_time']}：{x['reason']}")
        print()
    if review:
        print("## 需要您確認\n")
        for x in review:
            print(f"- {x['date']}（{x['weekday']}）{x['title']} {x['calendar_time']}：{x['reason']}")
        print()
    if overlaps:
        print("## 警告：清單內有不同診所時段重疊\n")
        for o in overlaps:
            print("- " + o)
        print()
    if others:
        print("## 其他行程（沒對到任何診所，不申報；若其中有上診班，請告訴我加進設定）\n")
        for x in others:
            print(f"- {x['date']}（{x['weekday']}）{x['title']} {x['calendar_time']}")
        print()
    n_clinic = len({x["code"] for x in todo})
    print(f"這次要申報：{n_clinic} 家診所、{len({(x['code'], x['date']) for x in todo})} 個診次、{len(todo)} 段時間。")
    if len(todo) != len(items):
        print(f"（另有 {len(items) - len(todo)} 段標為不報或先前已報，說明欄有寫）")

    if a.out:
        plan = {"period": {"from": d_from.isoformat(), "to": d_to.isoformat()}, "generated_at": datetime.now(TW).isoformat(timespec="seconds"),
                "items": items, "skipped": skipped, "needs_review": review, "overlaps": overlaps, "others": others}
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(f"\n已存：{a.out}")


if __name__ == "__main__":
    main()
