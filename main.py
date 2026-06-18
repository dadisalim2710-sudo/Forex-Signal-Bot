import os
import time
import requests
import pandas as pd
import numpy as np
import ta
import yfinance as yf
from datetime import datetime, timedelta
from supabase import create_client
import schedule

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
LOOKBACK = 2
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
ATR_PERIOD = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.0

TREND_MA_FAST = 10
TREND_MA_SLOW = 20

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# لتتبع آخر تحديث تم معالجته في تيليجرام
LAST_UPDATE_ID = None


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
        return resp
    except Exception as e:
        print(f"استثناء تيليجرام: {e}")
        return None


def get_telegram_updates():
    """جلب آخر رسائل تيليجرام ومعالجة /stats فقط"""
    global LAST_UPDATE_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    if LAST_UPDATE_ID is not None:
        url += f"?offset={LAST_UPDATE_ID + 1}"
    try:
        resp = requests.get(url)
        if resp.status_code != 200:
            return
        data = resp.json()
        if not data.get("ok"):
            return

        for update in data.get("result", []):
            if "message" not in update:
                continue
            msg_text = update["message"].get("text", "")
            chat_id = update["message"]["chat"]["id"]
            if msg_text.strip().lower() == "/stats":
                handle_stats(chat_id)

            # تحديث آخر معرّف
            LAST_UPDATE_ID = update["update_id"]

    except Exception as e:
        print(f"خطأ في getUpdates: {e}")


def handle_stats(chat_id):
    """توليد رد /stats من Supabase"""
    try:
        since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        res = supabase.table("signals") \
            .select("pair, direction, entry, created_at") \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .execute()

        signals = res.data or []
        total = len(signals)
        if total == 0:
            reply = "ℹ️ لا توجد توصيات خلال آخر 24 ساعة."
        else:
            last = signals[0]
            reply = (
                f"📊 إحصائيات آخر 24 ساعة:\n"
                f"• إجمالي التوصيات: {total}\n"
                f"• أحدث توصية: {last['direction']} {last['pair']}\n"
                f"• لم يتم تتبع الصفقات المغلقة بعد (ميزة قادمة)."
            )
        send_telegram(reply)
    except Exception as e:
        send_telegram(f"خطأ في الإحصائيات: {e}")


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
    df['ma_trend_fast'] = ta.trend.sma_indicator(df['close'], TREND_MA_FAST)
    df['ma_trend_slow'] = ta.trend.sma_indicator(df['close'], TREND_MA_SLOW)
    return df


def detect_signal(df, pair):
    if len(df) < max(LOOKBACK, TREND_MA_FAST, TREND_MA_SLOW) + 2:
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

    ma_fast = last['ma_trend_fast']
    ma_slow = last['ma_trend_slow']
    if pd.isna(ma_fast) or pd.isna(ma_slow):
        return None

    if direction == "BUY" and ma_fast <= ma_slow:
        return None
    if direction == "SELL" and ma_fast >= ma_slow:
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


def job_analyze_markets():
    """المهمة التي تحلل الأسواق كل دقيقتين"""
    print(f"تحليل الأسواق - {datetime.utcnow()}")
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
                if sig['direction'] == "BUY":
                    operation = "🟢 شراء"
                    trend = "صاعد"
                else:
                    operation = "🔴 بيع"
                    trend = "نازل"
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
        print("انتهى التحليل بدون إشارات.")


def main():
    print("✅ بدء تشغيل البوت الدائم على Railway...")
    send_telegram("✅ البوت يعمل الآن 24/7 على Railway 🚀")

    # جدولة التحليل كل دقيقتين
    schedule.every(2).minutes.do(job_analyze_markets)

    # حلقة لا نهائية: معالجة رسائل تيليجرام وتنفيذ المهام المجدولة
    while True:
        try:
            # فحص رسائل تيليجرام (كل 3 ثوانٍ)
            get_telegram_updates()
        except Exception as e:
            print(f"خطأ في حلقة تيليجرام: {e}")

        try:
            # تنفيذ المهام المجدولة (التحليل)
            schedule.run_pending()
        except Exception as e:
            print(f"خطأ في المجدول: {e}")

        time.sleep(3)  # انتظر 3 ثوانٍ ثم كرر


if __name__ == "__main__":
    main()
