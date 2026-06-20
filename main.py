import os, time, requests, pandas as pd, numpy as np, ta, yfinance as yf
from datetime import datetime, timedelta
from supabase import create_client
import schedule, joblib

# ========== الإعدادات ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

PAIRS = [
    "GC=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X"
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
MIN_CONFIDENCE = 65

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
LAST_UPDATE_ID = None

# النموذج (إن وُجد)
model = None
try:
    if os.path.exists("model.pkl"):
        model = joblib.load("model.pkl")
        print("✅ تم تحميل نموذج AI")
except Exception as e:
    print(f"خطأ في تحميل النموذج: {e}")


# ========== دوال تيليجرام ==========
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        return requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"تيليجرام خطأ: {e}")
        return None


def get_telegram_updates():
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
            msg_text = update["message"].get("text", "").strip().lower()
            chat_id = update["message"]["chat"]["id"]
            if msg_text == "/stats":
                handle_stats(chat_id)
            elif msg_text == "/performance":
                handle_performance(chat_id)
            elif msg_text in ["/win", "/loss", "/be"]:
                close_last_signal(chat_id, msg_text[1:])  # يمرر win, loss, be
            LAST_UPDATE_ID = update["update_id"]
    except Exception as e:
        print(f"خطأ في getUpdates: {e}")


def handle_stats(chat_id):
    try:
        since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        res = supabase.table("signals") \
            .select("id, pair, direction, entry, created_at") \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .execute()
        signals = res.data or []
        total = len(signals)
        if total == 0:
            reply = "ℹ️ لا توجد توصيات خلال آخر 24 ساعة."
        else:
            last = signals[0]
            reply = (f"📊 إحصائيات آخر 24 ساعة:\n"
                     f"• إجمالي التوصيات: {total}\n"
                     f"• أحدث توصية: {last['direction']} {last['pair']}\n"
                     f"• نظام AI هجين مفعّل (الثقة ≥ {MIN_CONFIDENCE})")
        send_telegram(reply)
    except Exception as e:
        send_telegram(f"خطأ في الإحصائيات: {e}")


def handle_performance(chat_id):
    """حساب ملخص الأداء الكلي من الصفقات المغلقة"""
    try:
        res = supabase.table("signals") \
            .select("direction, status, result_pips") \
            .neq("status", "open") \
            .execute()
        trades = res.data or []
        total = len(trades)
        if total == 0:
            reply = "ℹ️ لا توجد صفقات مغلقة بعد."
        else:
            wins = sum(1 for t in trades if t['status'] == 'win')
            losses = sum(1 for t in trades if t['status'] == 'loss')
            be = sum(1 for t in trades if t['status'] == 'be')
            winrate = (wins / total * 100) if total > 0 else 0
            # جمع النقاط (لصفقات الفوركس)
            pips_list = [t['result_pips'] for t in trades if t['result_pips'] is not None]
            avg_pips = np.mean(pips_list) if pips_list else 0.0
            reply = (f"📈 أداء البوت الكلي:\n"
                     f"• الصفقات المغلقة: {total}\n"
                     f"• رابحة: {wins} | خاسرة: {losses} | تعادل: {be}\n"
                     f"• نسبة النجاح: {winrate:.1f}%\n"
                     f"• متوسط النقاط: {avg_pips:+.1f}")
        send_telegram(reply)
    except Exception as e:
        send_telegram(f"خطأ في الأداء: {e}")


def close_last_signal(chat_id, result):
    """إغلاق أحدث توصية مفتوحة بالنتيجة المعطاة (win/loss/be)"""
    try:
        # جلب أحدث إشارة مفتوحة
        res = supabase.table("signals") \
            .select("id, pair, direction, entry, tp, sl") \
            .eq("status", "open") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if not res.data:
            send_telegram("⚠️ لا توجد توصية مفتوحة لتغلقها.")
            return
        sig = res.data[0]
        # حساب النقاط بناءً على النتيجة والهدف/الوقف
        entry = float(sig['entry'])
        if result == "win":
            pips = abs(entry - float(sig['tp'])) * 10000  # تقريب
        elif result == "loss":
            pips = -abs(entry - float(sig['sl'])) * 10000
        else:  # be
            pips = 0.0

        # تحديث الحالة والنتيجة
        supabase.table("signals") \
            .update({"status": result, "result_pips": round(pips, 1)}) \
            .eq("id", sig["id"]) \
            .execute()
        send_telegram(f"✅ تم إغلاق {sig['pair']} {sig['direction']} ({result}) - {pips:+.1f} نقطة")
    except Exception as e:
        send_telegram(f"فشل إغلاق الصفقة: {e}")


# ========== دوال السوق ==========
def fetch_data(pair):
    try:
        df = yf.download(pair, interval=TIMEFRAME, period=PERIOD, progress=False)
        if not df.empty and len(df) >= 50:
            if len(df.columns) == 5:
                df.columns = ['open', 'high', 'low', 'close', 'volume']
            return df
    except Exception as e:
        print(f"[{pair}] فشل Yahoo: {e}")
    return None


def compute_features(df):
    df['rsi'] = ta.momentum.rsi(df['close'], RSI_PERIOD)
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], ATR_PERIOD)
    df['highest'] = df['high'].shift(1).rolling(LOOKBACK).max()
    df['lowest'] = df['low'].shift(1).rolling(LOOKBACK).min()
    df['ma_trend_fast'] = ta.trend.sma_indicator(df['close'], TREND_MA_FAST)
    df['ma_trend_slow'] = ta.trend.sma_indicator(df['close'], TREND_MA_SLOW)
    macd = ta.trend.MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = df['macd'] - df['macd_signal']
    bb = ta.volatility.BollingerBands(df['close'], 20, 2)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['close']
    adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], 14)
    df['adx'] = adx.adx()
    if 'volume' in df.columns:
        df['volume_sma'] = ta.trend.sma_indicator(df['volume'], 10)
    else:
        df['volume_sma'] = 0
    return df


def calculate_confidence(row, direction):
    score = 50
    if direction == "BUY" and row['macd_diff'] > 0:
        score += 10
    elif direction == "SELL" and row['macd_diff'] < 0:
        score += 10
    else:
        score -= 15
    if row['adx'] > 25: score += 15
    elif row['adx'] > 20: score += 8
    else: score -= 10
    if direction == "BUY" and row['close'] < row['bb_lower'] * 1.01:
        score += 10
    elif direction == "SELL" and row['close'] > row['bb_upper'] * 0.99:
        score += 10
    if row['volume_sma'] > 0 and row['volume'] > row['volume_sma'] * 1.2:
        score += 5
    if direction == "BUY" and row['rsi'] < 40: score += 5
    elif direction == "SELL" and row['rsi'] > 60: score += 5
    if model:
        try:
            features = [row['rsi'], row['atr'], row['macd_diff'], row['adx'],
                        row['bb_width'], row['volume'], row['close'],
                        row['ma_trend_fast'] - row['ma_trend_slow']]
            proba = model.predict_proba([features])[0][1]
            if direction == "SELL": proba = 1 - proba
            score += int((proba - 0.5) * 40)
        except Exception: pass
    return max(0, min(100, score))


def detect_signal_advanced(df, pair):
    if len(df) < 35:
        return None, 0
    last = df.iloc[-1]
    rsi = last['rsi']
    if pd.isna(rsi): return None, 0
    highest = last['highest']
    lowest = last['lowest']
    if pd.isna(highest) or pd.isna(lowest): return None, 0
    close = last['close']
    direction = None
    if close > highest and rsi < RSI_OVERBOUGHT:
        direction = "BUY"
    elif close < lowest and rsi > RSI_OVERSOLD:
        direction = "SELL"
    if direction is None: return None, 0
    ma_fast = last['ma_trend_fast']
    ma_slow = last['ma_trend_slow']
    if pd.isna(ma_fast) or pd.isna(ma_slow): return None, 0
    if direction == "BUY" and ma_fast <= ma_slow: return None, 0
    if direction == "SELL" and ma_fast >= ma_slow: return None, 0
    conf = calculate_confidence(last, direction)
    if conf < MIN_CONFIDENCE:
        return None, conf
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
    signal = {
        "pair": pair, "direction": direction,
        "entry": round(entry, 5), "stop_loss": round(sl, 5),
        "take_profit": round(tp, 5), "atr": round(atr, 5),
        "rsi": round(rsi, 2), "confidence": round(conf, 1),
        "timestamp": datetime.utcnow().isoformat()
    }
    return signal, conf


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
            "confidence": signal["confidence"],
            "created_at": signal["timestamp"],
            "status": "open",
            "result_pips": None
        }).execute()
    except Exception as e:
        print("فشل تخزين الإشارة:", e)


def job_analyze_markets():
    print(f"تحليل الأسواق - {datetime.utcnow()}")
    found = False
    for pair in PAIRS:
        try:
            df = fetch_data(pair)
            if df is None: continue
            df = compute_features(df)
            sig, conf = detect_signal_advanced(df, pair)
            if sig:
                found = True
                op = "🟢 شراء" if sig['direction'] == "BUY" else "🔴 بيع"
                trend = "صاعد" if sig['direction'] == "BUY" else "نازل"
                msg = (f"{op} | {pair}\n"
                       f"الاتجاه: {trend}\n"
                       f"نقطة الدخول: {sig['entry']}\n"
                       f"وقف الخسارة: {sig['stop_loss']}\n"
                       f"الهدف: {sig['take_profit']}\n"
                       f"الثقة: {sig['confidence']}% {'🤖 AI' if model else '📊 خبير'}")
                print(msg)
                send_telegram(msg)
                store_signal(sig)
            else:
                print(f"[{pair}] لا توجد إشارة حالياً")
        except Exception as e:
            print(f"خطأ في زوج {pair}: {e}")
    if not found:
        print("انتهى التحليل بدون إشارات.")


def main():
    print("✅ بدء البوت الاحترافي (تتبع الصفقات)...")
    send_telegram("🚀 البوت الخارق قيد التشغيل 24/7")

    schedule.every(2).minutes.do(job_analyze_markets)

    while True:
        try:
            get_telegram_updates()
        except Exception as e:
            print(f"خطأ حلقة تيليجرام: {e}")
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"خطأ المجدول: {e}")
        time.sleep(3)


if __name__ == "__main__":
    main()
