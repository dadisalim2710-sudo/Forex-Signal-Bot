import os
import requests
import pandas as pd
import numpy as np
import ta
import yfinance as yf
from datetime import datetime
from supabase import create_client

# ========== الإعدادات (تُسحب تلقائياً من أسرار GitHub) ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# قائمة الأزواج – الذهب الآن في المقدمة كما طلبت
PAIRS = [
    "XAUUSD=X",    # الذهب مقابل الدولار
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCAD=X"
]

TIMEFRAME = "5m"
PERIOD = "5d"
MA_FAST = 20
MA_SLOW = 50
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
ATR_PERIOD = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.0

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def send_telegram(msg):
    """إرسال رسالة إلى تيليجرام"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})


def fetch_data(pair):
    """جلب بيانات من Yahoo Finance"""
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
    """حساب المتوسطات و RSI و ATR"""
    df['ma_fast'] = ta.trend.sma_indicator(df['close'], MA_FAST)
    df['ma_slow'] = ta.trend.sma_indicator(df['close'], MA_SLOW)
    df['rsi'] = ta.momentum.rsi(df['close'], RSI_PERIOD)
    df['atr'] = ta.volatility.average_true_range(
        df['high'], df['low'], df['close'], ATR_PERIOD
    )
    return df


def detect_signal_test(df, pair):
    """
    إشارة سريعة للاختبار:
    - شراء: آخر 3 شموع كلها صاعدة (close > open) والسعر فوق المتوسط السريع.
    - بيع: آخر 3 شموع كلها هابطة (close < open) والسعر تحت المتوسط السريع.
    """
    if len(df) < 10:
        return None

    last3 = df.iloc[-3:]
    all_bullish = all(last3['close'] > last3['open'])
    all_bearish = all(last3['close'] < last3['open'])

    if not all_bullish and not all_bearish:
        return None

    last = df.iloc[-1]
    if pd.isna(last['ma_fast']):
        return None

    if all_bullish and last['close'] > last['ma_fast']:
        direction = "BUY"
    elif all_bearish and last['close'] < last['ma_fast']:
        direction = "SELL"
    else:
        return None

    # حساب ATR لتحديد SL/TP
    atr = last['atr']
    if pd.isna(atr) or atr <= 0:
        atr = last['close'] * 0.0005  # قيمة افتراضية صغيرة للذهب والفوركس

    entry = last['close']
    if direction == "BUY":
        sl = entry - ATR_SL_MULT * atr
        tp = entry + ATR_TP_MULT * atr
    else:
        sl = entry + ATR_SL_MULT * atr
        tp = entry - ATR_TP_MULT * atr

    rsi_value = last['rsi']
    if pd.isna(rsi_value):
        rsi_value = 50.0

    return {
        "pair": pair,
        "direction": direction,
        "entry": round(entry, 5),
        "stop_loss": round(sl, 5),
        "take_profit": round(tp, 5),
        "atr": round(atr, 5),
        "rsi": round(rsi_value, 2),
        "timestamp": datetime.utcnow().isoformat()
    }


def store_signal(signal):
    """تخزين الإشارة في Supabase"""
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
    found_any_signal = False

    for pair in PAIRS:
        try:
            df = fetch_data(pair)
            if df is None:
                continue

            df = compute_indicators(df)
            sig = detect_signal_test(df, pair)

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


if __name__ == "__main__":
    main()
