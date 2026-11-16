import urllib.request
import json

def test():
    res = urllib.request.urlopen("http://localhost:8088/api/data")
    data = json.loads(res.read().decode("utf-8"))
    print("=== LIVE DASHBOARD API VERIFICATION ===")
    print(f"Timestamp: {data.get('timestamp')}")
    print(f"Account: Login {data['account']['login']} | Equity: ${data['account']['equity']:.2f} | Balance: ${data['account']['balance']:.2f}")
    print(f"Positions: {len(data['positions'])} open")
    print(f"Universe 28 Radar count: {len(data['radar_28'])}")
    print(f"All 90 MT5 Radar count: {len(data['radar_all'])}")
    print(f"Next 4H Bar Close in: {data.get('next_4h_close_sec', 0)} seconds")
    print("\n--- TOP 10 CLOSEST APPROACHING ASSETS TO FIBONACCI SHELVES (EMA 89/233) ---")
    for x in data['radar_all'][:10]:
        print(f"  {x['symbol']:<10} | Clus: {x['cluster']:<12} | Price: {x['price']:<10} | Nearest: {x['nearest_shelf']} ({x['shelf_price']}) | Dist: {x['dist_atr']} ATR ({x['status']}) | Regime: {x['trend_regime']} | Signal: {x['signal_hint']}")

if __name__ == "__main__":
    test()
