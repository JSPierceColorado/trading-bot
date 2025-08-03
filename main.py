import os
import json
import time
import gspread
from alpaca_trade_api.rest import REST, TimeFrame
from datetime import datetime

SHEET_NAME = "Trading Log"
SCREENER_TAB = "screener"
LOG_TAB = "log"

# Alpaca credentials (should be set as environment variables)
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID")
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
APCA_API_BASE_URL = os.getenv("APCA_API_BASE_URL", "https://api.alpaca.markets")  # live by default

def get_google_client():
    creds = json.loads(os.getenv("GOOGLE_CREDS_JSON"))
    return gspread.service_account_from_dict(creds)

def log_trade(ws, entry):
    ws.append_row(entry, value_input_option="USER_ENTERED")

def get_buying_power(api):
    account = api.get_account()
    return float(account.buying_power)

def get_toppicks_with_signal(ws):
    rows = ws.get_all_values()
    header = rows[0]
    top_pick_idx = header.index("TopPick") if "TopPick" in header else None
    bullish_idx = header.index("Bullish Signal") if "Bullish Signal" in header else None
    ticker_idx = header.index("Ticker") if "Ticker" in header else None
    price_idx = header.index("Price") if "Price" in header else None

    # Find all that are both TopPick and have bullish signal
    picks = []
    for row in rows[1:]:
        if not (top_pick_idx and bullish_idx and ticker_idx and price_idx):
            continue
        if row[top_pick_idx].strip().upper().startswith("TOP") and row[bullish_idx].strip() == "✅":
            ticker = row[ticker_idx].strip()
            try:
                price = float(row[price_idx].strip())
            except Exception:
                price = None
            picks.append({"ticker": ticker, "price": price})
    return picks

def submit_order(api, symbol, notional):
    try:
        order = api.submit_order(
            symbol=symbol,
            notional=notional,
            side='buy',
            type='market',
            time_in_force='day'
        )
        return order.id, True, ""
    except Exception as e:
        return None, False, str(e)

def main():
    print("🚦 Starting trading bot...")

    # Set up Google client & worksheet
    gc = get_google_client()
    screener_ws = gc.open(SHEET_NAME).worksheet(SCREENER_TAB)
    log_ws = gc.open(SHEET_NAME).worksheet(LOG_TAB)

    # Set up Alpaca
    api = REST(APCA_API_KEY_ID, APCA_API_SECRET_KEY, APCA_API_BASE_URL, api_version='v2')
    buying_power = get_buying_power(api)
    print(f"💵 Buying power: {buying_power:.2f}")

    # Get eligible trades
    picks = get_toppicks_with_signal(screener_ws)
    print(f"🟢 Found {len(picks)} eligible Top Picks with Bullish Signal.")

    # Place trades and log
    for pick in picks:
        symbol = pick["ticker"]
        price = pick["price"]
        notional = round(0.05 * buying_power, 2) if buying_power else None

        if not symbol or notional is None or notional < 1:
            print(f"⚠️ Skipping {symbol}: invalid notional or price.")
            continue

        print(f"🛒 Submitting order for {symbol} at ${notional} notional (market order)")
        order_id, success, error = submit_order(api, symbol, notional)
        now = datetime.now().isoformat(timespec="seconds")
        log_row = [
            now, symbol, "buy", notional, price if price else "",
            order_id if order_id else "",
            "success" if success else "fail",
            error
        ]
        log_trade(log_ws, log_row)
        print(f"   ↳ {'✅' if success else '❌'} Order {'submitted' if success else 'failed'}: {order_id if order_id else error}")
        # Optional: small delay between orders
        time.sleep(2)

    print("✅ Done submitting orders!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("❌ Fatal error:", e)
        traceback.print_exc()
