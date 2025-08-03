import os
import json
import time
import gspread
from alpaca_trade_api.rest import REST
from datetime import datetime

SHEET_NAME = "Trading Log"
SCREENER_TAB = "screener"
LOG_TAB = "log"

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
    print("DEBUG: Sheet header:", header)
    try:
        top_pick_idx = header.index("TopPick")
        bullish_idx = header.index("Bullish Signal")
        ticker_idx = header.index("Ticker")
        price_idx = header.index("Price")
    except Exception as e:
        print("❌ Column error:", e)
        return []

    picks = []
    for i, row in enumerate(rows[1:], 2):
        print(f"DEBUG: Row {i}: {row}")
        try:
            top_pick = row[top_pick_idx].strip() if row[top_pick_idx] else ""
            bullish = row[bullish_idx].strip() if row[bullish_idx] else ""
            ticker = row[ticker_idx].strip() if row[ticker_idx] else ""
            price_raw = row[price_idx].strip() if row[price_idx] else ""
            try:
                price = float(price_raw)
            except Exception:
                price = None
            print(f"  ↳ Checking: TopPick='{top_pick}', Bullish Signal='{bullish}', Ticker='{ticker}', Price='{price}'")
            if top_pick.upper().startswith("TOP") and bullish == "✅":
                picks.append({"ticker": ticker, "price": price})
                print(f"    ✔ Eligible: {ticker} at {price}")
        except Exception as e:
            print(f"❌ Row {i} error: {e}")
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

    # Get eligible trades (with extra debug)
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
        time.sleep(2)

    print("✅ Done submitting orders!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("❌ Fatal error:", e)
        traceback.print_exc()
