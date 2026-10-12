import streamlit as st
import pandas as pd
import numpy as np
import time
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
        option_move = underlying_change * 0.035 * premium_sensitivity
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
    st_autorefresh(interval=1000, key="simulator_tick")
    for _ in range(int(speed)):
        if not st.session_state.running:
            break
        process_tick()

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
    "Upload historical candle data to test the opening-range + VWAP rule on past data. "
    "Required columns: `open`, `high`, `low`, `close`; `volume` is optional. "
    "A `time` or `date` column is optional. Column names are case-insensitive."
)
uploaded_csv = st.file_uploader("Upload backtest CSV", type=["csv"], key="backtest_csv")
if uploaded_csv is not None:
    try:
        raw_bt = pd.read_csv(uploaded_csv)
        raw_bt.columns = [str(c).strip().lower() for c in raw_bt.columns]
        required = {"open", "high", "low", "close"}
        if not required.issubset(raw_bt.columns):
            st.error("CSV must contain these columns: open, high, low, close.")
        elif len(raw_bt) < 4:
            st.error("Please upload at least 4 candle rows.")
        else:
            bt = raw_bt.copy()
            for col in ["open", "high", "low", "close"]:
                bt[col] = pd.to_numeric(bt[col], errors="coerce")
            if "volume" not in bt.columns:
                bt["volume"] = 1.0
            bt["volume"] = pd.to_numeric(bt["volume"], errors="coerce").fillna(0).clip(lower=0)
            bt = bt.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
            if len(bt) < 4:
                st.error("Not enough valid OHLC rows after cleaning the CSV.")
            else:
                bt["typical"] = (bt["high"] + bt["low"] + bt["close"]) / 3
                vol_sum = bt["volume"].cumsum().replace(0, np.nan)
                bt["vwap"] = ((bt["typical"] * bt["volume"]).cumsum() / vol_sum).fillna(bt["close"].expanding().mean())
                opening_high = float(bt.loc[:2, "high"].max())
                opening_low = float(bt.loc[:2, "low"].min())
                stop_points = st.number_input("Backtest stop distance (underlying points)", min_value=1.0, max_value=1000.0, value=20.0, step=1.0, key="bt_stop")
                target_points = st.number_input("Backtest target distance (underlying points)", min_value=1.0, max_value=2000.0, value=30.0, step=1.0, key="bt_target")
                bt_cost = st.number_input("Cost per completed trade (₹)", min_value=0.0, max_value=10000.0, value=20.0, step=5.0, key="bt_cost")
                bt_qty = st.number_input("Backtest quantity", min_value=1, max_value=100000, value=75, step=1, key="bt_qty")
                if st.button("▶️ Run backtest", type="primary", key="run_backtest"):
                    bt_trades = []
                    active = None
                    for i in range(3, len(bt)):
                        row = bt.iloc[i]
                        prev_close = float(bt.iloc[i - 1]["close"])
                        close = float(row["close"])
                        signal = None
                        if close > opening_high and close > float(row["vwap"]) and close > prev_close:
                            signal = "LONG"
                        elif close < opening_low and close < float(row["vwap"]) and close < prev_close:
                            signal = "SHORT"

                        stamp_col = "time" if "time" in bt.columns else ("date" if "date" in bt.columns else None)
                        stamp = str(row[stamp_col]) if stamp_col else str(i + 1)

                        if active:
                            exit_price = None
                            exit_reason = ""
                            if active["side"] == "LONG":
                                if float(row["low"]) <= active["stop"]:
                                    exit_price, exit_reason = active["stop"], "Stop-loss"
                                elif float(row["high"]) >= active["target"]:
                                    exit_price, exit_reason = active["target"], "Target"
                            else:
                                if float(row["high"]) >= active["stop"]:
                                    exit_price, exit_reason = active["stop"], "Stop-loss"
                                elif float(row["low"]) <= active["target"]:
                                    exit_price, exit_reason = active["target"], "Target"
                            if exit_price is not None:
                                gross = (exit_price - active["entry"]) * bt_qty * (1 if active["side"] == "LONG" else -1)
                                bt_trades.append({
                                    "Side": active["side"], "Entry time": active["entry_time"], "Exit time": stamp,
                                    "Entry price": round(active["entry"], 2), "Exit price": round(exit_price, 2),
                                    "Quantity": int(bt_qty), "Exit reason": exit_reason,
                                    "Gross P&L ₹": round(gross, 2), "Costs ₹": float(bt_cost),
                                    "Net P&L ₹": round(gross - bt_cost, 2)
                                })
                                active = None

                        if active is None and signal:
                            active = {
                                "side": signal, "entry": close, "entry_time": stamp,
                                "stop": close - stop_points if signal == "LONG" else close + stop_points,
                                "target": close + target_points if signal == "LONG" else close - target_points,
                            }

                    if active:
                        last_row = bt.iloc[-1]
                        stamp_col = "time" if "time" in bt.columns else ("date" if "date" in bt.columns else None)
                        stamp = str(last_row[stamp_col]) if stamp_col else str(len(bt))
                        final_price = float(last_row["close"])
                        gross = (final_price - active["entry"]) * bt_qty * (1 if active["side"] == "LONG" else -1)
                        bt_trades.append({
                            "Side": active["side"], "Entry time": active["entry_time"], "Exit time": stamp,
                            "Entry price": round(active["entry"], 2), "Exit price": round(final_price, 2),
                            "Quantity": int(bt_qty), "Exit reason": "End of data",
                            "Gross P&L ₹": round(gross, 2), "Costs ₹": float(bt_cost),
                            "Net P&L ₹": round(gross - bt_cost, 2)
                        })

                    result = pd.DataFrame(bt_trades)
                    st.session_state["backtest_results"] = result
                    st.session_state["backtest_candle_count"] = len(bt)
                    st.session_state["backtest_opening_range"] = (opening_low, opening_high)

                if "backtest_results" in st.session_state and st.session_state.get("backtest_candle_count") == len(bt):
                    result = st.session_state["backtest_results"]
                    st.caption(f"Opening range used: {opening_low:,.2f} – {opening_high:,.2f} • Candles loaded: {len(bt)}")
                    if result.empty:
                        st.info("No completed signals/trades were generated for this file and configuration.")
                    else:
                        net_total = float(result["Net P&L ₹"].sum())
                        wins = int((result["Net P&L ₹"] > 0).sum())
                        b1, b2, b3 = st.columns(3)
                        b1.metric("Backtest net P&L", f"₹{net_total:,.2f}")
                        b2.metric("Trades", str(len(result)))
                        b3.metric("Win rate", f"{wins / len(result) * 100:.1f}%")
                        st.dataframe(result, use_container_width=True, hide_index=True)
                        st.download_button(
                            "Download backtest results CSV",
                            data=result.to_csv(index=False).encode("utf-8"),
                            file_name="backtest_results.csv",
                            mime="text/csv",
                            key="download_backtest_results"
                        )
                    st.caption("Backtest assumptions: entries at candle close, stop/target checks use candle high/low, and if both levels are touched in one candle the stop is assumed first. Results exclude slippage and may not reflect real execution.")

    except Exception as e:
        st.error(f"Could not read this CSV: {e}")

st.caption("Important: This is an educational toy simulator. Both underlying prices and option premiums are artificial. It does not model real option-chain prices, implied volatility, gamma, theta, spreads, liquidity, or real fills. P&L is not evidence of profitability. Never use it as a live trading signal.")
