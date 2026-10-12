import streamlit as st
import pandas as pd
import numpy as np
import time
import re
from datetime import datetime, timedelta, time as dtime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY Paper Trading Simulator", page_icon="📈", layout="centered")

# ---------- Session state ----------
defaults = {
    "running": False,
    "price": 25000.0,
    "prev_close": 25000.0,
    "candles": [],
    "ticks_in_candle": 0,
    "candle_seconds": 30,  # accelerated candle for practice; 30 sec = one simulated 5-minute candle
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
    "last_reason": "Start the simulation to generate candles.",
    "last_trade_bar": -999,
    "bar_index": 0,
    "day_id": 1,
    "history": [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

st.title("📈 NIFTY Expert Quant Paper Trading & Backtest Lab")
st.caption("SIMULATION ONLY • Patient Entry Filter • ADX & Time Cutoff (2:30 PM) • No Transaction Limits")

speed = st.select_slider(
    "⚡ Fast play speed",
    options=[1, 2, 5, 10, 20, 40, 60],
    value=1,
    format_func=lambda x: f"{x}x",
    help="When running, process this many simulated ticks per refresh."
)

with st.expander("⚙️ Expert Strategy & Risk Settings", expanded=False):
    capital = st.number_input("Practice capital (₹)", min_value=10000, max_value=250000, value=50000, step=5000)
    risk_pct = st.select_slider("Risk budget per trade (%)", options=[1.0, 1.5, 2.0], value=1.5)
    daily_loss_pct = st.select_slider("Daily loss limit (%)", options=[1.0, 1.5, 2.0, 3.0, 5.0], value=3.0)
    target_r = st.select_slider("Target (R multiple)", options=[1.0, 1.5, 2.0, 2.5, 3.0], value=2.0)
    
    st.markdown("---")
    st.subheader("🛡️ Patient & Choppy Market Filters")
    use_adx_filter = st.checkbox("Enable ADX Trend Filter (Blocks sideways chop)", value=True)
    adx_threshold = st.slider("Minimum ADX for Trend Confirmation", min_value=10, max_value=35, value=22, step=1)
    cutoff_hour = st.slider("Session Trade Cutoff Hour (No new entries after)", min_value=13, max_value=15, value=14, step=1)
    cutoff_minute = st.slider("Session Trade Cutoff Minute", min_value=0, max_value=45, value=30, step=15)
    
    st.markdown("---")
    trail_on = st.checkbox("Use trailing stop", value=True)
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

def calculate_advanced_indicators(candles):
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
    df['tr'] = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0.0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0.0)
    
    tr_smooth = df['tr'].ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (df['plus_dm'].ewm(alpha=1/period, adjust=False).mean() / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (df['minus_dm'].ewm(alpha=1/period, adjust=False).mean() / tr_smooth.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df['adx'] = dx.ewm(alpha=1/period, adjust=False).mean()
    
    current_adx = float(df['adx'].iloc[-1]) if not pd.isna(df['adx'].iloc[-1]) else 15.0
    return vwap, float(df["ema_fast"].iloc[-1]), float(df["ema_slow"].iloc[-1]), current_adx

def signal_for_state():
    s = st.session_state
    if s["bar_index"] < 3 or s["opening_high"] is None:
        return "NO TRADE", "Waiting for opening range completion."
    
    current_t = s["sim_time"].time()
    cutoff_t = dtime(cutoff_hour, cutoff_minute)
    if current_t >= cutoff_t:
        return "NO TRADE", f"Past time cutoff ({cutoff_hour:02d}:{cutoff_minute:02d}). Waiting for next session."

    candles = s["candles"]
    last = candles[-1]
    prev = candles[-2] if len(candles) > 1 else last
    vwap, ema_fast, ema_slow, adx = calculate_advanced_indicators(candles)
    
    if vwap is None:
        return "NO TRADE", "Gathering price data..."

    if use_adx_filter and adx < adx_threshold:
        return f"SIDEWAYS / CHOP (ADX: {adx:.1f})", f"Market is sideways/choppy (ADX {adx:.1f} < {adx_threshold}). Patiently waiting for a strong trend."

    bullish = (
        last["close"] > s["opening_high"] 
        and last["close"] > vwap 
        and ema_fast > ema_slow 
        and last["close"] > prev["close"]
    )
    bearish = (
        last["close"] < s["opening_low"] 
        and last["close"] < vwap 
        and ema_fast < ema_slow 
        and last["close"] < prev["close"]
    )
    
    if bullish:
        return "BUY CALL", f"Patient Setup Verified: Bullish breakout with ADX {adx:.1f}."
    if bearish:
        return "BUY PUT", f"Patient Setup Verified: Bearish breakdown with ADX {adx:.1f}."
    return "NO TRADE", f"No perfect breakout trigger yet (ADX: {adx:.1f})."

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
        
        if trail_on and pos["premium"] > pos["best_premium"]:
            pos["best_premium"] = pos["premium"]
            pos["stop_premium"] = max(pos["stop_premium"], pos["best_premium"] - pos["risk_per_unit"])
            
        pos["unrealized"] = (pos["premium"] - pos["entry_premium"]) * pos["quantity"] - pos["cost_estimate"]
        if pos["premium"] <= pos["stop_premium"]:
            close_position("Stop-loss / trailing stop", pos["premium"])
        elif pos["premium"] >= pos["target_premium"]:
            close_position("Target hit", pos["premium"])

    if s["ticks_in_candle"] >= s["candle_seconds"]:
        s["bar_index"] += 1
        s["ticks_in_candle"] = 0
        if s["bar_index"] == 3:
            opening = s["candles"][:3]
            s["opening_high"] = max(c["high"] for c in opening)
            s["opening_low"] = min(c["low"] for c in opening)
        signal, reason = signal_for_state()
        s["last_signal"], s["last_reason"] = signal, reason

        halted = s["daily_pnl"] <= -daily_limit
        if (auto_execute and not halted and s["position"] is None and signal in ("BUY CALL", "BUY PUT")
                and s["bar_index"] - s["last_trade_bar"] >= 2):
            stop_distance = min(max(1.0, base_premium * 0.10), risk_budget / lot_size)
            risk_amount_per_lot = stop_distance * lot_size
            if risk_amount_per_lot <= risk_budget and capital >= base_premium * lot_size:
                entry = max(1.0, base_premium + np.random.normal(0, 3))
                stop = max(1.0, entry - stop_distance)
                target = entry + stop_distance * target_r
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
                    "risk_per_unit": stop_distance,
                    "best_premium": entry,
                    "cost_estimate": costs_per_trade,
                    "unrealized": -costs_per_trade,
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
vwap, ema_fast, ema_slow, current_adx = calculate_advanced_indicators(s["candles"])
signal, reason = signal_for_state() if s["bar_index"] > 0 else (s["last_signal"], s["last_reason"])

if s["position"]:
    display_signal = f"POSITION OPEN — {s['position']['side']}"
else:
    display_signal = s["last_signal"] if s["bar_index"] > 0 else "NO TRADE"

st.subheader("1. Live Expert Simulator Dashboard")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Simulated NIFTY", f'{s["price"]:,.2f}', f'{s["price"]-s["prev_close"]:+.2f}')
m2.metric("VWAP", f'{vwap:,.2f}' if vwap else "—")
m3.metric("ADX Trend Strength", f'{current_adx:.1f}' if current_adx else "—")
m4.metric("Candle timer", f'{s["candle_seconds"] - s["ticks_in_candle"]:02d}s')

if s["opening_high"] is not None:
    st.write(f'**Opening Range:** {s["opening_low"]:,.2f} – {s["opening_high"]:,.2f}')
else:
    st.write("**Opening Range:** Waiting for 3 completed opening candles.")

if display_signal == "BUY CALL":
    st.success("🟢 BUY CALL — Patient Setup Confirmed")
elif display_signal == "BUY PUT":
    st.error("🔴 BUY PUT — Patient Setup Confirmed")
elif "SIDEWAYS" in display_signal:
    st.warning(f"🛡️ {display_signal}")
elif display_signal.startswith("POSITION OPEN"):
    st.info(f"📍 {display_signal}")
else:
    st.warning("⏸ WAITING FOR PERFECT SETUP — Filtering Chop")
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
st.subheader("2. Open Position & Trade Log")
pos = s["position"]
if pos:
    current_pnl = (pos["premium"] - pos["entry_premium"]) * pos["quantity"] - pos["cost_estimate"]
    p1, p2, p3 = st.columns(3)
    p1.metric("Option", f'BUY {pos["side"]}')
    p2.metric("Entry premium", f'₹{pos["entry_premium"]:.2f}')
    p3.metric("Current premium", f'₹{pos["premium"]:.2f}')

r1, r2 = st.columns(2)
r1.metric("Closed P&L", f'₹{s["daily_pnl"]:.2f}')
r2.metric("Total Trades Executed", f'{s["trade_count"]}')

if s["closed_trades"]:
    st.dataframe(pd.DataFrame(s["closed_trades"]), use_container_width=True, hide_index=True)

st.divider()
st.subheader("3. Historical Backtest Lab (No Transaction Limits)")
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
                         atr_period, atr_stop_mult, target_r, min_adx, cut_h, cut_m):
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
        
        previous_close = day["close"].shift(1)
        true
