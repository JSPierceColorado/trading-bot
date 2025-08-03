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
APCA_API_BASE_URL = os.getenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
DIVIDEND_SYMBOL = "VIG"
MIN_VIG_BUY = 1.00  # Only submit VIG buy order if proceeds ≥ $1

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
    try:
        top_pick_idx = header.index("TopPick")
        bullish_idx = header.index("Bullish Signal")
        ticker_idx = header.index("Ticker")
        price_idx = header.index("Price")
    except Exception:
        return []

    picks = []
    for row in rows[1:]:
        try:
            top_pick = row[top_pick_idx].strip() if row[top_pick_idx] else ""
            bullish = row[bullish_idx].strip() if row[bullish_idx] else ""
            ticker = row[ticker_idx].strip() if row[ticker_idx] else ""
            price_raw = row[price_idx].strip() if row[price_idx] else ""
            price = float(price_raw) if price_raw else None
            if top_pick.upper().startswith("TOP") and bullish == "✅":
                picks.append({"ticker": ticker, "price": price})
        except Exception:
            continue
    return picks

def submit_order(api, symbol, notional=None, qty=None, side='buy'):
    try:
        if side == 'buy':
            order = api.submit_order(
                symbol=symbol,
                notional=notional,
                side='buy',
                type='market',
                time_in_force='day'
            )
        elif side == 'sell':
            order = api.submit_order(
                symbol=symbol,
                qty=qty,
                side='sell',
                type='market',
                time_in_force='day'
            )
        else:
            raise ValueError("Invalid order side")
        return order.id, True, ""
    except Exception as e:
        return None, False, str(e)

def has_open_buy_order(api, symbol):
    open_orders = api.list_orders(status='open', symbols=[symbol])
    for order in open_orders:
        if order.side == 'buy':
            return True
    return False

def get_vig_funds(log_ws):
    # Looks for "VIG_FUNDS" in column 1 and returns its value
    try:
        records = log_ws.get_all_values()
        for row in records:
            if len(row) >= 2 and row[0] == "VIG_FUNDS":
                return float(row[1])
    except Exception:
        pass
    return 0.0

def set_vig_funds(log_ws, funds):
    # Upsert "VIG_FUNDS" in first matching row, or append if not found
    records = log_ws.get_all_values()
    for idx, row in enumerate(records):
        if len(row) >= 2 and row[0] == "VIG_FUNDS":
            log_ws.update_cell(idx+1, 2, funds)
            return
    # If not found, append at end
    log_ws.append_row(["VIG_FUNDS", funds], value_input_option="USER_ENTERED")

def check_and_sell_positions(api, log_ws, target_profit=0.05):
    print("🔎 Checking open positions for ≥5% gains...")
    positions = api.list_positions()
    cumulative_profits = get_vig_funds(log_ws)
    for pos in positions:
        symbol = pos.symbol
        qty = float(pos.qty)
        avg_entry = float(pos.avg_entry_price)
        current_price = float(pos.current_price)
        if symbol == DIVIDEND_SYMBOL:
            continue  # Never sell VIG
        if qty <= 0:
            continue
        gain = (current_price - avg_entry) / avg_entry
        if gain >= target_profit:
            print(f"💰 Selling {qty} shares of {symbol} at {current_price:.2f} (+{gain*100:.2f}%)")
            order_id, success, error = submit_order(api, symbol, qty=qty, side='sell')
            now = datetime.now().isoformat(timespec="seconds")
            log_row = [
                now, symbol, "sell", "", current_price,
                order_id if order_id else "",
                "success" if success else "fail",
                error
            ]
            log_trade(log_ws, log_row)
            # Add proceeds to cumulative profits if successful
            if success:
                proceeds = round(qty * current_price, 2)
                cumulative_profits += proceeds
            print(f"   ↳ {'✅' if success else '❌'} Sell order {'submitted' if success else 'failed'}: {order_id if order_id else error}")
            time.sleep(2)
    # After loop: if cumulative profits > $1, invest in VIG
    if cumulative_profits >= MIN_VIG_BUY:
        if not has_open_buy_order(api, DIVIDEND_SYMBOL):
            print(f"🏦 Reinvesting ${cumulative_profits:.2f} into {DIVIDEND_SYMBOL}")
            vig_order_id, vig_success, vig_error = submit_order(
                api, DIVIDEND_SYMBOL, notional=round(cumulative_profits, 2), side='buy'
            )
            now2 = datetime.now().isoformat(timespec="seconds")
            log_row2 = [
                now2, DIVIDEND_SYMBOL, "buy", round(cumulative_profits, 2), "",
                vig_order_id if vig_order_id else "",
                "success" if vig_success else "fail",
                vig_error
            ]
            log_trade(log_ws, log_row2)
            if vig_success:
                cumulative_profits = 0.0  # Reset on success
            print(f"   ↳ {'✅' if vig_success else '❌'} VIG buy {'submitted' if vig_success else 'failed'}: {vig_order_id if vig_order_id else vig_error}")
        else:
            print(f"⚠️ Skipped VIG buy: outstanding buy order exists.")
    set_vig_funds(log_ws, round(cumulative_profits, 2))

def main():
    print("🚦 Starting trading bot...")

    gc = get_google_client()
    screener_ws = gc.open(SHEET_NAME).worksheet(SCREENER_TAB)
    log_ws = gc.open(SHEET_NAME).worksheet(LOG_TAB)

    api = REST(APCA_API_KEY_ID, APCA_API_SECRET_KEY, APCA_API_BASE_URL, api_version='v2')
    buying_power = get_buying_power(api)
    print(f"💵 Buying power: {buying_power:.2f}")

    # First, sell positions with ≥5% gain and reinvest in VIG if proceeds ≥ $1
    check_and_sell_positions(api, log_ws, target_profit=0.05)

    picks = get_toppicks_with_signal(screener_ws)
    print(f"🟢 Found {len(picks)} eligible Top Picks with Bullish Signal.")

    for pick in picks:
        symbol = pick["ticker"]
        if not symbol or symbol == DIVIDEND_SYMBOL:
            continue  # Don't auto-buy VIG in screener loop
        price = pick["price"]
        notional = round(0.05 * buying_power, 2) if buying_power else None

        if notional is None or notional < 1:
            print(f"⚠️ Skipping {symbol}: invalid notional or price.")
            continue

        # Check if position already exists
        try:
            position = api.get_position(symbol)
            if float(position.qty) > 0:
                print(f"⚠️ Skipping {symbol}: already held in portfolio.")
                continue
        except Exception:
            pass  # No position, safe to trade

        # Check if there is an outstanding buy order for this symbol
        if has_open_buy_order(api, symbol):
            print(f"⚠️ Skipping {symbol}: outstanding buy order exists.")
            continue

        print(f"🛒 Submitting order for {symbol} at ${notional} notional (market order)")
        order_id, success, error = submit_order(api, symbol, notional=notional, side='buy')
        now = datetime.now().isoformat(timespec="seconds")
        log_row = [
            now, symbol, "buy", notional, price if price else "",
            order_id if order_id else "",
            "success" if success else "fail",
            error
        ]
        log_trade(log_ws, log_row)
        print(f"   ↳ {'✅' if success else '❌'} Buy order {'submitted' if success else 'failed'}: {order_id if order_id else error}")
        time.sleep(2)

    print("✅ Done submitting orders!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("❌ Fatal error:", e)
        traceback.print_exc()
