import os
import requests
import pandas as pd
import numpy as np
import ta
import yfinance as yf
from datetime import datetime
from supabase import create_client

# ========== الإعدادات ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

PAIRS = [
    "GC=F",        # الذهب
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCAD=X"
]

TIMEFRAME = "5m"
PERIOD = "5d"
LOOKBACK = 5               # عدد الشموع لاختراق القمة/القاع
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
ATR_PERIOD = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.0

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
        return resp
    except Exception as e:
        print(f"استثناء تيليجرام: {e}")
        return None


def fetch_data(pair):
    try:
        df = yf.download(pair, interval=TIMEFRAME, period=PERIOD, progress=False)
        if not df.empty and len(df) >= 50:
            if len(df.columns) == 5:
                df.columns = ['open', 'high', 'low', 'close', 'volume']
            print(f"[{pair}] تم جلب {len(df)} شمعة من Yahoo")
            return df
    except Exception as e:
        print(f"[{pair}] فشل Yahoo: {e}")
    return None


def compute_indicators(df):
    df['rsi'] = ta.momentum.rsi(df['close'], RSI_PERIOD)
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], ATR_PERIOD)
    df['highest'] = df['high'].shift(1).rolling(LOOKBACK).max()
    df['lowest'] = df['low'].shift(1).rolling(LOOKBACK).min()
    return df


def detect_signal(df, pair):
    if len(df) < LOOKBACK + 5:
        return None

    last = df.iloc[-1]
    rsi = last['rsi']
    if pd.isna(rsi):
        return None

    highest = last['highest']
    lowest = last['lowest']
    if pd.isna(highest) or pd.isna(lowest):
        return None

    close = last['close']
    direction = None

    if close > highest and rsi < RSI_OVERBOUGHT:
        direction = "BUY"
    elif close < lowest and rsi > RSI_OVERSOLD:
        direction = "SELL"

    if direction is None:
        return None

    atr = last['atr']
    if pd.isna(atr) or atr <= 0:
        atr = close * 0.0005

    entry = close
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

    # رسالة اختبار صامتة
    send_telegram("✅ البوت يعمل ويتابع الأسواق...")

    found_any_signal = False
    for pair in PAIRS:
        try:
            df = fetch_data(pair)
            if df is None:
                continue

            df = compute_indicators(df)
            sig = detect_signal(df, pair)

            if sig:
                found_any_signal = True

                # تحديد نوع التوصية والاتجاه
                if sig['direction'] == "BUY":
                    operation = "🟢 شراء"
                    trend = "صاعد"
                else:
                    operation = "🔴 بيع"
                    trend = "نازل"

                # بناء الرسالة: الزوج كما هو (بدون ترجمة)
                msg = (
                    f"{operation} | {pair}\n"
                    f"الاتجاه: {trend}\n"
                    f"نقطة الدخول: {sig['entry']}\n"
                    f"وقف الخسارة: {sig['stop_loss']}\n"
                    f"الهدف: {sig['take_profit']}"
                )

                print(msg)
                send_telegram(msg)
                store_signal(sig)
            else:
                print(f"[{pair}] لا توجد إشارة حالياً")
        except Exception as e:
            print(f"خطأ في زوج {pair}: {e}")

    if not found_any_signal:
        print("انتهى البوت بدون العثور على أي إشارة.")


if __name__ == "__main__":
    main()
