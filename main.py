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
MA_FAST = 10        # أسرع من السابق (كان 20)
MA_SLOW = 30        # أسرع من السابق (كان 50)
RSI_PERIOD = 14
RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25
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
        print(f"استثناء في إرسال تيليجرام: {e}")
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
    df['ma_fast'] = ta.trend.sma_indicator(df['close'], MA_FAST)
    df['ma_slow'] = ta.trend.sma_indicator(df['close'], MA_SLOW)
    df['rsi'] = ta.momentum.rsi(df['close'], RSI_PERIOD)
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], ATR_PERIOD)
    # MACD
    macd = ta.trend.MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    return df


def detect_signal(df, pair):
    if len(df) < MA_SLOW + 5:
        return None

    prev = df.iloc[-2]
    last = df.iloc[-1]

    golden_cross = (prev['ma_fast'] <= prev['ma_slow']) and (last['ma_fast'] > last['ma_slow'])
    death_cross  = (prev['ma_fast'] >= prev['ma_slow']) and (last['ma_fast'] < last['ma_slow'])

    if not golden_cross and not death_cross:
        return None

    rsi = last['rsi']
    if pd.isna(rsi):
        return None

    direction = "BUY" if golden_cross else "SELL"

    # فلتر RSI
    if direction == "BUY" and rsi > RSI_OVERBOUGHT:
        return None
    if direction == "SELL" and rsi < RSI_OVERSOLD:
        return None

    # فلتر MACD
    if direction == "BUY" and last['macd'] <= last['macd_signal']:
        return None
    if direction == "SELL" and last['macd'] >= last['macd_signal']:
        return None

    atr = last['atr']
    if pd.isna(atr) or atr <= 0:
        atr = last['close'] * 0.0005

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

    # رسالة اختبار
    test_msg = "✅ البوت يعمل الآن (استراتيجية سريعة) ويتابع الأسواق..."
    resp = send_telegram(test_msg)
    if resp:
        print(f"حالة إرسال تيليجرام: {resp.status_code} - {resp.text[:200]}")
    else:
        print("فشل إرسال رسالة اختبار تيليجرام")

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
            else:
                print(f"[{pair}] لا توجد إشارة حالياً")
        except Exception as e:
            print(f"خطأ في زوج {pair}: {e}")

    if not found_any_signal:
        print("انتهى البوت بدون العثور على أي إشارة.")
        send_telegram("ℹ️ لم تظهر أي توصية هذه الدورة.")


if __name__ == "__main__":
    main()
