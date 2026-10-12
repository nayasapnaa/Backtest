import streamlit as st
import pandas as pd
import numpy as np
import time
import re
from datetime import datetime, timedelta, time as dtime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY Paper Trading Simulator", page_icon="📈", layout="centered")

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
    "last_reason": "Waiting for clean trend momentum.",
    "last_trade_bar": -999,
    "bar_index": 0,
    "day_id": 1,
    "history": [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

st.title("📈 NIFTY Expert Momentum & Profit-Lock Lab")
st.caption("EXPERT MODE • Auto Profit Lock at ₹400 & Instant Re-Entry (SL: 1.5 pts, Target: 6 pts)")

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
    target_r = st.select_slider("Target (R multiple)", options=[1.5, 2.0, 2.5, 3.0], value=2.0)
    
    st.markdown("---")
    st.subheader("🛡️ Momentum & Noise Filters")
    min_ema_spread = st.slider("Min EMA Separation Filter (Points)", min_value=1.0, max_value=15.0, value=3.0, step=0.5)
    atr_stop_multiplier = st.slider("ATR Stop Buffer Multiplier", min_value=1.5, max_value=3.0, value=2.0, step=0.25)
    cutoff_hour = st.slider("Session Trade Cutoff Hour", min_value=13, max_value=15, value=14, step=1)
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
    if s["bar_index"] < 3 or s["opening_high"] is None:
        return "NO TRADE", "Waiting for opening range completion."
    
    current_t = s["sim_time"].time()
    cutoff_t = dtime(cutoff_hour, cutoff_minute)
    if current_t >= cutoff_t:
        return "NO TRADE", f"Past time cutoff ({cutoff_hour:02d}:{cutoff_minute:02d}). No new entries."

    candles = s["candles"]
    last = candles[-1]
    vwap, ema_fast, ema_slow, atr = calculate_expert_indicators(candles)
    
    if vwap is None:
        return "NO TRADE", "Gathering price data..."

    ema_spread = abs(ema_fast - ema_slow)
    if ema_spread < min_ema_spread:
        return "NO TRADE", f"EMA spread too tight ({ema_spread:.1f} < {min_ema_spread}). Market lacks clean directional momentum."

    bullish = (
        last["close"] > s["opening_high"] 
        and last["close"] > vwap 
        and ema_fast > ema_slow 
        and (ema_fast - ema_slow) >= min_ema_spread
        and last["close"] > last["open"]
    )

    bearish = (
        last["close"] < s["opening_low"] 
        and last["close"] < vwap 
        and ema_fast < ema_slow 
        and (ema_slow - ema_fast) >= min_ema_spread
        and last["close"] < last["open"]
    )

    if bullish:
        return "BUY CALL", f"Strong Bullish Momentum: Clean breakout with EMA spread {ema_spread:.1f}."
    if bearish:
        return "BUY PUT", f"Strong Bearish Momentum: Clean breakdown with EMA spread {ema_spread:.1f}."
        
    return "NO TRADE", "Waiting for clear momentum expansion."

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
        
        # Rule: When profit exceeds 400, close that position and execute another same transaction with SL 1.5 lower and target 6 points higher
        if pos["unrealized"] >= 400.0:
            side_to_reenter = pos["side"]
            exit_prem = pos["premium"]
            close_position("Profit target exceeded ₹400 (Auto-lock & Re-entry)", exit_prem)
            
            new_entry = max(1.0, exit_prem)
            new_stop = max(1.0, new_entry - 1.5)
            new_target = new_entry + 6.0
            s["position"] = {
                "side": side_to_reenter,
                "entry_time": s["sim_time"].strftime("%H:%M:%S"),
                "entry_underlying": s["price"],
                "last_underlying": s["price"],
                "entry_premium": new_entry,
                "premium": new_entry,
                "quantity": lot_size,
                "stop_premium": new_stop,
                "initial_stop": new_stop,
                "target_premium": new_target,
                "risk_per_unit": 1.5,
                "best_premium": new_entry,
                "cost_estimate": costs_per_trade,
                "unrealized": -costs_per_trade,
            }
            s["trade_count"] += 1
            
        elif pos["premium"] <= pos["stop_premium"]:
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
            stop_distance = 1.5
            risk_amount_per_lot = stop_distance * lot_size
            if risk_amount_per_lot <= risk_budget and capital >= base_premium * lot_size:
                entry = max(1.0, base_premium + np.random.normal(0, 3))
                stop = max(1.0, entry - 1.5)
                target = entry + 6.0
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
                    "risk_per_unit": 1.5,
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
m4.metric("Candle timer", f'{s["candle_seconds"] - s["ticks_in_candle"]:02d}s')

if s["opening_high"] is not None:
    st.write(f'**Opening Range:** {s["opening_low"]:,.2f} – {s["opening_high"]:,.2f}')
else:
    st.write("**Opening Range:** Waiting for opening range establishment.")

if display_signal == "BUY CALL":
    st.success("🟢 BUY CALL — Momentum Breakout Confirmed")
elif display_signal == "BUY PUT":
    st.error("🔴 BUY PUT — Momentum Breakdown Confirmed")
elif display_signal.startswith("POSITION OPEN"):
    st.info(f"📍 {display_signal}")
else:
    st.warning("⏸ WAITING FOR CLEAN MOMENTUM")
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
    p5.metric("Stop-loss", f'₹{pos["stop_premium"]:.2f}')
    p6.metric("Target", f'₹{pos["target_premium"]:.2f}')
else:
    st.info("No open position.")

r1, r2 = st.columns(2)
r1.metric("Closed Session P&L", f'₹{s["daily_pnl"]:.2f}')
r2.metric("Total Trades Executed", f'{s["trade_count"]}')

if s["closed_trades"]:
    st.dataframe(pd.DataFrame(s["closed_trades"]), use_container_width=True, hide_index=True)

st.divider()
st.subheader("3. Historical Backtest Lab (Profit-Lock & Re-Entry Enabled)")
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
        session_trades_start = len(trades)

        for i in range(3, len(day)):
            row = day.iloc[i]
            exited_this_bar = False
            stamp = str(row["time"]) if "time" in day.columns else f"{session_key} candle {i+1}"

            if "time" in day.columns and pd.notna(row["time"]):
                if row["time"].time() >= dtime(cut_h, cut_m):
                    pending = None

            if pending is not None and active is None:
                entry = float(row["open"]) + (slippage_points if pending["side"] == "LONG" else -slippage_points)
                stop_distance = 1.5
                target_distance = 6.0
                active = {
                    "side": pending["side"], "entry": entry, "entry_time": pending["time"],
                    "stop": entry - stop_distance if pending["side"] == "LONG" else entry + stop_distance,
                    "target": entry + target_distance if pending["side"] == "LONG" else entry - target_distance,
                }
                pending = None

            if active is not None:
                exit_price, exit_reason = None, None
                if active["side"] == "LONG":
                    if float(row["low"]) <= active["stop"]:
                        exit_price, exit_reason = active["stop"] - slippage_points, "Stop-loss"
                    elif float(row["high"]) >= active["target"]:
                        exit_price, exit_reason = active["target"] - slippage_points, "Target"
                else:
                    if float(row["high"]) >= active["stop"]:
                        exit_price, exit_reason = active["stop"] + slippage_points, "Stop-loss"
                    elif float(row["low"]) <= active["target"]:
                        exit_price, exit_reason = active["target"] + slippage_points, "Target"

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

            if daily_net <= -(capital_value * daily_loss_pct_value / 100):
                pending = None
                continue

            ema_fast_v = row["ema_fast"]
            ema_slow_v = row["ema_slow"]
            spread = abs(ema_fast_v - ema_slow_v)

            if active is None and pending is None and not exited_this_bar and spread >= min_spread:
                close = float(row["close"])
                long_signal = (
                    close > opening_high and close > float(row["vwap"])
                    and ema_fast_v > ema_slow_v and close > float(row["open"])
                )
                short_signal = (
                    close < opening_low and close < float(row["vwap"])
                    and ema_fast_v < ema_slow_v and close < float(row["open"])
                )
                if long_signal:
                    pending = {"side": "LONG", "time": stamp}
                elif short_signal:
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
                bt_min_spread = st.slider("Backtest Min EMA Spread", min_value=1.0, max_value=10.0, value=3.0, key="bt_spread")
            
            if st.button("▶️ Run Momentum Backtest", type="primary", key="run_bt"):
                res_df, sess_df, m_dd = _run_expert_backtest(raw_bt, bt_qty, 40.0, 1.0, 150000.0, 3.0, 14, bt_min_spread, cutoff_hour, cutoff_minute)
                if not res_df.empty:
                    st.metric("Total Net P&L", f"₹{res_df['Net P&L ₹'].sum():,.2f}")
                    st.metric("Total Trades Taken", len(res_df))
                    st.dataframe(res_df, use_container_width=True, hide_index=True)
                else:
                    st.info("No trades executed. Adjust the EMA spread filter if needed.")
    except Exception as e:
        st.error(f"Error: {e}")
