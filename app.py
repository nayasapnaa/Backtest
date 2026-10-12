import streamlit as st
import pandas as pd
import numpy as np
import time
import re
from datetime import datetime, timedelta
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
    "vwap_num": 0.0,
    "vwap_den": 0.0,
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
    "signal_countdown": 0,
    "last_trade_bar": -999,
    "bar_index": 0,
    "day_id": 1,
    "history": [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

st.title("📈 NIFTY Paper Trading Lab")
st.caption("SIMULATION ONLY • Artificial prices and option premiums • No broker connection • No real orders")

speed = st.select_slider(
    "⚡ Fast play speed",
    options=[1, 2, 5, 10, 20, 40, 60],
    value=1,
    format_func=lambda x: f"{x}x",
    help="When running, process this many simulated ticks per refresh."
)

with st.expander("⚙️ Risk settings", expanded=False):
    capital = st.number_input("Practice capital (₹)", min_value=10000, max_value=25000, value=15000, step=1000)
    risk_pct = st.select_slider("Risk budget per trade (%)", options=[1.0, 1.5, 2.0], value=1.5)
    daily_loss_pct = st.select_slider("Daily loss limit (%)", options=[1.0, 1.5, 2.0], value=1.5)
    max_trades = st.select_slider("Maximum trades per session", options=[1, 2, 3, 4], value=4)
    target_r = st.select_slider("Target (R multiple)", options=[1.0, 1.5, 2.0], value=1.5)
    trail_on = st.checkbox("Use trailing stop", value=True)
    auto_execute = st.checkbox("Automatic paper execution on confirmed signal", value=True)
    lot_size = 75
    st.info("Fixed simulated quantity: 75 units = 1 batch")
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
        # force close current candle and create next candle in this rerun
        st.session_state.ticks_in_candle = st.session_state.candle_seconds - 1
        st.session_state.running = False

st.info("Practice mode: each simulated 30-second candle represents one 5-minute candle. The prices are generated for training and are not NIFTY market quotes.")

# ---------- Simulated market engine ----------
def calculate_vwap(candles):
    if not candles:
        return None
    df = pd.DataFrame(candles)
    vol = df["volume"].sum()
    if vol <= 0:
        return float(df["close"].mean())
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return float((typical * df["volume"]).sum() / vol)

def signal_for_state():
    s = st.session_state
    if s["bar_index"] < 3 or s["opening_high"] is None:
        return "NO TRADE", "Waiting for the first 3 completed opening candles."
    candles = s["candles"]
    last = candles[-1]
    vwap = calculate_vwap(candles)
    prev = candles[-2] if len(candles) > 1 else last
    if vwap is None:
        return "NO TRADE", "Waiting for enough price data."
    bullish = last["close"] > s["opening_high"] and last["close"] > vwap and last["close"] > prev["close"]
    bearish = last["close"] < s["opening_low"] and last["close"] < vwap and last["close"] < prev["close"]
    if bullish:
        return "BUY CALL", "Simulated candle closed above the opening range and VWAP with bullish follow-through."
    if bearish:
        return "BUY PUT", "Simulated candle closed below the opening range and VWAP with bearish follow-through."
    return "NO TRADE", "No confirmed breakout with VWAP and candle follow-through."

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
    # Deterministic random-ish market movement, no internet/live feed.
    t = s["ticks_in_candle"]
    trend_cycle = np.sin((s["bar_index"] + t / s["candle_seconds"]) / 3.7) * 0.75
    shock = np.sin((s["bar_index"] * 7 + t * 3) / 5.0) * 0.9
    drift = trend_cycle + shock + np.random.normal(0, 1.25)
    new_price = max(1000, s["price"] + drift)
    s["prev_close"] = s["price"]
    s["price"] = float(new_price)
    s["sim_time"] += timedelta(seconds=1)
    s["ticks_in_candle"] += 1

    # Update the in-progress candle.
    if not s["candles"] or s["ticks_in_candle"] == 1:
        s["candles"].append({"time": s["sim_time"], "open": s["prev_close"], "high": max(s["prev_close"], s["price"]),
                             "low": min(s["prev_close"], s["price"]), "close": s["price"], "volume": int(np.random.randint(50, 250))})
    else:
        cur = s["candles"][-1]
        cur["high"] = max(cur["high"], s["price"])
        cur["low"] = min(cur["low"], s["price"])
        cur["close"] = s["price"]
        cur["volume"] += int(np.random.randint(10, 80))

    # Update simulated option premium on every tick while position open.
    if s["position"]:
        pos = s["position"]
        underlying_change = s["price"] - pos["last_underlying"]
        direction = 1.0 if pos["side"] == "CALL" else -1.0
        option_move = direction * underlying_change * 0.035 * premium_sensitivity
        # Add a small independent premium wobble to represent imperfect tracking.
        option_move += np.random.normal(0, 0.18)
        pos["premium"] = max(1.0, pos["premium"] + option_move)
        pos["last_underlying"] = s["price"]
        # Update trailing stop in premium terms.
        if trail_on and pos["premium"] > pos["best_premium"]:
            pos["best_premium"] = pos["premium"]
            pos["stop_premium"] = max(pos["stop_premium"], pos["best_premium"] - pos["risk_per_unit"])
        # Mark-to-market P&L, after estimated round-trip cost.
        pos["unrealized"] = (pos["premium"] - pos["entry_premium"]) * pos["quantity"] - pos["cost_estimate"]
        if pos["premium"] <= pos["stop_premium"]:
            close_position("Stop-loss / trailing stop", pos["premium"])
        elif pos["premium"] >= pos["target_premium"]:
            close_position("Target hit", pos["premium"])

    if s["ticks_in_candle"] >= s["candle_seconds"]:
        # Candle complete
        s["bar_index"] += 1
        s["ticks_in_candle"] = 0
        if s["bar_index"] == 3:
            opening = s["candles"][:3]
            s["opening_high"] = max(c["high"] for c in opening)
            s["opening_low"] = min(c["low"] for c in opening)
        signal, reason = signal_for_state()
        s["last_signal"], s["last_reason"] = signal, reason

        # Auto paper-entry only at completed candle and only one position at a time.
        halted = s["daily_pnl"] <= -daily_limit or s["trade_count"] >= max_trades or s["consecutive_losses"] >= 2
        if (auto_execute and not halted and s["position"] is None and signal in ("BUY CALL", "BUY PUT")
                and s["bar_index"] - s["last_trade_bar"] >= 2):
            # Use one simulated batch (75 units). Stop distance and target are defined in option premium points.
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
            else:
                s["last_reason"] += " Risk/capital check rejected entry; no trade opened."
        if s["daily_pnl"] <= -daily_limit:
            s["running"] = False
            s["last_signal"] = "NO TRADE"
            s["last_reason"] = "Daily loss limit reached. Simulation paused."
        if s["trade_count"] >= max_trades:
            s["last_signal"] = "NO TRADE"
            s["last_reason"] = "Maximum trades reached for this session."

# Auto-refresh every second. If paused, dashboard remains still.
if st.session_state.running:
    # One UI refresh per second; Fast Play advances multiple simulated ticks per refresh.
    st_autorefresh(interval=1000, key="simulator_tick")
    for _ in range(int(speed)):
        if not st.session_state.running:
            break
        process_tick()
        # Do not continue advancing ticks after a risk limit pauses the session.
        if not st.session_state.running:
            break

s = st.session_state
vwap = calculate_vwap(s["candles"])
signal, reason = signal_for_state() if s["bar_index"] > 0 else (s["last_signal"], s["last_reason"])
if s["position"] and s["position"]["side"] == "CALL":
    # Keep the signal panel focused on current open position; no fresh entry while in trade.
    display_signal = "POSITION OPEN — CALL"
elif s["position"] and s["position"]["side"] == "PUT":
    display_signal = "POSITION OPEN — PUT"
else:
    display_signal = s["last_signal"] if s["bar_index"] > 0 else "NO TRADE"

st.subheader("1. Market simulator")
m1, m2, m3 = st.columns(3)
m1.metric("Simulated NIFTY", f'{s["price"]:,.2f}', f'{s["price"]-s["prev_close"]:+.2f}')
m2.metric("VWAP (simulated)", f'{vwap:,.2f}' if vwap else "—")
m3.metric("Candle timer", f'{s["candle_seconds"] - s["ticks_in_candle"]:02d}s')
st.caption(f'Simulated time: {s["sim_time"].strftime("%H:%M:%S")} • Completed simulated 5-minute candles: {s["bar_index"]} • {"RUNNING" if s["running"] else "PAUSED"}')

if s["opening_high"] is not None:
    st.write(f'**Opening range:** {s["opening_low"]:,.2f} – {s["opening_high"]:,.2f}')
else:
    st.write("**Opening range:** waiting for 3 completed opening candles.")

if display_signal == "BUY CALL":
    st.success("🟢 BUY CALL — confirmed simulated setup")
elif display_signal == "BUY PUT":
    st.error("🔴 BUY PUT — confirmed simulated setup")
elif display_signal.startswith("POSITION OPEN"):
    st.info(f"📍 {display_signal}")
else:
    st.warning("⏸ NO TRADE — wait for confirmation")
st.write(s["last_reason"])

# Chart
if s["candles"]:
    cdf = pd.DataFrame(s["candles"])
    cdf["time"] = pd.to_datetime(cdf["time"])
    cdf = cdf.tail(60).set_index("time")
    chart_df = cdf[["close"]].copy()
    if vwap is not None:
        chart_df["VWAP"] = vwap
    st.line_chart(chart_df, height=230)
    with st.expander("View latest candles"):
        st.dataframe(cdf.tail(12).reset_index(), use_container_width=True, hide_index=True)

st.divider()
st.subheader("2. Open paper trade")
pos = s["position"]
if pos:
    current_pnl = (pos["premium"] - pos["entry_premium"]) * pos["quantity"] - pos["cost_estimate"]
    p1, p2, p3 = st.columns(3)
    p1.metric("Option", f'BUY {pos["side"]}')
    p2.metric("Entry premium", f'₹{pos["entry_premium"]:.2f}')
    p3.metric("Current premium", f'₹{pos["premium"]:.2f}')
    p4, p5, p6 = st.columns(3)
    p4.metric("Running P&L", f'₹{current_pnl:.2f}')
    p5.metric("Stop-loss", f'₹{pos["stop_premium"]:.2f}')
    p6.metric("Target", f'₹{pos["target_premium"]:.2f}')
    st.caption(f'Quantity: {pos["quantity"]} (1 batch = 75 simulated units) • Entry underlying: {pos["entry_underlying"]:.2f} • Estimated cost deducted from P&L.')
else:
    st.info("No open position. Automatic paper execution waits for a confirmed signal, risk approval, and a free trade slot.")

st.subheader("3. Session risk")
r1, r2, r3 = st.columns(3)
r1.metric("Closed P&L", f'₹{s["daily_pnl"]:.2f}')
running_pnl = ((s["position"]["premium"] - s["position"]["entry_premium"]) * s["position"]["quantity"] - s["position"]["cost_estimate"]) if s["position"] else 0.0
r2.metric("Running P&L", f'₹{running_pnl:.2f}')
r3.metric("Trades opened", f'{s["trade_count"]}/{max_trades}')
st.progress(min(1.0, abs(min(0.0, s["daily_pnl"])) / daily_limit), text=f'Daily loss limit: ₹{daily_limit:.0f} • Two consecutive losses pauses new entries.')

st.subheader("4. Closed-trade log")
if s["closed_trades"]:
    log = pd.DataFrame(s["closed_trades"])
    st.dataframe(log, use_container_width=True, hide_index=True)
    st.download_button("Download trade log CSV", data=log.to_csv(index=False).encode("utf-8"),
                       file_name="paper_trade_log.csv", mime="text/csv")
else:
    st.info("No closed trades yet. Keep the simulation running to see stop-loss or target exits.")

st.divider()
st.subheader("5. Historical CSV backtest")
st.write(
    "Backtest an intraday Opening Range Breakout (ORB) with session VWAP, EMA trend confirmation, "
    "and an ATR volatility filter. This section trades the uploaded underlying/index candles, not real options."
)
st.caption(
    "For best results upload 1-minute or 5-minute OHLCV candles with a timestamp column. "
    "Each calendar date is treated as a separate session; timestamps should be in exchange-local time."
)

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

def _run_orb_backtest(frame, qty, cost_per_trade, slippage_points,
                      capital_value, daily_loss_pct_value, max_trades_day,
                      atr_period, atr_stop_mult, target_r, min_atr):
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
        if len(day) < max(atr_period + 3, 5):
            continue

        # Indicators use only this session's data; no VWAP carry-over across days.
        typical = (day["high"] + day["low"] + day["close"]) / 3.0
        vol = day["volume"].clip(lower=0)
        cumulative_volume = vol.cumsum()
        day["vwap"] = ((typical * vol).cumsum() / cumulative_volume.replace(0, np.nan))
        day["vwap"] = day["vwap"].fillna(day["close"].expanding().mean())
        day["ema_fast"] = day["close"].ewm(span=9, adjust=False).mean()
        day["ema_slow"] = day["close"].ewm(span=21, adjust=False).mean()
        previous_close = day["close"].shift(1)
        true_range = pd.concat([
            day["high"] - day["low"],
            (day["high"] - previous_close).abs(),
            (day["low"] - previous_close).abs()
        ], axis=1).max(axis=1)
        day["atr"] = true_range.rolling(atr_period, min_periods=atr_period).mean()

        # Opening range is the first three candles of each session.
        opening_high = float(day.loc[:2, "high"].max())
        opening_low = float(day.loc[:2, "low"].min())
        active = None
        pending = None
        trades_today = 0
        daily_net = 0.0
        session_trades_start = len(trades)

        for i in range(3, len(day)):
            row = day.iloc[i]
            exited_this_bar = False
            stamp = str(row["time"]) if "time" in day.columns else f"{session_key} candle {i+1}"

            # Entry is executed at the NEXT candle open after a closed-candle signal.
            if pending is not None and active is None and trades_today < max_trades_day:
                entry = float(row["open"]) + (slippage_points if pending["side"] == "LONG" else -slippage_points)
                atr_value = float(pending["atr"])
                stop_distance = max(atr_value * atr_stop_mult, 0.01)
                target_distance = stop_distance * target_r
                active = {
                    "side": pending["side"], "entry": entry, "entry_time": pending["time"],
                    "stop": entry - stop_distance if pending["side"] == "LONG" else entry + stop_distance,
                    "target": entry + target_distance if pending["side"] == "LONG" else entry - target_distance,
                    "risk_points": stop_distance
                }
                trades_today += 1
                pending = None

            # Exit checks on subsequent candle high/low; if both touched, assume stop first.
            if active is not None:
                exit_price, exit_reason = None, None
                if active["side"] == "LONG":
                    if float(row["open"]) <= active["stop"]:
                        exit_price, exit_reason = float(row["open"]) - slippage_points, "Stop gap"
                    elif float(row["open"]) >= active["target"]:
                        exit_price, exit_reason = float(row["open"]) - slippage_points, "Target gap"
                    elif float(row["low"]) <= active["stop"]:
                        exit_price, exit_reason = active["stop"] - slippage_points, "Stop-loss"
                    elif float(row["high"]) >= active["target"]:
                        exit_price, exit_reason = active["target"] - slippage_points, "Target"
                else:
                    if float(row["open"]) >= active["stop"]:
                        exit_price, exit_reason = float(row["open"]) + slippage_points, "Stop gap"
                    elif float(row["open"]) <= active["target"]:
                        exit_price, exit_reason = float(row["open"]) + slippage_points, "Target gap"
                    elif float(row["high"]) >= active["stop"]:
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
                        "Stop price": round(active["stop"], 2), "Target price": round(active["target"], 2),
                        "Quantity": int(qty), "Exit reason": exit_reason,
                        "Gross P&L ₹": round(gross, 2), "Costs ₹": float(cost_per_trade),
                        "Net P&L ₹": round(net, 2)
                    })
                    daily_net += net
                    equity += net
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, peak_equity - equity)
                    active = None
                    exited_this_bar = True

            # Avoid a new entry after the session's daily loss guard is breached.
            if daily_net <= -(capital_value * daily_loss_pct_value / 100):
                pending = None
                continue

            # Evaluate signals only after the candle is complete; place pending entry for next candle.
            atr_value = row["atr"]
            if active is None and pending is None and not exited_this_bar and trades_today < max_trades_day and pd.notna(atr_value):
                if float(atr_value) >= min_atr and i > 0:
                    close = float(row["close"])
                    prior_high = float(day.iloc[i - 1]["high"])
                    prior_low = float(day.iloc[i - 1]["low"])
                    long_signal = (
                        close > opening_high and close > float(row["vwap"])
                        and float(row["ema_fast"]) > float(row["ema_slow"])
                        and close > prior_high
                    )
                    short_signal = (
                        close < opening_low and close < float(row["vwap"])
                        and float(row["ema_fast"]) < float(row["ema_slow"])
                        and close < prior_low
                    )
                    if long_signal:
                        pending = {"side": "LONG", "atr": float(atr_value), "time": stamp}
                    elif short_signal:
                        pending = {"side": "SHORT", "atr": float(atr_value), "time": stamp}

        # Flatten any open trade at the last close of the session (no overnight positions).
        if active is not None:
            last = day.iloc[-1]
            stamp = str(last["time"]) if "time" in day.columns else f"{session_key} session close"
            exit_price = float(last["close"]) - (slippage_points if active["side"] == "LONG" else -slippage_points)
            sign = 1 if active["side"] == "LONG" else -1
            gross = (exit_price - active["entry"]) * int(qty) * sign
            net = gross - float(cost_per_trade)
            trades.append({
                "Session": str(session_key), "Side": active["side"],
                "Entry time": active["entry_time"], "Exit time": stamp,
                "Entry price": round(active["entry"], 2), "Exit price": round(exit_price, 2),
                "Stop price": round(active["stop"], 2), "Target price": round(active["target"], 2),
                "Quantity": int(qty), "Exit reason": "Session end",
                "Gross P&L ₹": round(gross, 2), "Costs ₹": float(cost_per_trade),
                "Net P&L ₹": round(net, 2)
            })
            daily_net += net
            equity += net
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)

        sessions.append({"Session": str(session_key), "Trades": len(trades) - session_trades_start, "Net P&L ₹": round(daily_net, 2)})

    return pd.DataFrame(trades), pd.DataFrame(sessions), max_drawdown

if uploaded_csv is not None:
    try:
        raw_bt = _normalise_columns(pd.read_csv(uploaded_csv))
        required = {"open", "high", "low", "close"}
        if not required.issubset(raw_bt.columns):
            st.error("CSV must contain open, high, low, close. A date/time or timestamp column is strongly recommended.")
        else:
            time_col = next((c for c in ["time", "date"] if c in raw_bt.columns), None)
            if time_col:
                raw_bt["time"] = pd.to_datetime(raw_bt[time_col], errors="coerce")
                raw_bt = raw_bt.dropna(subset=["time"])
            else:
                st.warning("No timestamp found: the entire file will be treated as one session. Multi-day results will be invalid unless dates are provided.")

            for col in ["open", "high", "low", "close"]:
                raw_bt[col] = pd.to_numeric(raw_bt[col], errors="coerce")
            if "volume" not in raw_bt.columns:
                raw_bt["volume"] = 1.0
            raw_bt["volume"] = pd.to_numeric(raw_bt["volume"], errors="coerce").fillna(0).clip(lower=0)
            raw_bt = raw_bt.dropna(subset=["open", "high", "low", "close"])
            invalid_ohlc = (
                (raw_bt["high"] < raw_bt[["open", "close", "low"]].max(axis=1)) |
                (raw_bt["low"] > raw_bt[["open", "close", "high"]].min(axis=1)) |
                (raw_bt[["open", "high", "low", "close"]] <= 0).any(axis=1)
            )
            raw_bt = raw_bt.loc[~invalid_ohlc].reset_index(drop=True)

            if len(raw_bt) < 30:
                st.error("At least 30 valid candle rows are required for indicator warm-up and testing.")
            else:
                with st.expander("Backtest settings", expanded=True):
                    bt_qty = st.number_input("Underlying quantity (units)", min_value=1, max_value=100000, value=75, step=1, key="bt_qty")
                    bt_cost = st.number_input("Estimated total cost per round trip (₹)", min_value=0.0, max_value=10000.0, value=40.0, step=5.0, key="bt_cost")
                    bt_slippage = st.number_input("Slippage per side (underlying points)", min_value=0.0, max_value=100.0, value=1.0, step=0.25, key="bt_slippage")
                    bt_capital = st.number_input("Backtest reference capital (₹)", min_value=1000.0, max_value=100000000.0, value=150000.0, step=10000.0, key="bt_capital")
                    bt_daily_loss = st.select_slider("Daily loss guard (%)", options=[0.5, 1.0, 1.5, 2.0, 3.0], value=1.5, key="bt_daily_loss")
                    bt_max_trades = st.select_slider("Maximum trades per day", options=[1, 2, 3, 4, 5], value=2, key="bt_max_trades")
                    bt_atr_period = st.select_slider("ATR period", options=[7, 10, 14, 20], value=14, key="bt_atr_period")
                    bt_atr_mult = st.select_slider("Stop distance (ATR multiple)", options=[1.0, 1.25, 1.5, 2.0, 2.5], value=1.5, key="bt_atr_mult")
                    bt_target_r = st.select_slider("Target (R multiple)", options=[1.0, 1.25, 1.5, 2.0, 2.5, 3.0], value=1.5, key="bt_target_r")
                    bt_min_atr = st.number_input("Minimum ATR filter (points)", min_value=0.0, max_value=1000.0, value=0.0, step=1.0, key="bt_min_atr")

                if st.button("▶️ Run backtest", type="primary", key="run_backtest"):
                    trades_df, sessions_df, max_dd = _run_orb_backtest(
                        raw_bt, bt_qty, bt_cost, bt_slippage, bt_capital,
                        bt_daily_loss, bt_max_trades, bt_atr_period, bt_atr_mult,
                        bt_target_r, bt_min_atr
                    )
                    st.session_state["backtest_results"] = trades_df
                    st.session_state["backtest_sessions"] = sessions_df
                    st.session_state["backtest_max_drawdown"] = max_dd
                    st.session_state["backtest_signature"] = (
                        len(raw_bt),
                        str(raw_bt["time"].iloc[0]) if "time" in raw_bt.columns else "single_session",
                        str(raw_bt["time"].iloc[-1]) if "time" in raw_bt.columns else "single_session",
                        int(pd.util.hash_pandas_object(raw_bt, index=True).sum())
                    )

                signature = (
                    len(raw_bt),
                    str(raw_bt["time"].iloc[0]) if "time" in raw_bt.columns else "single_session",
                    str(raw_bt["time"].iloc[-1]) if "time" in raw_bt.columns else "single_session",
                    int(pd.util.hash_pandas_object(raw_bt, index=True).sum())
                )
                if st.session_state.get("backtest_signature") == signature:
                    result = st.session_state.get("backtest_results", pd.DataFrame())
                    sessions_result = st.session_state.get("backtest_sessions", pd.DataFrame())
                    st.caption(
                        f"Opening range: first 3 candles of each session • Valid candles: {len(raw_bt)} "
                        f"• Sessions: {raw_bt['time'].dt.date.nunique() if 'time' in raw_bt.columns else 1}"
                    )
                    if result.empty:
                        st.info("No trades were generated. Check candle timeframe, data quality, and filters.")
                    else:
                        net_total = float(result["Net P&L ₹"].sum())
                        wins = int((result["Net P&L ₹"] > 0).sum())
                        losses = int((result["Net P&L ₹"] < 0).sum())
                        gross_profit = float(result.loc[result["Net P&L ₹"] > 0, "Net P&L ₹"].sum())
                        gross_loss = abs(float(result.loc[result["Net P&L ₹"] < 0, "Net P&L ₹"].sum()))
                        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
                        avg_trade = float(result["Net P&L ₹"].mean())
                        win_rate = wins / len(result) * 100
                        m1, m2, m3 = st.columns(3)
                        m1.metric("Net P&L", f"₹{net_total:,.2f}")
                        m2.metric("Trades / Win rate", f"{len(result)} / {win_rate:.1f}%")
                        m3.metric("Profit factor", "∞" if np.isinf(profit_factor) else f"{profit_factor:.2f}")
                        m4, m5, m6 = st.columns(3)
                        m4.metric("Average net trade", f"₹{avg_trade:,.2f}")
                        m5.metric("Max drawdown*", f"₹{st.session_state.get('backtest_max_drawdown', 0):,.2f}")
                        m6.metric("Winning / losing trades", f"{wins} / {losses}")
                        st.caption("*Drawdown is computed on closed-trade equity; open-trade intrabar drawdown is not included.")
                        st.dataframe(result, use_container_width=True, hide_index=True)
                        st.download_button(
                            "Download backtest trades CSV",
                            data=result.to_csv(index=False).encode("utf-8"),
                            file_name="backtest_trades.csv", mime="text/csv", key="download_backtest_trades"
                        )
                        if not sessions_result.empty:
                            with st.expander("Session-by-session results"):
                                st.dataframe(sessions_result, use_container_width=True, hide_index=True)
                                st.download_button(
                                    "Download session summary CSV",
                                    data=sessions_result.to_csv(index=False).encode("utf-8"),
                                    file_name="backtest_sessions.csv", mime="text/csv", key="download_backtest_sessions"
                                )
                    st.warning(
                        "Backtest caveats: this is a rule-based hypothesis, not a proven profitable strategy. "
                        "Entries use next-candle open, slippage and round-trip costs are configurable, and if stop/target "
                        "are both touched in one candle the stop is assumed first. Test on multiple periods and out-of-sample data."
                    )

    except Exception as e:
        st.error(f"Could not process this CSV: {e}")

st.caption("Important: This is an educational toy simulator. Both underlying prices and option premiums are artificial. It does not model real option-chain prices, implied volatility, gamma, theta, spreads, liquidity, or real fills. P&L is not evidence of profitability. Never use it as a live trading signal.")
