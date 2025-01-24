EUR/USD Trading Bot with Oanda API
This is an automated trading bot that uses the Oanda API to trade the EUR/USD currency pair. It incorporates technical analysis, risk management, and trade monitoring features to execute trades based on a defined strategy.

Key Features:

Data Retrieval: Fetches historical and current price data for EUR/USD and USD_IDX (US Dollar Index) from Oanda's servers using the oandapyV20 library.
Technical Indicators:
Calculates the 20-period Simple Moving Average (SMA) for both EUR/USD and USD_IDX.
Calculates the Average Directional Index (ADX) to assess trend strength.
Calculates the Average True Range (ATR) for volatility-based risk management.
Trading Strategy:
Enters long or short positions in EUR/USD based on the following conditions:
EUR/USD price relative to its 20-period SMA.
USD_IDX price relative to its 20-period SMA.
Volume of both instruments exceeding their respective 20-period average volumes.
ADX values of both instruments exceeding a predefined threshold (indicating a strong trend).
Risk Management:
Limits risk per trade to a specified percentage of the account balance.
Calculates position size (units) based on the risk percentage and the distance between the entry price and the stop-loss order.
Implements a trailing stop-loss order based on the ATR to lock in profits as the trade moves favorably.
Trade Monitoring:
Continuously monitors open positions.
Updates trailing stop-loss orders.
Exits trades based on the ADX falling below the threshold or the price hitting the stop-loss order.
Trade Logging:
Stores trade data (entry/exit prices, profit/loss, etc.) in a local SQLite database (trades.db).
Web Interface:
Provides a local web interface (using Flask) to view trade history and performance metrics.
Includes a chart to visualize profit/loss over time.
Error Handling and Logging:
Implements error handling to catch and log exceptions.
Logs events and errors to a file (trading_bot.log) for debugging and monitoring.
Requirements:

Python 3.7 or higher
oandapyV20 library
pandas library
sqlite3 library
Flask library
An Oanda trading account with API access
Setup:

Install the required libraries: pip install oandapyV20 pandas Flask
Replace the placeholder account credentials (accountID and access_token) with your actual Oanda account details.
Create an HTML file named trades.html in the same directory as the Python script to display the trade history.
Run the script: python trading_bot.py
Access the web interface: Open a web browser and go to http://127.0.0.1:5000/
