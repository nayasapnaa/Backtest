import streamlit as st
import pandas as pd
import numpy as np
import time
import re
from datetime import datetime, timedelta, time as dtime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY Expert Paper Trading Simulator", page_icon="📈", layout="centered")

defaults = {
    "running": False,
    "price": 25000.0,
    "prev_close": 25000.0,
    "candles": [],
    "ticks_in_candle": 0,
    "candle_seconds": 30,
    "tick_seconds": 1,
    "sim_time": datetime(2026, 10, 12, 9, 15),
    "opening_high": None,
    "opening_low": None,
    "session_candles": 0,
    "position": None,
    "trades": [],
    "closed_trades": [],
    "trade_count": 0,
    "consecutive_losses": 0,
    "daily_pnl": 0.0,
    "last_signal": "NO TRADE",
    "last_reason": "Waiting for high-probability rejection setup.",
    "last_trade_bar": -999,
    "bar_index": 0,
    "day_id": 1,
    "history": [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

st.title("📈 NIFTY Expert Rejection & Profit-Lock Lab")
st.caption("EXPERT OPTIONS MODE • Max 5 Patient Trades • Rejection Pin Bars • VWAP & EMA • Multi-Tier Profit Locking")

speed = st.select_slider(
    "⚡ Fast play speed",
    options=[1, 2, 5, 10, 20, 40, 60],
    value=1,
    format_func=lambda x: f"{x}x"
)

with st.expander("⚙️ Expert Strategy & Risk Settings", expanded=True):
    capital = st.number_input("Practice capital (₹)", min_value=10000, max_value=250000, value=50000, step=5000)
    risk_pct = st.select_slider("Risk budget per trade (%)", options=[1.0, 1.5, 2.0], value=1.5)
    daily_loss_pct = st.select_slider("Daily loss limit (%)", options=[1.0, 1.5, 2.0, 3.0, 5.0], value=3.0)
    max_trades_session = 5  # Strict expert limit
    
    st.markdown("---")
    st.subheader("🛡️ Price Action Rejection & Timing Rules")
    min_ema_spread = st.slider("Min EMA Separation Filter", min_value=1.0, max_value=10.0, value=2.5, step=0.5)
    cutoff_hour = 15  # Stop trading at 3:00 PM
    cutoff_minute = 0
    
    st.markdown("---")
    auto_execute = st.checkbox("Automatic paper execution on confirmed signal", value=True)
    lot_size = 75
    base_premium = st.number_input("Starting simulated option premium (₹)", min_value=20.0, max_value=500.0, value=120.0, step=5.0)
    premium_sensitivity = st.slider("Option premium sensitivity", min_value=0.5, max_value=3.0, value=1.2, step=0.1)
    costs_per_trade = st.number_input("Estimated round-trip cost per batch (₹)", min_value=0.0, max_value=200.0, value=20.0, step=5.0)

risk_budget = capital * risk_pct / 100
daily_limit = capital * daily_loss_pct / 100

c1, c2 = st.columns(2)
with c1:
    if st.button("▶️ Start / Resume", use_container_width=True, type="primary"):
        st.session_state.running = True
with c2:
    if st.button("⏸ Pause", use_container_width=True):
        st.session_state.running = False

c3, c4 = st.columns(2)
with c3:
    if st.button("🔄 Reset session", use_container_width=True):
        for k, v in defaults.items():
            st.session_state[k] = v
        st.rerun()
with c4:
    if st.button("⏭ Next candle", use_container_width=True):
        st.session_state.ticks_in_candle = st.session_state.candle_seconds - 1
        st.session_state.running = False

def calculate_expert_indicators(candles):
    if not candles or len(candles) < 5:
        return None, None, None, None
    df = pd.DataFrame(candles)
    vol = df["volume"].sum()
    if vol <= 0:
        vwap = float(df["close"].mean())
    else:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vwap = float((typical * df["volume"]).sum() / vol)
    
    df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
    
    period = 14
    previous_close = df["close"].shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs()
    ], axis=1).max(axis=1)
    df['atr'] = true_range.rolling(period, min_periods=1).mean()
    
    return vwap, float(df["ema_fast"].iloc[-1]), float(df["ema_slow"].iloc[-1]), float(df['atr'].iloc[-1])

def signal_for_state():
    s = st.session_state
    if s["bar_index"] < 4 or s["opening_high"] is None:
        return "NO TRADE", "Waiting for opening range and candle structure."
    
    current_t = s["sim_time"].time()
    cutoff_t = dtime(cutoff_hour, cutoff_minute)
    if current_t >= cutoff_t or current_t < dtime(9, 15):
        return "NO TRADE", "Outside trading window (9:15 AM - 3:00 PM)."

    if s["trade_count"] >= max_trades_session:
        return "NO TRADE", f"Reached maximum discipline limit of {max_trades_session} trades for the session."

    candles = s["candles"]
    last = candles[-1]
    prev = candles[-2]
    vwap, ema_fast, ema_slow, atr = calculate_expert_indicators(candles)
    
    if vwap is None:
        return "NO TRADE", "Gathering market data..."

    ema_spread = abs(ema_fast - ema_slow)
    if ema_spread < min_ema_spread:
        return "NO TRADE", f"EMA spread too narrow ({ema_spread:.1f}). Waiting for expansion."

    # Rejection Candle Mastery (Pin Bar / Rejection Wick Check)
    body = abs(last["close"] - last["open"])
    total_range = last["high"] - last["low"]
    lower_wick = min(last["close"], last["open"]) - last["low"]
    upper_wick = last["high"] - max(last["close"], last["open"])

    # Bullish Rejection: Long lower wick testing support (VWAP or opening range) with bullish close
    is_bullish_rejection = (
        lower_wick > (total_range * 0.5) 
        and last["close"] > last["open"]
        and (last["low"] <= vwap or last["low"] <= s["opening_high"])
        and ema_fast > ema_slow
    )

    # Bearish Rejection: Long upper wick testing resistance with bearish close
    is_bearish_rejection = (
        upper_wick > (total_range * 0.5) 
        and last["close"] < last["open"]
        and (last["high"] >= vwap or last["high"] >= s["opening_low"])
        and ema_fast < ema_slow
    )

    if is_bullish_rejection:
        return "BUY CALL", "Expert Rejection Confirmed: Bullish pin bar rejection at support with EMA & VWAP alignment."
    if is_bearish_rejection:
        return "BUY PUT", "Expert Rejection Confirmed: Bearish pin bar rejection at resistance with EMA & VWAP alignment."
        
    return "NO TRADE", "Patiently waiting for a clean rejection candle setup."

def close_position(reason, exit_premium):
    s = st.session_state
    pos = s["position"]
    if not pos:
        return
    gross = (exit_premium - pos["entry_premium"]) * pos["quantity"]
    net = gross - pos["cost_estimate"]
    trade = {
        "Trade": len(s["closed_trades"]) + 1,
        "Side": pos["side"],
        "Entry time": pos["entry_time"],
        "Exit time": s["sim_time"].strftime("%H:%M:%S"),
        "Entry premium ₹": round(pos["entry_premium"], 2),
        "Exit premium ₹": round(exit_premium, 2),
        "Qty": pos["quantity"],
        "Exit reason": reason,
        "Gross P&L ₹": round(gross, 2),
        "Costs ₹": round(pos["cost_estimate"], 2),
        "Net P&L ₹": round(net, 2),
    }
    s["closed_trades"].append(trade)
    s["daily_pnl"] += net
    if net < 0:
        s["consecutive_losses"] += 1
    else:
        s["consecutive_losses"] = 0
    s["position"] = None

def process_tick():
    s = st.session_state
    t = s["ticks_in_candle"]
    trend_cycle = np.sin((s["bar_index"] + t / s["candle_seconds"]) / 3.7) * 0.75
    shock = np.sin((s["bar_index"] * 7 + t * 3) / 5.0) * 0.9
    drift = trend_cycle + shock + np.random.normal(0, 1.25)
    new_price = max(1000, s["price"] + drift)
    s["prev_close"] = s["price"]
    s["price"] = float(new_price)
    s["sim_time"] += timedelta(seconds=1)
    s["ticks_in_candle"] += 1

    if not s["candles"] or s["ticks_in_candle"] == 1:
        s["candles"].append({
            "time": s["sim_time"], "open": s["prev_close"], 
            "high": max(s["prev_close"], s["price"]),
            "low": min(s["prev_close"], s["price"]), 
            "close": s["price"], "volume": int(np.random.randint(50, 250))
        })
    else:
        cur = s["candles"][-1]
        cur["high"] = max(cur["high"], s["price"])
        cur["low"] = min(cur["low"], s["price"])
        cur["close"] = s["price"]
        cur["volume"] += int(np.random.randint(10, 80))

    if s["position"]:
        pos = s["position"]
        underlying_change = s["price"] - pos["last_underlying"]
        direction = 1.0 if pos["side"] == "CALL" else -1.0
        option_move = direction * underlying_change * 0.035 * premium_sensitivity
        option_move += np.random.normal(0, 0.18)
        pos["premium"] = max(1.0, pos["premium"] + option_move)
        pos["last_underlying"] = s["price"]
        
        pos["unrealized"] = (pos["premium"] - pos["entry_premium"]) * pos["quantity"] - pos["cost_estimate"]
        
        # Expert Profit-Lock Rule: After hitting >= ₹500 profit, move stoploss to Entry + 5 points, then trail
        if pos["unrealized"] >= 500.0 and not pos.get("profit_locked", False):
            pos["profit_locked"] = True
            pos["stop_premium"] = pos["entry_premium"] + 5.0  # Cost + 5 points locked profit
            pos["target_premium"] = pos["entry_premium"] + 15.0 # Extend target

        # Progressive trailing after profit lock
        if pos.get("profit_locked", False) and pos["premium"] > pos["best_premium"]:
            pos["best_premium"] = pos["premium"]
            pos["stop_premium"] = max(pos["stop_premium"], pos["best_premium"] - 2.5) # Trail by 2.5 pts

        if pos["premium"] <= pos["stop_premium"]:
            reason = "Profit-Locked Trailing SL hit (+5 pts locked)" if pos.get("profit_locked", False) else "Initial Stop-Loss hit (5 pts)"
            close_position(reason, pos["premium"])
        elif pos["premium"] >= pos["target_premium"]:
            close_position("Target hit (10 pts)", pos["premium"])

    if s["ticks_in_candle"] >= s["candle_seconds"]:
        s["bar_index"] += 1
        s["ticks_in_candle"] = 0
        if s["bar_index"] == 3:
            opening = s["candles"][:3]
            s["opening_high"] = max(c["high"] for c in opening)
            s["opening_low"] = min(c["low"] for c in opening)
        signal, reason = signal_for_state()
        s["last_signal"], s["last_reason"] = signal, reason

        halted = s["daily_pnl"] <= -daily_limit or s["trade_count"] >= max_trades_session
        if (auto_execute and not halted and s["position"] is None and signal in ("BUY CALL", "BUY PUT")
                and s["bar_index"] - s["last_trade_bar"] >= 3):
            entry = max(1.0, base_premium + np.random.normal(0, 2))
            stop = max(1.0, entry - 5.0)  # Initial stop-loss: 5 points
            target = entry + 10.0           # Initial target: 10 points
            s["position"] = {
                "side": "CALL" if signal == "BUY CALL" else "PUT",
                "entry_time": s["sim_time"].strftime("%H:%M:%S"),
                "entry_underlying": s["price"],
                "last_underlying": s["price"],
                "entry_premium": entry,
                "premium": entry,
                "quantity": lot_size,
                "stop_premium": stop,
                "initial_stop": stop,
                "target_premium": target,
                "risk_per_unit": 5.0,
                "best_premium": entry,
                "cost_estimate": costs_per_trade,
                "unrealized": -costs_per_trade,
                "profit_locked": False,
            }
            s["trade_count"] += 1
            s["last_trade_bar"] = s["bar_index"]

if st.session_state.running:
    st_autorefresh(interval=1000, key="simulator_tick")
    for _ in range(int(speed)):
        if not st.session_state.running:
            break
        process_tick()
        if not st.session_state.running:
            break

s = st.session_state
vwap, ema_fast, ema_slow, atr = calculate_expert_indicators(s["candles"])
signal, reason = signal_for_state() if s["bar_index"] > 0 else (s["last_signal"], s["last_reason"])

if s["position"]:
    display_signal = f"POSITION OPEN — {s['position']['side']}"
else:
    display_signal = s["last_signal"] if s["bar_index"] > 0 else "NO TRADE"

st.subheader("1. Live Expert Simulator Dashboard")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Simulated NIFTY", f'{s["price"]:,.2f}', f'{s["price"]-s["prev_close"]:+.2f}')
m2.metric("VWAP", f'{vwap:,.2f}' if vwap else "—")
m3.metric("EMA Spread", f'{abs(ema_fast - ema_slow):.1f}' if ema_fast else "—")
m4.metric("Trades Today", f'{s["trade_count"]}/{max_trades_session}')

if s["opening_high"] is not None:
    st.write(f'**Opening Range:** {s["opening_low"]:,.2f} – {s["opening_high"]:,.2f}')
else:
    st.write("**Opening Range:** Waiting for establishment.")

if display_signal == "BUY CALL":
    st.success("🟢 BUY CALL — Rejection Pin Bar Confirmed")
elif display_signal == "BUY PUT":
    st.error("🔴 BUY PUT — Rejection Pin Bar Confirmed")
elif display_signal.startswith("POSITION OPEN"):
    st.info(f"📍 {display_signal}")
else:
    st.warning("⏸ PATIENTLY WAITING FOR REJECTION SETUP")
st.write(s["last_reason"])

if s["candles"]:
    cdf = pd.DataFrame(s["candles"])
    cdf["time"] = pd.to_datetime(cdf["time"])
    cdf = cdf.tail(60).set_index("time")
    
    chart_df = pd.DataFrame({"Close": cdf["close"]})
    chart_df["EMA 9"] = cdf["close"].ewm(span=9).mean()
    chart_df["EMA 21"] = cdf["close"].ewm(span=21).mean()
    
    st.line_chart(chart_df, height=250)

st.divider()
st.subheader("2. Open Position & Live P&L")
pos = s["position"]
if pos:
    current_pnl = (pos["premium"] - pos["entry_premium"]) * pos["quantity"] - pos["cost_estimate"]
    p1, p2, p3 = st.columns(3)
    p1.metric("Option", f'BUY {pos["side"]}')
    p2.metric("Entry premium", f'₹{pos["entry_premium"]:.2f}')
    p3.metric("Current premium", f'₹{pos["premium"]:.2f}')
    
    p4, p5, p6 = st.columns(3)
    p4.metric("Running P&L", f'₹{current_pnl:.2f}', delta=f'₹{current_pnl:.2f}')
    p5.metric("Stop-loss", f'₹{pos["stop_premium"]:.2f} ({"LOCKED (+5 pts)" if pos.get("profit_locked") else "Initial 5 pt"})')
    p6.metric("Target", f'₹{pos["target_premium"]:.2f}')
else:
    st.info("No open position. Waiting for high-probability rejection setup.")

r1, r2 = st.columns(2)
r1.metric("Closed Session P&L", f'₹{s["daily_pnl"]:.2f}')
r2.metric("Total Trades Executed", f'{s["trade_count"]}/{max_trades_session}')

if s["closed_trades"]:
    st.dataframe(pd.DataFrame(s["closed_trades"]), use_container_width=True, hide_index=True)

st.divider()
st.subheader("3. Historical Backtest Lab (Expert Rejection & Profit-Lock)")
uploaded_csv = st.file_uploader("Upload historical candles (CSV)", type=["csv"], key="backtest_csv")

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

def _run_expert_backtest(frame, qty, cost_per_trade, slippage_points,
                         capital_value, daily_loss_pct_value,
                         atr_period, min_spread, cut_h, cut_m):
    data = frame.copy()
    data["session"] = data["time"].dt.date if "time" in data.columns else "single_session"
    data = data.sort_values("time").reset_index(drop=True) if "time" in data.columns else data.reset_index(drop=True)

    trades = []
    equity = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0
    sessions = []

    for session_key, day in data.groupby("session", sort=False):
        day = day.copy().reset_index(drop=True)
        if len(day) < max(atr_period + 3, 15):
            continue

        typical = (day["high"] + day["low"] + day["close"]) / 3.0
        vol = day["volume"].clip(lower=0)
        cumulative_volume = vol.cumsum()
        day["vwap"] = ((typical * vol).cumsum() / cumulative_volume.replace(0, np.nan))
        day["vwap"] = day["vwap"].fillna(day["close"].expanding().mean())
        day["ema_fast"] = day["close"].ewm(span=9, adjust=False).mean()
        day["ema_slow"] = day["close"].ewm(span=21, adjust=False).mean()
        
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
            stamp = str(row["time"]) if "time" in day.columns else f"{session_key} candle {i+1}"

            if "time" in day.columns and pd.notna(row["time"]):
                if row["time"].time() >= dtime(cut_h, cut_m):
                    pending = None

            if pending is not None and active is None and trades_today < 5:
                entry = float(row["open"]) + (slippage_points if pending["side"] == "LONG" else -slippage_points)
                active = {
                    "side": pending["side"], "entry": entry, "entry_time": pending["time"],
                    "stop": entry - 5.0 if pending["side"] == "LONG" else entry + 5.0,
                    "target": entry + 10.0 if pending["side"] == "LONG" else entry - 10.0,
                    "profit_locked": False
                }
                trades_today += 1
                pending = None

            if active is not None:
                current_profit_pts = (float(row["close"]) - active["entry"]) if active["side"] == "LONG" else (active["entry"] - float(row["close"]))
                if current_profit_pts * int(qty) >= 500.0 and not active["profit_locked"]:
                    active["profit_locked"] = True
                    active["stop"] = active["entry"] + 5.0 if active["side"] == "LONG" else active["entry"] - 5.0

                exit_price, exit_reason = None, None
                if active["side"] == "LONG":
                    if float(row["low"]) <= active["stop"]:
                        exit_price, exit_reason = active["stop"] - slippage_points, "Stop-loss (Locked +5)" if active["profit_locked"] else "Stop-loss (5 pt)"
                    elif float(row["high"]) >= active["target"]:
                        exit_price, exit_reason = active["target"] - slippage_points, "Target (10 pt)"
                else:
                    if float(row["high"]) >= active["stop"]:
                        exit_price, exit_reason = active["stop"] + slippage_points, "Stop-loss (Locked +5)" if active["profit_locked"] else "Stop-loss (5 pt)"
                    elif float(row["low"]) <= active["target"]:
                        exit_price, exit_reason = active["target"] + slippage_points, "Target (10 pt)"

                if exit_price is not None:
                    sign = 1 if active["side"] == "LONG" else -1
                    gross = (exit_price - active["entry"]) * int(qty) * sign
                    net = gross - float(cost_per_trade)
                    trades.append({
                        "Session": str(session_key), "Side": active["side"],
                        "Entry time": active["entry_time"], "Exit time": stamp,
                        "Entry price": round(active["entry"], 2), "Exit price": round(exit_price, 2),
                        "Quantity": int(qty), "Exit reason": exit_reason,
                        "Net P&L ₹": round(net, 2)
                    })
                    daily_net += net
                    equity += net
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, peak_equity - equity)
                    active = None
                    exited_this_bar = True

            if daily_net <= -(capital_value * daily_loss_pct_value / 100) or trades_today >= 5:
                pending = None
                continue

            ema_fast_v = row["ema_fast"]
            ema_slow_v = row["ema_slow"]
            spread = abs(ema_fast_v - ema_slow_v)

            if active is None and pending is None and not exited_this_bar and spread >= min_spread and trades_today < 5:
                high_v = float(row["high"])
                low_v = float(row["low"])
                close_v = float(row["close"])
                open_v = float(row["open"])
                total_rng = high_v - low_v
                lower_wk = min(close_v, open_v) - low_v
                upper_wk = high_v - max(close_v, open_v)

                long_reject = (
                    lower_wk > (total_rng * 0.5) and close_v > open_v
                    and (low_v <= float(row["vwap"]) or low_v <= opening_high)
                    and ema_fast_v > ema_slow_v
                )
                short_reject = (
                    upper_wk > (total_rng * 0.5) and close_v < open_v
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
        if {"open", "high", "low", "close"}.issubset(raw_bt.columns):
            if "time" in raw_bt.columns:
                raw_bt["time"] = pd.to_datetime(raw_bt["time"], errors="coerce")
            
            with st.expander("Backtest Parameters", expanded=True):
                bt_qty = st.number_input("Backtest Quantity", value=75, key="bt_q")
                bt_min_spread = st.slider("Backtest Min EMA Spread", min_value=1.0, max_value=10.0, value=2.5, key="bt_spread")
            
            if st.button("▶️ Run Expert Backtest", type="primary", key="run_bt"):
                res_df, sess_df, m_dd = _run_expert_backtest(raw_bt, bt_qty, 40.0, 1.0, 150000.0, 3.0, 14, bt_min_spread, cutoff_hour, cutoff_minute)
                if not res_df.empty:
                    st.metric("Total Net P&L", f"₹{res_df['Net P&L ₹'].sum():,.2f}")
                    st.metric("Total Trades Taken", len(res_df))
                    st.dataframe(res_df, use_container_width=True, hide_index=True)
                else:
                    st.info("No trades executed. Rejection filters successfully suppressed low-probability setups.")
    except Exception as e:
        st.error(f"Error: {e}")
