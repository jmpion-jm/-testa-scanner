import openpyxl
import json

with open("config.json", encoding="utf-8") as f:
    cfg = json.load(f)

stocks = cfg["stocks"]

# NY/ND 거래소 매핑 (영웅문글로벌 코드 형식)
exchange_map = {
    "LHX":"NY","ASTS":"ND","NVDA":"ND","IONQ":"ND","AVGO":"ND","MRVL":"ND",
    "AMD":"ND","ALAB":"ND","NTAP":"ND","VRT":"NY","ON":"NY","NVTS":"ND",
    "APH":"NY","TDY":"NY","COHR":"ND","LITE":"ND","GLW":"NY","AAOI":"ND",
    "RTX":"NY","ATI":"NY","CRS":"NY","LDOS":"NY","BWXT":"NY","PLUG":"ND",
    "RKLB":"ND","LIN":"NY","CCJ":"NY","TSLA":"ND","PSTG":"NY","ORCL":"NY",
    "RGTI":"ND","FCX":"NY","OII":"NY","QS":"ND","OKLO":"ND","TEM":"ND",
    "CRWD":"ND","PLTR":"ND","DOCS":"ND","CRSP":"ND","ALNY":"ND","SMR":"NY",
    "FTI":"NY","WDC":"ND","SNDK":"ND","MU":"ND","CBRS":"ND","QBTS":"NY",
    "LEU":"ND","ADI":"ND","CEVA":"ND","MOG":"NY","MP":"NY","RMBS":"ND",
    "ADBE":"ND","SHOP":"NY",
}

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "월봉MA10"

# 헤더
ws.append(["종목명", "종목코드", "메모"])

for ticker, (name, theme) in stocks.items():
    exch = exchange_map.get(ticker, "NY")
    # MOG는 MOGa (클래스A)
    code_suffix = "MOGa" if ticker == "MOG" else ticker
    code = f"{exch}{code_suffix}"
    ws.append([name, code, theme])

fname = "월봉MA10_관심종목.xlsx"
wb.save(fname)
print(f"저장 완료: {fname}")
print(f"총 {ws.max_row - 1}개 종목")
