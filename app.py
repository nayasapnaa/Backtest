import streamlit as st
import pandas as pd
import numpy as np
import re
from datetime import datetime, timedelta, time as dtime

st.set_page_config(page_title="NIFTY Real Chart Backtest Lab", page_icon="📈", layout="centered")

st.title("📈 NIFTY Real Chart Backtest Lab")
st.caption("Upload your historical OHLCV CSV to backtest your exact Profit-Lock & Rejection strategy.")

with st.expander("⚙️ Backtest Strategy Parameters", expanded=True):
    bt_capital = st.number_input("Backtest Initial Capital (₹)", value=150000.0, step=10000.0)
    bt_lot_size = st.number_input("Backtest Quantity / Lot Size", value=75, step=25)
    bt_costs = st.number_input("Round-trip cost per batch (₹)", value=40.0, step=5.0)
    bt_slippage = st.number_input("Estimated Slippage (Points)", value=1.0, step=0.5)
    bt_min_spread = st.slider("Min EMA Separation Filter", min_value=1.0, max_value=10.0, value=2.0, step=0.5)

uploaded_csv = st.file_uploader("Upload historical candles (CSV format)", type=["csv"], key="real_chart_csv")

def _normalise_columns(frame):
    frame = frame.copy()
    frame.columns = [
        re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")
        for c in frame.columns
    ]
    aliases = {
        "datetime": "time", "timestamp": "time", "date_time": "time",
        "date": "time", "time_stamp": "time", "vol": "volume",
    }
    return frame.rename(columns={c: aliases.get(c, c) for c in frame.columns})

def run_historical_backtest(frame, qty, cost_per_trade, slippage_points, min_spread):
    data = frame.copy()
    if "time" in data.columns:
        data["time"] = pd.to_datetime(data["time"], errors="coerce")
        data["session"] = data["time"].dt.date
        data = data.sort_values("time").reset_index(drop=True)
    else:
        data["session"] = "single_session"
        data = data.reset_index(drop=True)

    trades = []
    equity = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0
    sessions = []

    for session_key, day in data.groupby("session", sort=False):
        day = day.copy().reset_index(drop=True)
        if len(day) < 15:
            continue

        # Calculate technical indicators for real chart
        typical = (day["high"] + day["low"] + day["close"]) / 3.0
        vol = day["volume"].clip(lower=0)
        cumulative_volume = vol.cumsum()
        day["vwap"] = ((typical * vol).cumsum() / cumulative_volume.replace(0, np.nan))
        day["vwap"] = day["vwap"].fillna(day["close"].expanding().mean())
        
        day["ema_fast"] = day["close"].ewm(span=9, adjust=False).mean()
        day["ema_slow"] = day["close"].ewm(span=21, adjust=False).mean()
        day["macro_trend"] = day["close"].ewm(span=50, adjust=False).mean()
        
        opening_high = float(day.loc[:2, "high"].max())
        opening_low = float(day.loc[:2, "low"].min())
        
        active = None
        pending = None
        daily_net = 0.0
        trades_today = 0
        session_trades_start = len(trades)

        for i in range(4, len(day)):
            row = day.iloc[i]
            exited_this_bar = False
            stamp = str(row["time"]) if "time" in day.columns else f"Candle {i+1}"

            # Execute pending signal on open of next candle
            if pending is not None and active is None and trades_today < 5:
                entry = float(row["open"]) + (slippage_points if pending["side"] == "LONG" else -slippage_points)
                active = {
                    "side": pending["side"], 
                    "entry": entry, 
                    "entry_time": pending["time"],
                    "stop": entry - 5.0 if pending["side"] == "LONG" else entry + 5.0, # Initial 5 pt SL
                    "target": entry + 10.0 if pending["side"] == "LONG" else entry - 10.0, # Initial 10 pt target
                    "profit_locked": False
                }
                trades_today += 1
                pending = None

            # Manage active position against high/low of current historical candle
            if active is not None:
                current_profit_pts = (float(row["close"]) - active["entry"]) if active["side"] == "LONG" else (active["entry"] - float(row["close"]))
                
                # PROFIT LOCK SYSTEM: Check if profit crosses ₹500
                if current_profit_pts * int(qty) >= 500.0 and not active["profit_locked"]:
                    active["profit_locked"] = True
                    active["stop"] = active["entry"] + 5.0 if active["side"] == "LONG" else active["entry"] - 5.0
                    active["target"] = active["entry"] + 15.0 if active["side"] == "LONG" else active["entry"] - 15.0

                exit_price, exit_reason = None, None
                if active["side"] == "LONG":
                    if float(row["low"]) <= active["stop"]:
                        exit_price, exit_reason = active["stop"] - slippage_points, "Stop-loss (Locked +5)" if active["profit_locked"] else "Stop-loss (5 pt)"
                    elif float(row["high"]) >= active["target"]:
                        exit_price, exit_reason = active["target"] - slippage_points, "Target Hit (10+ pts)"
                else:
                    if float(row["high"]) >= active["stop"]:
                        exit_price, exit_reason = active["stop"] + slippage_points, "Stop-loss (Locked +5)" if active["profit_locked"] else "Stop-loss (5 pt)"
                    elif float(row["low"]) <= active["target"]:
                        exit_price, exit_reason = active["target"] + slippage_points, "Target Hit (10+ pts)"

                if exit_price is not None:
                    sign = 1 if active["side"] == "LONG" else -1
                    gross = (exit_price - active["entry"]) * int(qty) * sign
                    net = gross - float(cost_per_trade)
                    trades.append({
                        "Session": str(session_key), 
                        "Side": active["side"],
                        "Entry time": active["entry_time"], 
                        "Exit time": stamp,
                        "Entry price": round(active["entry"], 2), 
                        "Exit price": round(exit_price, 2),
                        "Quantity": int(qty), 
                        "Exit reason": exit_reason,
                        "Net P&L ₹": round(net, 2)
                    })
                    daily_net += net
                    equity += net
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, peak_equity - equity)
                    active = None
                    exited_this_bar = True

            if trades_today >= 5:
                pending = None
                continue

            # Signal generation logic based on Rejection Candle & VWAP / EMA
            ema_fast_v = row["ema_fast"]
            ema_slow_v = row["ema_slow"]
            spread = abs(ema_fast_v - ema_slow_v)

            if active is None and pending is None and not exited_this_bar and spread >= min_spread:
                high_v = float(row["high"])
                low_v = float(row["low"])
                close_v = float(row["close"])
                open_v = float(row["open"])
                total_rng = high_v - low_v
                
                if total_rng > 0:
                    lower_wk = min(close_v, open_v) - low_v
                    upper_wk = high_v - max(close_v, open_v)

                    long_reject = (
                        lower_wk > (total_rng * 0.45) and close_v > open_v
                        and (low_v <= float(row["vwap"]) or low_v <= opening_high)
                        and ema_fast_v > ema_slow_v
                    )
                    short_reject = (
                        upper_wk > (total_rng * 0.45) and close_v < open_v
                        and (high_v >= float(row["vwap"]) or high_v >= opening_low)
                        and ema_fast_v < ema_slow_v
                    )

                    if long_reject:
                        pending = {"side": "LONG", "time": stamp}
                    elif short_reject:
                        pending = {"side": "SHORT", "time": stamp}

        sessions.append({"Session": str(session_key), "Trades": len(trades) - session_trades_start, "Net P&L ₹": round(daily_net, 2)})

    return pd.DataFrame(trades), pd.DataFrame(sessions), max_drawdown

if uploaded_csv is not None:
    try:
        raw_bt = _normalise_columns(pd.read_csv(uploaded_csv))
        required_cols = {"open", "high", "low", "close"}
        if required_cols.issubset(raw_bt.columns):
            st.success("✅ CSV successfully parsed with OHLC structure!")
            if st.button("▶️ Run Real Chart Backtest", type="primary"):
                results_df, sessions_df, max_dd = run_historical_backtest(
                    raw_bt, bt_lot_size, bt_costs, bt_slippage, bt_min_spread
                )
                if not results_df.empty:
                    st.markdown("### 📊 Backtest Performance Summary")
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total Net P&L", f"₹{results_df['Net P&L ₹'].sum():,.2f}")
                    col2.metric("Total Trades Taken", len(results_df))
                    col3.metric("Max Drawdown", f"₹{max_dd:,.2f}")

                    st.markdown("#### Detailed Trade Log")
                    st.dataframe(results_df, use_container_width=True, hide_index=True)
                    
                    st.download_button(
                        "Download Backtest Results CSV",
                        data=results_df.to_csv(index=False).encode("utf-8"),
                        file_name="real_chart_backtest_results.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("No trades were triggered. Try lowering the Min EMA Separation filter or checking your candle data format.")
        else:
            st.error(f"Missing required columns. Your CSV must include: open, high, low, close. Found: {list(raw_bt.columns)}")
    except Exception as e:
        st.error(f"Error processing CSV: {e}")
