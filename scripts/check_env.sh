#!/usr/bin/env bash
# 報備支援小幫手：檢查執行環境。缺什麼就印出安裝方式。
# 用法：bash scripts/check_env.sh
missing=0
echo "== 必要 =="
if command -v python3 >/dev/null 2>&1; then
  v=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    echo "  OK    python3 $v"
  else
    echo "  缺少  python3 版本 $v 太舊，需要 3.8 以上。macOS：brew install python ／ Windows：到 python.org 下載"
    missing=1
  fi
else
  echo "  缺少  python3。macOS 內建或 brew install python ／ Windows：到 python.org 下載 3.8 以上"
  missing=1
fi
echo
echo "== 選配 =="
if command -v pdftotext >/dev/null 2>&1; then
  echo "  OK    pdftotext（申請書 PDF 驗收用）"
else
  echo "  沒有  pdftotext：申請書驗收會改用讀 PDF 工具轉成文字。想安裝：macOS：brew install poppler ／ Ubuntu：sudo apt install poppler-utils"
fi
echo
echo "== 使用者設定檔 =="
cfg="$HOME/.mohw-support-report/config.json"
if [ -f "$cfg" ]; then
  echo "  OK    $cfg"
else
  echo "  尚未建立：第一次使用時會進入首次設定"
fi
echo
echo "== 下列兩項無法用指令檢查，請 AI 看自己的工具清單確認 =="
echo "  瀏覽器操作工具（開網址、點擊、讀頁面）：填報備系統需要"
echo "  Google 日曆連接器：沒有的話改成貼班表"
echo
if [ "$missing" = 0 ]; then echo "必要項目齊備。"; else echo "有缺項，請先照上面的提示安裝。"; exit 1; fi
