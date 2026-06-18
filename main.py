import os
import requests
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import yfinance as yf
import ta
from supabase import create_client

# ========== الإعدادات (ستُسحب من أسرار GitHub) ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# قائمة الأزواج (صيغة Yahoo Finance)
PAIRS = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCAD=X",
]

TIMEFRAME = "5m"          # شمعة كل 5 دقائق
PERIOD = "5d"             # عدد الأيام للتحليل
MA_FAST = 20
MA_SLOW = 50
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70      # عدم شراء إذا RSI > 70
ATR_PERIOD = 14
ATR_SL_MULT = 1.5        # وقف الخسارة = ATR × 1.5
ATR_TP_MULT = 2.0        # الهدف = ATR × 2.0

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

def fetch_data(pair):
    """ جلب بيانات زوج عملات (يمكن استبداله بـ MT5 لاحقاً) """
    df = yf.download(pair, interval=TIMEFRAME, period=PERIOD, progress=False)
    if df.empty:
        return None
    if len(df.columns) == 5:
        df.columns = ['open','high','low','close','volume']
    return df

def compute_indicators(df):
    df['ma_fast'] = ta.trend.sma_indicator(df['close'], MA_FAST)
    df['ma_slow'] = ta.trend.sma_indicator(df['close'], MA_SLOW)
    df['rsi'] = ta.momentum.rsi(df['close'], RSI_PERIOD)
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], ATR_PERIOD)
    return df

def detect_signal(df):
    if len(df) < max(MA_FAST, MA_SLOW, RSI_PERIOD, ATR_PERIOD) + 2:
        return None

    prev = df.iloc[-2]
    last = df.iloc[-1]

    # شرط التقاطع
    golden_cross = (prev['ma_fast'] <= prev['ma_slow']) and (last['ma_fast'] > last['ma_slow'])
    death_cross  = (prev['ma_fast'] >= prev['ma_slow']) and (last['ma_fast'] < last['ma_slow'])

    if not golden_cross and not death_cross:
        return None

    # فلتر RSI
    rsi = last['rsi']
    if pd.isna(rsi):
        return None

    direction = "BUY" if golden_cross else "SELL"

    # شراء: RSI يجب ألا يكون في تشبع شراء (>70)
    if direction == "BUY" and rsi > RSI_OVERBOUGHT:
        return None
    # بيع: RSI يجب ألا يكون في تشبع بيع (<30) اختياري
    if direction == "SELL" and rsi < (100 - RSI_OVERBOUGHT):
        return None

    atr = last['atr']
    if pd.isna(atr) or atr <= 0:
        return None

    entry = last['close']
    if direction == "BUY":
        sl = entry - ATR_SL_MULT * atr
        tp = entry + ATR_TP_MULT * atr
    else:
        sl = entry + ATR_SL_MULT * atr
        tp = entry - ATR_TP_MULT * atr

    return {
        "pair": pair,
        "direction": direction,
        "entry": round(entry, 5),
        "stop_loss": round(sl, 5),
        "take_profit": round(tp, 5),
        "atr": round(atr, 5),
        "rsi": round(rsi, 2),
        "timestamp": datetime.utcnow().isoformat()
    }

def store_signal(signal):
    """ تخزين الإشارة في Supabase """
    try:
        supabase.table("signals").insert({
            "pair": signal["pair"],
            "direction": signal["direction"],
            "entry": signal["entry"],
            "sl": signal["stop_loss"],
            "tp": signal["take_profit"],
            "atr": signal["atr"],
            "rsi": signal["rsi"],
            "created_at": signal["timestamp"]
        }).execute()
    except Exception as e:
        print("فشل تخزين الإشارة:", e)

def main():
    print(f"تشغيل البوت في {datetime.utcnow()}")
    for pair in PAIRS:
        try:
            df = fetch_data(pair)
            if df is None:
                continue
            df = compute_indicators(df)
            sig = detect_signal(df)
            if sig:
                msg = (
                    f"🚨 توصية {sig['direction']} على {pair}\n"
                    f"الدخول: {sig['entry']}\n"
                    f"وقف الخسارة: {sig['stop_loss']}\n"
                    f"الهدف: {sig['take_profit']}\n"
                    f"ATR: {sig['atr']} | RSI: {sig['rsi']}\n"
                    f"الوقت: {sig['timestamp']}"
                )
                print(msg)
                send_telegram(msg)
                store_signal(sig)
        except Exception as e:
            print(f"خطأ في زوج {pair}: {e}")

if __name__ == "__main__":
    main()
