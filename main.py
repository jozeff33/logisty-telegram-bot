import os
import re
import json
import asyncio
from typing import Dict, List, Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN env var")

# =========================
# In-memory buffers per user/chat
# =========================
BUFFERS: Dict[int, List[str]] = {}
TIMERS: Dict[int, asyncio.Task] = {}

AUTO_PROCESS_SECONDS = int(os.getenv("AUTO_PROCESS_SECONDS", "0"))  # 0 = off


# =========================
# Helpers: parsing
# =========================
PHONE_RE = re.compile(r"(\+964\s?7\d{9}|07\d{9})")
AMOUNT_RE = re.compile(r"(?:مبلغ|المبلغ|amount)\s*[:：]?\s*(\d{3,})", re.IGNORECASE)

def normalize_phone(phone: str) -> str:
    phone = phone.replace(" ", "")
    if phone.startswith("+9647"):
        return "0" + phone[4:]  # +9647XXXXXXXXX -> 07XXXXXXXXX
    return phone

def split_into_orders(text: str) -> List[str]:
    """
    تقسيم النص إلى طلبات.
    القاعدة: كل طلب لازم يحتوي رقم هاتف (07... أو +9647...).
    نقسم حسب ظهور أرقام الهواتف.
    """
    matches = list(PHONE_RE.finditer(text))
    if not matches:
        return [text.strip()] if text.strip() else []

    chunks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
    return chunks

def extract_order_fields(order_text: str) -> Dict:
    """
    استخراج حقول عامة من النص.
    نعتبر أول رقم هاتف هو الأساس.
    """
    phone_match = PHONE_RE.search(order_text)
    phone = normalize_phone(phone_match.group(1)) if phone_match else ""

    amount_match = AMOUNT_RE.search(order_text)
    amount = int(amount_match.group(1)) if amount_match else None

    # اسم الزبون: نحاول التقاطه من "اسم:" أو "الاسم:"
    name = ""
    m = re.search(r"(?:اسم|الاسم)\s*[:：]\s*(.+)", order_text)
    if m:
        name = m.group(1).strip().splitlines()[0]

    # العنوان
    address = ""
    m = re.search(r"(?:عنوان|العنوان)\s*[:：]\s*(.+)", order_text)
    if m:
        address = m.group(1).strip()

    # ملاحظات
    notes = ""
    m = re.search(r"(?:ملاحظات|ملاحظة)\s*[:：]\s*(.+)", order_text)
    if m:
        notes = m.group(1).strip()

    # المحافظة/المنطقة (اختياري)
    city = ""
    m = re.search(r"(?:محافظة|المدينة)\s*[:：]\s*(.+)", order_text)
    if m:
        city = m.group(1).strip().splitlines()[0]

    district = ""
    m = re.search(r"(?:منطقة|المنطقه|قضاء)\s*[:：]\s*(.+)", order_text)
    if m:
        district = m.group(1).strip().splitlines()[0]

    return {
        "customerName": name or "غير محدد",
        "phone": phone or "غير محدد",
        "amountIQD": amount if amount is not None else 0,
        "city": city,
        "district": district,
        "address": address,
        "notes": notes if notes else order_text.strip(),  # نخلي النص كله ملاحظة إذا ماكو حقل واضح
        "raw": order_text.strip(),
    }

def parse_orders(full_text: str) -> List[Dict]:
    orders_text = split_into_orders(full_text)
    orders = [extract_order_fields(x) for x in orders_text if x.strip()]
    # فلترة الطلبات الفارغة
    return [o for o in orders if o.get("raw")]


# =========================
# Bot commands
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ وضع التجميع شغال.\n"
        "ارسل كل رسائل الزبائن (رسالة واحدة أو عدة رسائل).\n\n"
        "لما تخلص اكتب: /done\n"
        "للحذف: /cancel"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    BUFFERS.pop(chat_id, None)
    t = TIMERS.pop(chat_id, None)
    if t:
        t.cancel()
    await update.message.reply_text("🗑️ تم حذف التجميع الحالي. ارسل من جديد ثم /done.")

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = "\n".join(BUFFERS.get(chat_id, [])).strip()
    BUFFERS.pop(chat_id, None)

    t = TIMERS.pop(chat_id, None)
    if t:
        t.cancel()

    if not text:
        await update.message.reply_text("ما استلمت نص بعد. ارسل رسائل ثم /done.")
        return

    orders = parse_orders(text)
    pretty = json.dumps(orders, ensure_ascii=False, indent=2)

    await update.message.reply_text(
        f"✅ تم تحليل الرسائل.\n"
        f"عدد الطلبات المستخرجة: {len(orders)}\n\n"
        f"```json\n{pretty}\n```",
        parse_mode="Markdown"
    )

    # هنا لاحقًا: نربط إنشاء الشحنات عبر API
    # for order in orders:
    #     result = await create_shipment_via_api(order)
    # ثم نرجع رقم الشحنة/الباركود للتاجر


async def _auto_finalize(chat_id: int, app: Application):
    await asyncio.sleep(AUTO_PROCESS_SECONDS)
    text = "\n".join(BUFFERS.get(chat_id, [])).strip()
    if not text:
        return

    orders = parse_orders(text)
    pretty = json.dumps(orders, ensure_ascii=False, indent=2)

    BUFFERS.pop(chat_id, None)
    TIMERS.pop(chat_id, None)

    await app.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏱️ تم المعالجة تلقائيًا بسبب عدم وجود رسائل جديدة.\n"
            f"عدد الطلبات: {len(orders)}\n\n"
            f"```json\n{pretty}\n```"
        ),
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = (update.message.text or "").strip()
    if not msg:
        return

    BUFFERS.setdefault(chat_id, []).append(msg)

    # خيار المعالجة التلقائية بعد فترة سكون
    if AUTO_PROCESS_SECONDS > 0:
        old = TIMERS.get(chat_id)
        if old:
            old.cancel()
        TIMERS[chat_id] = asyncio.create_task(_auto_finalize(chat_id, context.application))

    await update.message.reply_text("📥 تم استلام الرسالة. أكمل إرسال البقية ثم /done")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()


if __name__ == "__main__":
    main()