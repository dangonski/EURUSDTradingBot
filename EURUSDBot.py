import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.trades as trades
import pandas as pd
import logging
import sqlite3
from flask import Flask, render_template
import time
import threading

# Configure logging
logging.basicConfig(filename="trading_bot.log", level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# Replace with your Oanda account credentials
accountID = "your_account_id"  # Replace with your actual account ID
access_token = "your_access_token"  # Replace with your actual access token

# Initialize Oanda API client
client = oandapyV20.API(access_token=access_token)

# --- Trading Parameters ---
instrument = "EUR_USD"
granularity = "M15"  # 15-minute chart
risk_percentage = 0.03  # 3% risk per trade
adx_threshold = 25
atr_period = 14  # Period for ATR calculation
trailing_stop_atr_multiplier = 2  # Multiplier for trailing stop based on ATR

# --- Database Setup ---
conn = sqlite3.connect('trades.db')
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        instrument TEXT NOT NULL,
        units INTEGER NOT NULL,
        entry_price REAL NOT NULL,
        stop_loss REAL,
        take_profit REAL,
        exit_price REAL,
        profit_loss REAL,
        profit_ratio REAL
    )
''')
conn.commit()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Helper Functions ---
def get_historical_data(instruments, granularity, count):
    """Fetches historical data for the given instruments."""
    try:
        params = {"count": count, "granularity": granularity, "instruments": instruments}
        r = instruments.InstrumentsCandles(params=params)
        client.request(r)

        data = {}
        for instrument in instruments.split(","):
            prices = [
                {
                    "Date": pd.to_datetime(candle['time']),
                    "Open": float(candle['mid']['o']),
                    "High": float(candle['mid']['h']),
                    "Low": float(candle['mid']['l']),
                    "Close": float(candle['mid']['c']),
                    "Volume": float(candle['volume'])
                }
                for candle in r.response['candles'] if candle['instrument'] == instrument
            ]
            df = pd.DataFrame(prices)
            df.set_index("Date", inplace=True)
            data[instrument] = df
        return data
    except oandapyV20.exceptions.V20Error as e:
        logging.error(f"Error fetching historical data: {e}")
        raise

def calculate_adx(df, period=14):
    """Calculates the Average Directional Index (ADX) for the given DataFrame."""
    try:
        df['TR'] = df[['High', 'Low', 'Close']].diff().abs().max(axis=1)
        df['TR'] = df['TR'].fillna(df['High'] - df['Low'])
        df['+DM'] = (df['High'] - df['High'].shift(1)).apply(lambda x: x if x > 0 else 0)
        df['-DM'] = (df['Low'].shift(1) - df['Low']).apply(lambda x: x if x > 0 else 0)
        df['+DM14'] = df['+DM'].rolling(window=period).mean()
        df['-DM14'] = df['-DM'].rolling(window=period).mean()
        df['TR14'] = df['TR'].rolling(window=period).mean()
        df['+DI14'] = (df['+DM14'] / df['TR14']) * 100
        df['-DI14'] = (df['-DM14'] / df['TR14']) * 100
        df['DX'] = (df['+DI14'] - df['-DI14']).abs() / (df['+DI14'] + df['-DI14']) * 100
        df['ADX'] = df['DX'].rolling(window=period).mean()
        return df['ADX']
    except Exception as e:
        logging.error(f"Error calculating ADX: {e}")
        raise

def calculate_atr(df, period=14):
    """Calculates the Average True Range (ATR) for the given DataFrame."""
    try:
        df['TR'] = df[['High', 'Low', 'Close']].diff().abs().max(axis=1)
        df['TR'] = df['TR'].fillna(df['High'] - df['Low'])
        df['ATR'] = df['TR'].rolling(window=period).mean()
        return df['ATR']
    except Exception as e:
        logging.error(f"Error calculating ATR: {e}")
        raise

def backtest_strategy(df_eur_usd, df_dxy):
    """Backtests the strategy with enhanced risk management and exit signals."""
    try:
        df_combined = pd.concat([df_eur_usd[['Close', 'Volume']], 
                                 df_dxy[['Close', 'Volume']]], axis=1, 
                                keys=['EUR_USD', 'DXY'])
        df_combined.dropna(inplace=True)

        df_combined['EUR_USD_SMA_20'] = df_combined['EUR_USD']['Close'].rolling(window=20).mean()
        df_combined['DXY_SMA_20'] = df_combined['DXY']['Close'].rolling(window=20).mean()
        df_combined['EUR_USD_Avg_Volume_20'] = df_combined['EUR_USD']['Volume'].rolling(window=20).mean()
        df_combined['DXY_Avg_Volume_20'] = df_combined['DXY']['Volume'].rolling(window=20).mean()
        df_combined['EUR_USD_ADX'] = calculate_adx(df_combined['EUR_USD'])
        df_combined['DXY_ADX'] = calculate_adx(df_combined['DXY'])

        df_combined['Signal'] = 0.0
        df_combined['Signal'][(df_combined['EUR_USD']['Close'] < df_combined['EUR_USD_SMA_20']) &
                             (df_combined['EUR_USD']['Volume'] > df_combined['EUR_USD_Avg_Volume_20']) &
                             (df_combined['EUR_USD_ADX'] > adx_threshold) &
                             (df_combined['DXY']['Close'] > df_combined['DXY_SMA_20']) &
                             (df_combined['DXY']['Volume'] > df_combined['DXY_Avg_Volume_20']) &
                             (df_combined['DXY_ADX'] > adx_threshold)] = -1.0  
        df_combined['Signal'][(df_combined['EUR_USD']['Close'] > df_combined['EUR_USD_SMA_20']) &
                             (df_combined['EUR_USD']['Volume'] > df_combined['EUR_USD_Avg_Volume_20']) &
                             (df_combined['EUR_USD_ADX'] > adx_threshold) & 
                             (df_combined['DXY']['Close'] < df_combined['DXY_SMA_20']) &
                             (df_combined['DXY']['Volume'] > df_combined['DXY_Avg_Volume_20']) &
                             (df_combined['DXY_ADX'] > adx_threshold)] = 1.0

        # --- Enhanced Risk Management and Exit Signals ---
        df_combined['Position'] = df_combined['Signal'].diff()
        df_combined['Entry Price'] = df_combined['EUR_USD']['Close'].shift(1)

        df_combined['ATR'] = calculate_atr(df_combined['EUR_USD'], atr_period)
        df_combined['Stop Loss'] = 0.0
        df_combined.loc[df_combined['Position'] == 1, 'Stop Loss'] = df_combined['Entry Price'] - (trailing_stop_atr_multiplier * df_combined['ATR'])
        df_combined.loc[df_combined['Position'] == -1, 'Stop Loss'] = df_combined['Entry Price'] + (trailing_stop_atr_multiplier * df_combined['ATR'])
        df_combined['Exit Signal'] = 0.0
        df_combined.loc[(df_combined['EUR_USD_ADX'] < adx_threshold) | (df_combined['DXY_ADX'] < adx_threshold), 'Exit Signal'] = 1.0

        current_position = 0
        entry_price = 0
        stop_loss_price = 0
        for i in range(1, len(df_combined)):
            if df_combined['Position'].iloc[i] != 0:
                current_position = df_combined['Position'].iloc[i]
                entry_price = df_combined['Entry Price'].iloc[i]
                stop_loss_price = df_combined['Stop Loss'].iloc[i]
            elif current_position != 0:
                if current_position == 1 and df_combined['EUR_USD']['Close'].iloc[i] > entry_price:
                    entry_price = df_combined['EUR_USD']['Close'].iloc[i]
                    stop_loss_price = entry_price - (trailing_stop_atr_multiplier * df_combined['ATR'].iloc[i])
                elif current_position == -1 and df_combined['EUR_USD']['Close'].iloc[i] < entry_price:
                    entry_price = df_combined['EUR_USD']['Close'].iloc[i]
                    stop_loss_price = entry_price + (trailing_stop_atr_multiplier * df_combined['ATR'].iloc[i])
                if (current_position == 1 and df_combined['EUR_USD']['Close'].iloc[i] < stop_loss_price) or \
                   (current_position == -1 and df_combined['EUR_USD']['Close'].iloc[i] > stop_loss_price) or \
                   (df_combined['Exit Signal'].iloc[i] == 1):
                    current_position = 0
            df_combined['Position'].iloc[i] = current_position

        df_combined['Returns'] = df_combined['EUR_USD']['Close'].pct_change() * df_combined['Position'].shift(1)
        df_combined['Cumulative Returns'] = (1 + df_combined['Returns']).cumprod()
        return df_combined
    except Exception as e:
        logging.error(f"Error backtesting strategy: {e}")
        raise

def get_current_price(instrument):
    """Fetches the current price of the instrument."""
    try:
        params = {"instruments": instrument}
        r = instruments.InstrumentsCandles(instrument=instrument, params=params)
        client.request(r)
        data = r.response['candles'][0]['mid']
        return (float(data['o']) + float(data['c'])) / 2
    except oandapyV20.exceptions.V20Error as e:
        logging.error(f"Error getting current price: {e}")
        raise

def get_account_balance():
    """Retrieves the account balance."""
    try:
        r = accounts.AccountDetails(accountID)
        client.request(r)
        return float(r.response['account']['balance'])
    except oandapyV20.exceptions.V20Error as e:
        logging.error(f"Error getting account balance: {e}")
        raise

def get_open_positions(instrument):
    """Retrieves any open positions for the instrument."""
    try:
        r = trades.OpenTrades(accountID)
        client.request(r)
        positions = r.response['trades']
        return [position for position in positions if position['instrument'] == instrument]
    except oandapyV20.exceptions.V20Error as e:
        logging.error(f"Error getting open positions: {e}")
        raise

def place_market_order(instrument, units, stop_loss):
    """Places a market order with a stop loss and records the trade in the database."""
    try:
        data = {
            "order": {
                "instrument": instrument,
                "units": units,
                "type": "MARKET",
                "stopLossOnFill": {
                    "distance": str(stop_loss)
                },
            }
        }
        r = orders.OrderCreate(accountID, data=data)
        client.request(r)
        print("Order created successfully:", r.response)
        logging.info(f"Order created successfully: {r.response}")

        # Record trade data in the database
        entry_price = float(r.response['orderFillTransaction']['price'])
        cursor.execute('''
            INSERT INTO trades (instrument, units, entry_price, stop_loss)
            VALUES (?, ?, ?, ?)
        ''', (instrument, units, entry_price, stop_loss))
        conn.commit()
    except oandapyV20.exceptions.V20Error as e:
        logging.error(f"Error placing market order: {e}")
        raise

def update_trade_data(trade_id, exit_price, profit_loss, profit_ratio):
    """Updates the trade record in the database with exit details."""
    try:
        cursor.execute('''
            UPDATE trades
            SET exit_price = ?, profit_loss = ?, profit_ratio = ?
            WHERE id = ?
        ''', (exit_price, profit_loss, profit_ratio, trade_id))
        conn.commit()
    except Exception as e:
        logging.error(f"Error updating trade data: {e}")
        raise

def close_trade(trade_id):
    """Closes the specified trade."""
    try:
        r = trades.TradeClose(accountID, trade_id)
        client.request(r)
        print(f"Trade {trade_id} closed successfully:", r.response)
        logging.info(f"Trade {trade_id} closed successfully: {r.response}")
    except oandapyV20.exceptions.V20Error as e:
        logging.error(f"Error closing trade {trade_id}: {e}")
        raise

def calculate_units(account_balance, risk_percentage, entry_price, stop_loss_price):
    """Calculates the number of units to trade based on risk percentage."""
    try:
        risk_amount = account_balance * risk_percentage
        stop_loss_pips = abs(entry_price - stop_loss_price)
        units = round(risk_amount / stop_loss_pips)
        return units
    except Exception as e:
        logging.error(f"Error calculating units: {e}")
        raise

# --- Flask Routes ---
@app.route('/')
def index():
    """Renders the HTML template with trade data."""
    cursor.execute("SELECT * FROM trades")
    trades_data = cursor.fetchall()
    return render_template('trades.html', trades=trades_data)

# --- Main Trading Logic ---
def main():
    """Main function to execute the trading strategy."""
    try:
        # --- Backtesting ---
        print("Performing backtesting...")
        logging.info("Performing backtesting...")
        data = get_historical_data("EUR_USD,USD_IDX", granularity, 500)
        df_eur_usd = data["EUR_USD"]
        df_dxy = data["USD_IDX"]
        backtest_results = backtest_strategy(df_eur_usd.copy(), df_dxy.copy())

        print("\nBacktesting Results:")
        print(backtest_results[['EUR_USD', 'EUR_USD_SMA_20', 'EUR_USD_Avg_Volume_20', 'EUR_USD_ADX',
                                 'DXY', 'DXY_SMA_20', 'DXY_Avg_Volume_20', 'DXY_ADX',
                                 'Signal', 'Position', 'Returns', 'Cumulative Returns']].tail())

        # --- User Input for Live Trading ---
        choice = input("\nDo you want to start auto-trading? (yes/no): ")

        if choice.lower() == "yes":
            print("\nLive Trading...")
            logging.info("Live Trading...")

            while True:
                # Check for open positions
                open_positions = get_open_positions(instrument)
                if open_positions:
                    for position in open_positions:
                        trade_id = position['id']
                        current_price = get_current_price(instrument)

                        # --- Trade Exit Logic ---
                        # 1. Trailing Stop Loss
                        if int(position['initialUnits']) > 0:  # Long position
                            stop_loss_price = float(position['stopLossOrder']['price'])
                            if current_price > stop_loss_price + (trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1]):
                                # Update stop loss
                                new_stop_loss_price = current_price - (trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1])
                                try:
                                    data = {
                                        "order": {
                                            "type": "STOP_LOSS",
                                            "tradeID": trade_id,
                                            "price": str(new_stop_loss_price)
                                        }
                                    }
                                    r = orders.OrderCreate(accountID, data=data)
                                    client.request(r)
                                    print(f"Stop loss for trade {trade_id} updated to {new_stop_loss_price}")
                                    logging.info(f"Stop loss for trade {trade_id} updated to {new_stop_loss_price}")
                                except oandapyV20.exceptions.V20Error as e:
                                    logging.error(f"Error updating stop loss for trade {trade_id}: {e}")

                        elif int(position['initialUnits']) < 0:  # Short position
                            stop_loss_price = float(position['stopLossOrder']['price'])
                            if current_price < stop_loss_price - (trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1]):
                                # Update stop loss
                                new_stop_loss_price = current_price + (trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1])
                                try:
                                    data = {
                                        "order": {
                                            "type": "STOP_LOSS",
                                            "tradeID": trade_id,
                                            "price": str(new_stop_loss_price)
                                        }
                                    }
                                    r = orders.OrderCreate(accountID, data=data)
                                    client.request(r)
                                    print(f"Stop loss for trade {trade_id} updated to {new_stop_loss_price}")
                                    logging.info(f"Stop loss for trade {trade_id} updated to {new_stop_loss_price}")
                                except oandapyV20.exceptions.V20Error as e:
                                    logging.error(f"Error updating stop loss for trade {trade_id}: {e}")

                        # 2. ADX Exit Signal
                        if backtest_results['EUR_USD_ADX'].iloc[-1] < adx_threshold or backtest_results['DXY_ADX'].iloc[-1] < adx_threshold:
                            try:
                                close_trade(trade_id)
                            except Exception as e:
                                logging.error(f"Error closing trade {trade_id}: {e}")

                        # --- Update Trade Data in Database ---
                        # Get the latest trade details
                        try:
                            r = trades.TradeDetails(accountID, trade_id)
                            client.request(r)
                            trade_data = r.response['trade']

                            exit_price = float(trade_data['price'])
                            profit_loss = float(trade_data['realizedPL'])
                            profit_ratio = (exit_price / float(trade_data['price'])) - 1 if trade_data['initialUnits'] > 0 else \
                                           (float(trade_data['price']) / exit_price) - 1

                            update_trade_data(trade_id, exit_price, profit_loss, profit_ratio)
                        except oandapyV20.exceptions.V20Error as e:
                            logging.error(f"Error getting trade details for {trade_id}: {e}")

                else:
                    # No open positions, wait for the next trading opportunity
                    current_price = get_current_price(instrument)
                    current_dxy = get_current_price("USD_IDX")

                    if (current_price > backtest_results['EUR_USD_SMA_20'].iloc[-1] and
                        current_dxy < backtest_results['DXY_SMA_20'].iloc[-1] and
                        backtest_results['EUR_USD_ADX'].iloc[-1] > adx_threshold and
                        backtest_results['DXY_ADX'].iloc[-1] > adx_threshold):

                        units = abs(calculate_units(get_account_balance(), risk_percentage, current_price,
                                                    current_price - trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1]))  # Buy
                        stop_loss = abs(current_price - (current_price - trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1]))
                        place_market_order(instrument, units, stop_loss)

                    elif (current_price < backtest_results['EUR_USD_SMA_20'].iloc[-1] and
                          current_dxy > backtest_results['DXY_SMA_20'].iloc[-1] and
                          backtest_results['EUR_USD_ADX'].iloc[-1] > adx_threshold and
                          backtest_results['DXY_ADX'].iloc[-1] > adx_threshold):

                        units = -abs(calculate_units(get_account_balance(), risk_percentage, current_price,
                                                     current_price + trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1]))  # Sell
                        stop_loss = abs(current_price - (current_price + trailing_stop_atr_multiplier * backtest_results['ATR'].iloc[-1]))
                        place_market_order(instrument, units, stop_loss)

                # Wait for the next 15-minute candle
                time.sleep(900)  # 15 minutes = 900 seconds

        else:
            print("Auto-trading not activated.")
            logging.info("Auto-trading not activated.")

    except Exception as e:
        logging.exception(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    # Run the Flask app in a separate thread
    threading.Thread(target=app.run, kwargs={'host':'0.0.0.0'}).start()

    # Run the main trading logic
    main()