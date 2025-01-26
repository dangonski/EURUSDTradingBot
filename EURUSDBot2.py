import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts
import time
import pandas as pd
import datetime
import sqlite3

# Replace with your Oanda account credentials
accountID = "your_account_id"  # Replace with your account ID
access_token = "your_access_token"  # Replace with your access token

# Initialize Oanda API client
client = oandapyV20.API(access_token=access_token)

# Define instruments
dxy_instrument = "USD_IDX"
eurus_instrument = "EUR_USD"

# --- Database functions ---
def create_database():
    conn = sqlite3.connect('trades.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            instrument TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            profit REAL,
            expectancy REAL
        )
    ''')
    conn.commit()
    conn.close()

def insert_trade(instrument, direction, entry_price, exit_price=None, profit=None, expectancy=None):
    conn = sqlite3.connect('trades.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trades (instrument, direction, entry_price, exit_price, profit, expectancy)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (instrument, direction, entry_price, exit_price, profit, expectancy))
    conn.commit()
    conn.close()

# --- Oanda API functions ---
def get_price(instrument):
    params = {"count": 1, "granularity": "M5"}  # Get latest 5-minute candle
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    return float(r.response['candles'][0]['mid']['c'])

def get_historical_prices(instrument, count=200, granularity="M5"):
    params = {"count": count, "granularity": granularity}
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    prices = []
    for candle in r.response['candles']:
        prices.append({
            "close": float(candle['mid']['c']),
            "open": float(candle['mid']['o']),
            "high": float(candle['mid']['h']),
            "low": float(candle['mid']['l'])
        })
    return prices

def calculate_sma(prices, period=20):
    if len(prices) < period:
        return None
    return sum([p['close'] for p in prices[-period:]]) / period

def calculate_atr(prices, period=14):
    if len(prices) < period:
        return None
    tr_values = []
    for i in range(1, len(prices)):
        tr = max(
            prices[i]['high'] - prices[i]['low'],
            abs(prices[i]['high'] - prices[i-1]['close']),
            abs(prices[i]['low'] - prices[i-1]['close'])
        )
        tr_values.append(tr)
    return sum(tr_values[-period:]) / period

def place_market_order(instrument, units, direction):
    data = {
        "order": {
            "units": units,
            "instrument": instrument,
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    if direction == "buy":
        data["order"]["side"] = "BUY"
    elif direction == "sell":
        data["order"]["side"] = "SELL"
    else:
        print("Invalid order direction")
        return

    r = orders.OrderCreate(accountID=accountID, data=data)
    client.request(r)
    print(f"Market order placed for {instrument} ({direction}): {units} units")

def get_eurus_position():
    r = accounts.AccountDetails(accountID=accountID)
    client.request(r)
    positions = r.response['account']['positions']
    for position in positions:
        if position['instrument'] == eurus_instrument:
            return int(position['long']['units'])  # Positive for long, negative for short
    return 0

def close_position(instrument, units):
    direction = "sell" if units > 0 else "buy"
    place_market_order(instrument, abs(units), direction)

# --- Backtesting and trading logic ---
def backtest(dxy_prices, eurus_prices):
    trades = []
    current_position = 0
    eurus_entry_price = 0
    for i in range(20, len(dxy_prices)):
        dxy_price = dxy_prices[i]['close']
        eurus_price = eurus_prices[i]['close']
        dxy_sma = calculate_sma(dxy_prices[:i+1])
        dxy_atr = calculate_atr(dxy_prices[:i+1])

        # Price action analysis (bullish engulfing)
        if (
            dxy_prices[i-1]['close'] < dxy_prices[i-1]['open']
            and dxy_price > dxy_prices[i-1]['open']
            and dxy_price > dxy_prices[i-1]['close']
        ):
            dxy_trend_up = True
        else:
            dxy_trend_up = False

        # ATR-based trailing stop-loss
        if current_position > 0:  # Long EUR_USD
            stop_loss = eurus_price - 2 * dxy_atr
            if eurus_price < stop_loss:
                trades.append({"entry": eurus_entry_price, "exit": stop_loss, "profit": stop_loss - eurus_entry_price})
                current_position = 0
        elif current_position < 0:  # Short EUR_USD
            stop_loss = eurus_price + 2 * dxy_atr
            if eurus_price > stop_loss:
                trades.append({"entry": eurus_entry_price, "exit": stop_loss, "profit": eurus_entry_price - stop_loss})
                current_position = 0

        # Combine SMA, price action, and ATR for trend confirmation
        if dxy_price > dxy_sma and dxy_trend_up and dxy_atr > 0.1:
            if current_position > 0:
                close_position(eurus_instrument, current_position)
            if current_position >= 0:
                place_market_order(eurus_instrument, 1000, "sell")
                eurus_entry_price = eurus_price
                current_position = -1000
        elif dxy_price < dxy_sma and not dxy_trend_up and dxy_atr > 0.1:
            if current_position < 0:
                close_position(eurus_instrument, current_position)
            if current_position <= 0:
                place_market_order(eurus_instrument, 1000, "buy")
                eurus_entry_price = eurus_price
                current_position = 1000

    # Calculate expectancy
    df = pd.DataFrame(trades)
    win_rate = df[df['profit'] > 0].shape[0] / len(df) if len(df) > 0 else 0
    avg_win = df[df['profit'] > 0]['profit'].mean() if len(df[df['profit'] > 0]) > 0 else 0
    avg_loss = df[df['profit'] < 0]['profit'].mean() if len(df[df['profit'] < 0]) > 0 else 0
    expectancy = win_rate * avg_win - (1 - win_rate) * abs(avg_loss)
    return expectancy

def run_strategy():
    # Get current time (EST)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5)))

    # Check if it's market open (adjust hours as needed)
    if now.hour == 18 and now.minute == 0 and now.weekday() == 6:  # Sunday 6:00 PM EST
        # Backtest
        dxy_historical_prices = get_historical_prices(dxy_instrument)
        eurus_historical_prices = get_historical_prices(eurus_instrument)
        expectancy = backtest(dxy_historical_prices, eurus_historical_prices)

        # Store expectancy in database
        insert_trade(eurus_instrument, "N/A", 0, expectancy=expectancy)

        if expectancy > 0:
            print(f"Positive expectancy ({expectancy:.4f}). Starting live trading...")
            # --- Live trading logic ---
            previous_dxy_price = get_price(dxy_instrument)
            while True:
                try:
                    # Get current prices and historical data
                    dxy_price = get_price(dxy_instrument)
                    eurus_price = get_price(eurus_instrument)
                    dxy_prices = get_historical_prices(dxy_instrument, count=20)

                    # Calculate 20-period SMA for DXY
                    dxy_sma = calculate_sma(dxy_prices)

                    # Get current EUR_USD position
                    current_position = get_eurus_position()

                    # Price action analysis (bullish engulfing)
                    if (
                        dxy_prices[-2]['close'] < dxy_prices[-2]['open']
                        and dxy_price > dxy_prices[-2]['open']
                        and dxy_price > dxy_prices[-2]['close']
                    ):
                        dxy_trend_up = True
                    else:
                        dxy_trend_up = False

                    # ATR-based trailing stop-loss
                    dxy_atr = calculate_atr(dxy_prices)
                    if current_position > 0:  # Long EUR_USD
                        stop_loss = eurus_price - 2 * dxy_atr
                        if eurus_price < stop_loss:
                            close_position(eurus_instrument, current_position)
                            profit = stop_loss - eurus_entry_price
                            insert_trade(eurus_instrument, "long", eurus_entry_price, stop_loss, profit, expectancy)
                            current_position = 0
                    elif current_position < 0:  # Short EUR_USD
                        stop_loss = eurus_price + 2 * dxy_atr
                        if eurus_price > stop_loss:
                            close_position(eurus_instrument, current_position)
                            profit = eurus_entry_price - stop_loss
                            insert_trade(eurus_instrument, "short", eurus_entry_price, stop_loss, profit, expectancy)
                            current_position = 0

                    # Combine SMA, price action, and ATR for trend confirmation
                    if dxy_price > dxy_sma and dxy_trend_up and dxy_atr > 0.1:
                        if current_position > 0:
                            close_position(eurus_instrument, current_position)
                        if current_position >= 0:
                            place_market_order(eurus_instrument, 1000, "sell")
                            eurus_entry_price = eurus_price
                            insert_trade(eurus_instrument, "short", eurus_entry_price, expectancy=expectancy)
                            current_position = -1000
                    elif dxy_price < dxy_sma and not dxy_trend_up and dxy_atr > 0.1:
                        if current_position < 0:
                            close_position(eurus_instrument, current_position)
                        if current_position <= 0:
                            place_market_order(eurus_instrument, 1000, "buy")
                            eurus_entry_price = eurus_price
                            insert_trade(eurus_instrument, "long", eurus_entry_price, expectancy=expectancy)
                            current_position = 1000

                    # Wait for the next interval
                    time.sleep(300)

                except Exception as e:
                    print(f"An error occurred: {e}")
                    time.sleep(60)

                # Check if it's market close (adjust hours as needed)
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5)))
                if now.hour == 17 and now.minute == 0 and now.weekday() == 4:  # Friday 5:00 PM EST
                    # Close all open positions
                    current_position = get_eurus_position()
                    if current_position != 0:
                        close_position(eurus_instrument, current_position)
                        print("All positions closed at market close.")
                    break  # Exit the live trading loop

        else:
            print(f"Negative expectancy ({expectancy:.4f}). Not trading today.")

# --- Main program ---
if __name__ == "__main__":
    create_database()  # Create the database

    while True:
        try:
            run_strategy()
            time.sleep(60)  # Check every minute
        except Exception as e:
            print(f"An error occurred: {e}")
            time.sleep(60)