import os
import re
import json
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "7091454389:AAGbj_ecGj4X_-uu7TqCu2O4tM4IhqT3xDQ")

ARABIC_KEYS_ALIASES = {
    "تاجر": "merchantLoginId",
    "المتجر": "shopName",
    "متجر": "shopName",
    "اسم": "customerName",
    "هاتف": "phone",
    "رقم": "phone",
    "مبلغ": "amountIQD",
    "محافظة": "stateName",
    "المنطقة": "districtName",
    "منطقة": "districtName",
    "عنوان": "address",
    "تفاصيل العنوان": "address",
    "ملاحظات": "notes",
    "ملاحظة": "notes",
}

REQUIRED_FIELDS = ["customerName", "phone", "amountIQD", "stateName", "districtName", "address"]


def normalize_phone(p: str) -> str:
    return re.sub(r"\D+", "", p or "")


def normalize_amount(a: str) -> int:
    a = (a or "").strip()
    digits = re.sub(r"\D+", "", a)
    if digits:
        return int(digits)

    a = a.replace("ألف", "الف").strip()
    m = re.match(r"(\d+)\s*الف", a)
    if m:
        return int(m.group(1)) * 1000

    raise ValueError("مبلغ غير صحيح")


def gen_client_ref() -> str:
    return str(int(time.time() * 1000))


def parse_kv_lines(block_text: str) -> dict:
    data = {}
    for raw in (block_text or "").splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not v:
            continue
        key = ARABIC_KEYS_ALIASES.get(k, k)
        data[key] = v
    return data


def build_payload(data: dict) -> dict:
    phone = normalize_phone(data.get("phone", ""))
    amount = normalize_amount(data.get("amountIQD", ""))

    payload = {
        "merchantLoginId": data.get("merchantLoginId", ""),
        "shopName": data.get("shopName", ""),
        "customerName": data.get("customerName", "").strip(),
        "phone": phone,
        "amountIQD": amount,
        "stateName": data.get("stateName", "").strip(),
        "districtName": data.get("districtName", "").strip(),
        "address": data.get("address", "").strip(),
        "notes": data.get("notes", "").strip(),
        "clientRef": gen_client_ref(),
    }
    return payload


def validate_payload(payload: dict) -> list[str]:
    missing = []
    for k in REQUIRED_FIELDS:
        if not payload.get(k):
            missing.append(k)
    phone = payload.get("phone", "")
    if phone and len(phone) != 11:
        missing.append("phone (must be 11 digits)")
    return missing


def split_shipments(text: str) -> list[str]:
    parts = [p.strip() for p in (text or "").split("---")]
    return [p for p in parts if p]


def apply_global_fields(global_data: dict, data: dict) -> dict:
    for key in ["merchantLoginId", "shopName", "stateName"]:
        if not data.get(key) and global_data.get(key):
            data[key] = global_data[key]
    return data


def short_preview(ship: dict) -> str:
    return (
        f"- {ship.get('customerName','?')} | {ship.get('phone','?')} | "
        f"{ship.get('amountIQD','?')} د.ع | {ship.get('districtName','?')}"
    )


# ---------- Telegram Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ البوت يعمل\n\n"
        "📌 Bulk Format:\n"
        "اكتب عدة شحنات برسالة واحدة وافصل بينهم بـ ---\n\n"
        "بعدها سيظهر زر ✅ تأكيد الإرسال / ❌ إلغاء"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    blocks = split_shipments(text)

    if not blocks:
        await update.message.reply_text("❌ لا توجد بيانات.")
        return

    global_data = parse_kv_lines(blocks[0])

    results = []
    errors = []

    for idx, block in enumerate(blocks, start=1):
        data = parse_kv_lines(block)
        data = apply_global_fields(global_data, data)

        try:
            payload = build_payload(data)
        except ValueError as e:
            errors.append(f"شحنة #{idx}: {e}")
            continue

        missing = validate_payload(payload)
        if missing:
            errors.append(f"شحنة #{idx}: ناقص/خطأ -> {', '.join(missing)}")
            continue

        results.append(payload)

    # خزّن النتائج مؤقتًا للمستخدم (حتى نستخدمها وقت التأكيد)
    context.user_data["pending_shipments"] = results
    context.user_data["pending_errors"] = errors
    context.user_data["pending_ts"] = time.time()

    # عرض مختصر للمعاينة
    preview_lines = [short_preview(s) for s in results[:15]]
    preview = "\n".join(preview_lines) if preview_lines else "(لا يوجد)"

    summary = (
        f"🧾 معاينة قبل الإرسال:\n"
        f"✅ جاهزة: {len(results)}\n"
        f"❌ أخطاء: {len(errors)}\n\n"
        f"{preview}\n"
    )

    if errors:
        summary += "\nتفاصيل الأخطاء (أول 8):\n" + "\n".join(errors[:8]) + "\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="CONFIRM_SEND")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="CANCEL_SEND")],
    ])

    await update.message.reply_text(summary, reply_markup=keyboard)


async def on_confirm_or_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data
    pending = context.user_data.get("pending_shipments", [])
    errors = context.user_data.get("pending_errors", [])

    if action == "CANCEL_SEND":
        context.user_data.pop("pending_shipments", None)
        context.user_data.pop("pending_errors", None)
        await query.edit_message_text("❌ تم الإلغاء. أرسل البيانات من جديد إذا تريد.")
        return

    # CONFIRM_SEND
    if not pending:
        await query.edit_message_text("⚠️ لا توجد شحنات معلقة للتأكيد. أرسل البيانات من جديد.")
        return

    # حاليا: تأكيد فقط + طباعة JSON (لاحقًا هنا نعمل API call)
    pretty_json = json.dumps(pending, ensure_ascii=False, indent=2)

    msg = (
        f"✅ تم التأكيد\n"
        f"📦 عدد الشحنات: {len(pending)}\n"
        f"❌ أخطاء (لم تُرسل): {len(errors)}\n\n"
        f"```json\n{pretty_json}\n```"
    )

    # نلغي التعليق حتى لا يتكرر التأكيد لنفس الدفعة
    context.user_data.pop("pending_shipments", None)
    context.user_data.pop("pending_errors", None)

    await query.edit_message_text(msg, parse_mode="Markdown")


from fastapi import FastAPI, Request
import uvicorn
from telegram import Update

app = FastAPI()
tg_app = None  # telegram Application

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # سنضعه في Railway

@app.on_event("startup")
async def startup():
    global tg_app
    tg_app = Application.builder().token(BOT_TOKEN).build()

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    tg_app.add_handler(CallbackQueryHandler(on_confirm_or_cancel))

    await tg_app.initialize()
    await tg_app.start()

    if WEBHOOK_URL:
        await tg_app.bot.set_webhook(url=WEBHOOK_URL)

@app.on_event("shutdown")
async def shutdown():
    if tg_app:
        await tg_app.stop()
        await tg_app.shutdown()

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(req: Request):
    update_json = await req.json()
    update = Update.de_json(update_json, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

if __name__ == "__main__":
    # للتشغيل المحلي (اختياري)
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))