#!/usr/bin/env python3
"""報備支援小幫手：用診所名稱或地址查醫事機構 10 碼代碼（只用 Python 3 標準函式庫，不需登入）。

用法：
  python3 lookup_clinic.py 晨光 --county 臺中市 --town 西屯區
  python3 lookup_clinic.py 晨光 皮膚科 --county 臺中市            多個關鍵字＝全部都要出現在名稱或地址
  python3 lookup_clinic.py 文心路 --county 臺中市 --town 西屯區     慣稱查不到時，改用路名查
  python3 lookup_clinic.py 晨光 --county 臺中市 --live            另外查健保署即時資料
  python3 lookup_clinic.py 晨光 --county 臺中市 --json            機器可讀輸出
  python3 lookup_clinic.py --refresh                              強制重抓快取

資料來源（皆為政府公開資料、免登入，只會送出查詢關鍵字）：
  A. 健保署「健保特約醫療院所名冊」CSV（政府資料開放平臺 資料集 168341，每日更新，約 4.7MB）
     只有健保特約機構，含已終止特約者（會標示終止日期）。
       https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D2100G-001
  B. 衛福部醫事司「醫療機構與人員基本資料」ODS（資料集 15393，約一年更新一次，約 2.2MB）
     含非健保特約的自費診所，但名稱與地址可能比現況舊（診所改名後這裡還是舊名）。
       https://www.mohw.gov.tw/dl-96581-66dbb751-f83a-416a-a998-893222e20fef.html
  C. --live：健保署特約醫事機構查詢網頁背後的即時資料（只含健保特約）
       POST https://info.nhi.gov.tw/api/inae1000/inae1000s01/SQL100
快取位置：~/.cache/nhi_lookup/（約 7MB）

注意：查到的結果是「候選」，一定要給使用者看名稱與地址確認，多筆時讓使用者挑，不可自己猜。
"""
import argparse, csv, io, json, os, sys, time, unicodedata, urllib.request, zipfile
import xml.etree.ElementTree as ET

CACHE = os.path.expanduser("~/.cache/nhi_lookup")
UA = {"User-Agent": "Mozilla/5.0"}
NHI_CSV_URL = "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D2100G-001"
MOHW_ODS_URL = "https://www.mohw.gov.tw/dl-96581-66dbb751-f83a-416a-a998-893222e20fef.html"
NHI_API = "https://info.nhi.gov.tw/api/inae1000/inae1000s01/"
NHI_TTL = 24 * 3600          # 健保署名冊每日更新
MOHW_TTL = 30 * 24 * 3600    # 醫事司每年更新，一個月重抓一次就夠


def norm(s):
    """全形轉半形、台/臺統一，方便比對（健保署地址是全形數字，醫事司是半形）。"""
    s = unicodedata.normalize("NFKC", s or "")
    for i, ch in enumerate("一二三四五六七八九", 1):
        s = s.replace(ch + "段", "%d段" % i)
    return s.replace("台", "臺").replace(" ", "").replace("　", "")


def fetch(url, data=None, headers=None, timeout=120):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def cached(name, url, ttl, refresh):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if refresh or not os.path.exists(p) or time.time() - os.path.getmtime(p) > ttl:
        sys.stderr.write("下載 %s ...\n" % name)
        blob = fetch(url)
        with open(p, "wb") as f:
            f.write(blob)
    return p


def load_nhi(refresh):
    p = cached("nhi_all.csv", NHI_CSV_URL, NHI_TTL, refresh)
    out = []
    with open(p, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            end = (r.get("終止合約或歇業日期") or "").strip()
            out.append({
                "code": r["醫事機構代碼"].strip(), "name": r["醫事機構名稱"].strip(),
                "addr": r["機構地址"].strip(), "kind": r["權屬別名稱"].strip(),
                "note": ("已終止特約/歇業 " + end) if end else "", "src": "健保署名冊",
            })
    return out


def load_mohw(refresh):
    p = cached("mohw_basic.ods", MOHW_ODS_URL, MOHW_TTL, refresh)
    ns = {"t": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
          "x": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
    T = "{%s}" % ns["t"]
    rows = []
    with zipfile.ZipFile(p) as z:
        for _, el in ET.iterparse(z.open("content.xml"), events=("end",)):
            if el.tag == T + "table-row":
                r = []
                for c in el:
                    rep = int(c.get(T + "number-columns-repeated", "1"))
                    txt = "".join("".join(x.itertext()) for x in c.findall("x:p", ns))
                    r += [txt] * (rep if rep <= 50 else 1)
                rows.append(r)
                el.clear()
    out = []
    for r in rows[1:]:
        if len(r) < 6 or not r[0].strip():
            continue
        out.append({"code": r[0].strip(), "name": r[1].strip(), "addr": r[4].strip(),
                    "kind": r[5].strip().rstrip(","), "note": "", "src": "醫事司基本資料"})
    return out


def live_nhi(name, county, town):
    """健保署即時查詢 API。C_AreaCod=縣市代碼(例 33)，C_AreaName=鄉鎮代碼(例 3305)。
    C_SrvDay/C_SrvTime 必須帶 selAll/012，否則一律回 0 筆（實測）。"""
    def post(path, obj):
        return json.loads(fetch(NHI_API + path, json.dumps(obj).encode(),
                                {"Content-Type": "application/json"}, 30))
    area, area_name = "", ""
    if county:
        meta = json.loads(fetch(NHI_API + "SQL001", timeout=30))
        for it in meta["getAreaCoditem"]:
            if norm(it["text"]) == norm(county):
                area = it["value"]
        if area and town:
            for it in post("SQL002", {"C_AreaNo": area}):
                if norm(it["text"]).endswith(norm(town)):
                    area_name = it["value"]
    body = {k: "" for k in ["C_FuncType", "C_HospID", "C_Type", "C_BranchCode", "C_HospAddr",
            "C_HospCntType", "C_NoneObs", "C_Pre", "C_SrvList", "C_HospAlliance", "C_HospFlu",
            "C_Plans", "C_LongHoliday", "C_LongHolidayTime"]}
    body.update({"C_HospName": name, "C_AreaCod": area, "C_AreaName": area_name,
                 "C_SrvDay": "selAll", "C_SrvTime": "012", "C_ShowType": "1",
                 "C_Str": "x", "pageDataCount": 50, "nowPage": 1})
    d = post("SQL100", body)
    return [{"code": x["hosP_ID"], "name": x["hosP_NAME"], "addr": x["hosP_ADDR"],
             "kind": x["hosP_CNT_TYPE"], "note": "", "src": "健保署即時API"} for x in d["data"]]


def main():
    ap = argparse.ArgumentParser(description="查醫事機構 10 碼代碼")
    ap.add_argument("keywords", nargs="*", help="診所名稱關鍵字（多個＝全部都要出現在名稱或地址）")
    ap.add_argument("--county", help="縣市，例：新竹縣")
    ap.add_argument("--town", help="鄉鎮市區，例：竹北市")
    ap.add_argument("--live", action="store_true", help="另外打健保署即時 API（只含健保特約）")
    ap.add_argument("--refresh", action="store_true", help="強制重抓快取")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    a = ap.parse_args()
    if not a.keywords and not a.refresh:
        ap.error("至少給一個關鍵字")

    recs = load_nhi(a.refresh) + load_mohw(a.refresh)
    if a.live and a.keywords:
        try:
            recs += live_nhi(a.keywords[0], a.county, a.town)
        except Exception as e:
            sys.stderr.write("即時API失敗：%s\n" % e)
    kws = [norm(k) for k in a.keywords]
    area = norm((a.county or "") + (a.town or ""))
    merged = {}
    for r in recs:
        hay = norm(r["name"] + r["addr"])
        if not all(k in hay for k in kws):
            continue
        if area:
            addr = norm(r["addr"])
            if (a.county and not addr.startswith(norm(a.county))) or \
               (a.town and norm(a.town) not in addr[:12]):
                continue
        m = merged.setdefault(r["code"], {**r, "srcs": []})
        if r["src"] not in m["srcs"]:
            m["srcs"].append(r["src"])
        if r["note"] and not m["note"]:
            m["note"] = r["note"]
    rows = sorted(merged.values(), key=lambda m: (bool(m["note"]), m["code"]))
    if a.json:
        print(json.dumps([{k: m[k] for k in ("code", "name", "addr", "kind", "note", "srcs")} for m in rows],
                         ensure_ascii=False, indent=1))
        return
    print("共 %d 筆候選" % len(rows))
    for m in rows:
        print("%s | %s | %s | %s | 來源:%s%s" % (m["code"], m["name"], m["addr"], m["kind"],
              "+".join(m["srcs"]), (" | " + m["note"]) if m["note"] else ""))
    if not rows:
        print("查不到。可以：改用正式名稱、改用路名查、拿掉鄉鎮只留縣市；"
              "仍查不到就等使用者登入報備系統後，用系統內的被支援機構搜尋。")
    elif all(m["srcs"] == ["醫事司基本資料"] for m in rows):
        print("提醒：這些結果只出現在醫事司年度資料，名稱可能是舊名，請使用者核對地址。")

if __name__ == "__main__":
    main()
