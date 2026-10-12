# NIFTY 50 Options Paper Trading Lab

**Simulation only.** This app does not connect to Zerodha, use live quotes, or place orders.

## Run
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
Open the URL shown by Streamlit in a browser.

## Android-friendly deployment
You can put `app.py` and `requirements.txt` in a private GitHub repository, then deploy through Streamlit Community Cloud and open the resulting URL on your phone. Be aware that a public repository makes source code public; don't put API keys or personal information in it. This prototype has no secrets.

## CSV format
Columns: `datetime,open,high,low,close,volume`. Use 5-minute candles, sorted or sortable by timestamp, and timestamps in India local time. Use trustworthy historical data you are allowed to use.

## Important limitations
- Built-in sample candles are synthetic, not actual NIFTY market history.
- Option premiums are represented by a simplistic fixed-delta proxy, not real option quotes or option-chain data.
- VWAP depends on the uploaded volume values. Index volume may not be meaningful; a futures-based VWAP strategy would need a consistent futures dataset and separate signal mapping.
- Stops and fills are approximations. Real gaps, spreads, liquidity, volatility changes, brokerage, taxes, and slippage can change results.
- This is not evidence of an edge and must not be used to trade real money without substantial validation.
