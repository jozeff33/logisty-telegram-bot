import os
import json
from typing import Any, Dict

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# =========================
# Config
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "Jozef3333")  # اختياري للحماية
BASE_URL = os.getenv("BASE_URL", "https://web-production-ec68.up.railway.app").strip()  # مثل: https://xxxx.up.railway.app

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN env var")

# =========================
# Conversation States
# =========================
(
    SHOP,
    CUSTOMER,
    PHONE,
    DISTRICT,
    ADDRESS,
    AMOUNT,
    NOTES,
) = range(7)

AR = {
    "welcome": "✅ أهلاً بك!\nسأجهّز الشحنة خطوة بخطوة.\n\nابدأ بكتابة *اسم المتجر*:",
    "cancel": "تم الإلغاء ✅\nإذا تريد تبدأ من جديد اكتب /start",
    "ask_customer": "تمام.\nاكتب *اسم الزبون*:",
    "ask_phone": "اكتب *رقم هاتف الزبون* (يفضل يبدأ 07 وطوله 11 رقم):",
    "bad_phone": "⚠️ الرقم غير صحيح.\nاكتب رقم مثل: 07701234567",
    "ask_district": "اكتب *المنطقة/الحي*:",
    "ask_address": "اكتب *العنوان الكامل*:",
    "ask_amount": "اكتب *مبلغ الاستلام (IQD)* رقم فقط مثال: 25000",
    "bad_amount": "⚠️ المبلغ لازم يكون رقم فقط.\nمثال: 25000",
    "ask_notes": "اكتب *ملاحظات* (أو اكتب - إذا ماكو):",
    "done": "✅ تم تجهيز الشحنة (JSON جاهز):",
    "hint": "إذا تريد تبدأ شحنة جديدة: /new\nإذا تريد إلغاء: /cancel",
}

def _clean_text(t: str) -> str:
    return (t or "").strip()

def _is_valid_phone(phone: str) -> bool:
    phone = phone.replace(" ", "")
    return phone.isdigit() and len(phone) == 11 and phone.startswith("07")

def _pretty_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)

def _build_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    # هنا نجهز الPayload النهائي (تقدر تغيّر المفاتيح حسب API لاحقًا)
    return {
        "shopName": data["shop_name"],
        "customerName": data["customer_name"],
        "phone": data["phone"],
        "district": data["district"],
        "address": data["address"],
        "amountIQD": data["amount_iqd"],
        "notes": data.get("notes", ""),
    }

# =========================
# Telegram Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(AR["welcome"], parse_mode=ParseMode.MARKDOWN)
    return SHOP

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(AR["cancel"])
    return ConversationHandler.END

async def new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("✅ ممتاز. اكتب *اسم المتجر*:", parse_mode=ParseMode.MARKDOWN)
    return SHOP

async def shop_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["shop_name"] = _clean_text(update.message.text)
    await update.message.reply_text(AR["ask_customer"], parse_mode=ParseMode.MARKDOWN)
    return CUSTOMER

async def customer_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["customer_name"] = _clean_text(update.message.text)
    await update.message.reply_text(AR["ask_phone"], parse_mode=ParseMode.MARKDOWN)
    return PHONE

async def phone_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = _clean_text(update.message.text)
    if not _is_valid_phone(phone):
        await update.message.reply_text(AR["bad_phone"])
        return PHONE
    context.user_data["phone"] = phone
    await update.message.reply_text(AR["ask_district"], parse_mode=ParseMode.MARKDOWN)
    return DISTRICT

async def district_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["district"] = _clean_text(update.message.text)
    await update.message.reply_text(AR["ask_address"], parse_mode=ParseMode.MARKDOWN)
    return ADDRESS

async def address_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["address"] = _clean_text(update.message.text)
    await update.message.reply_text(AR["ask_amount"], parse_mode=ParseMode.MARKDOWN)
    return AMOUNT

async def amount_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = _clean_text(update.message.text).replace(",", "")
    if not raw.isdigit():
        await update.message.reply_text(AR["bad_amount"])
        return AMOUNT
    context.user_data["amount_iqd"] = int(raw)
    await update.message.reply_text(AR["ask_notes"], parse_mode=ParseMode.MARKDOWN)
    return NOTES

async def notes_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    notes = _clean_text(update.message.text)
    if notes == "-":
        notes = ""
    context.user_data["notes"] = notes

    payload = _build_payload(context.user_data)
    pretty = _pretty_json(payload)

    await update.message.reply_text(
        f"{AR['done']}\n\n```json\n{pretty}\n```\n\n{AR['hint']}",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("اكتب /start للبدء ✅")

def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SHOP: [MessageHandler(filters.TEXT & ~filters.COMMAND, shop_step)],
            CUSTOMER: [MessageHandler(filters.TEXT & ~filters.COMMAND, customer_step)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_step)],
            DISTRICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, district_step)],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_step)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_step)],
            NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("new", new))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    return app

# =========================
# FastAPI (Webhook)
# =========================
tg_app: Application = build_application()
api = FastAPI()

@api.on_event("startup")
async def on_startup():
    await tg_app.initialize()
    await tg_app.start()

    # إذا BASE_URL موجود: نفعّل Webhook
    if BASE_URL:
        webhook_url = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
        await tg_app.bot.set_webhook(webhook_url)
        print("✅ Webhook set:", webhook_url)
    else:
        print("⚠️ BASE_URL not set. Webhook not configured.")

@api.on_event("shutdown")
async def on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()

@api.get("/")
async def root():
    return {"status": "ok", "bot": "running"}

@api.post("/webhook/{secret}")
async def webhook(secret: str, request: Request) -> Response:
    if secret != WEBHOOK_SECRET:
        return Response(status_code=403, content="forbidden")

    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return Response(status_code=200, content="ok")

# Railway expects "app"
app = api