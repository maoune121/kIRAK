import discord
from discord.ext import commands, tasks
from tradingview_ta import TA_Handler, Interval
import asyncio
import csv
import os
import logging

# إعداد سجل التصحيح
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('discord')

# تفعيل الـ Intents مع صلاحية قراءة محتوى الرسائل
intents = discord.Intents.default()
intents.message_content = True

# قراءة التوكن ومعرف القناة من متغيرات البيئة
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# إنشاء البوت مع تعيين بادئة الأوامر
bot = commands.Bot(command_prefix="/", intents=intents)

CSV_FILE = "alerts.csv"  # ملف CSV لتخزين التنبيهات
alerts = []  # قائمة التنبيهات

# دوال لتحميل وحفظ التنبيهات في ملف CSV
def load_alerts():
    global alerts
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            alerts = [row for row in reader]
            # تحويل قيمة target_price إلى float
            for alert in alerts:
                alert["target_price"] = float(alert["target_price"])
    else:
        alerts = []

def save_alerts():
    global alerts
    with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
        fieldnames = ['symbol', 'target_price', 'channel_id']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for alert in alerts:
            writer.writerow(alert)

# تحميل التنبيهات عند بدء التشغيل
load_alerts()

# أمر /alert لإضافة تنبيه جديد
@bot.command()
async def alert(ctx, symbol: str, target_price: float):
    # التأكد من عدم وجود التنبيه مسبقًا لنفس العملة والسعر
    for a in alerts:
        if a["symbol"] == symbol.upper() and a["target_price"] == target_price:
            await ctx.send(f"تنبيه لـ **{symbol.upper()}** عند **{target_price}** موجود بالفعل.")
            return
    alert_data = {
        "symbol": symbol.upper(),
        "target_price": target_price,
        "channel_id": str(ctx.channel.id)  # تخزين المعرف كسلسلة لتسهيل التعامل مع CSV
    }
    alerts.append(alert_data)
    save_alerts()
    await ctx.send(f"تم ضبط تنبيه لـ **{alert_data['symbol']}** عند السعر **{alert_data['target_price']}** في هذه القناة.")
    logger.debug(f"تنبيه جديد مضاف: {alert_data}")

# مهمة فحص الأسعار: يتم تشغيلها مرة واحدة ثم يتم الانتقال لسؤال المستخدم
@tasks.loop(count=1)
async def check_prices():
    logger.debug("بدء فحص الأسعار...")
    alerts_to_remove = []
    for alert_data in alerts:
        symbol = alert_data["symbol"]
        target_price = alert_data["target_price"]
        channel_id = int(alert_data["channel_id"])
        try:
            logger.debug(f"فحص {symbol} مع السعر المستهدف {target_price}")
            handler = TA_Handler(
                symbol=symbol,
                screener="forex",       # بيانات الفوركس لجميع أزواج العملات
                exchange="OANDA",       # استخدام منصة OANDA
                interval=Interval.INTERVAL_15_MINUTE  # الشمعة الزمنية لمدة 15 دقيقة
            )
            analysis = handler.get_analysis().indicators
            high_price = float(analysis.get('high', 0))
            low_price = float(analysis.get('low', 0))
            logger.debug(f"الشمعة لـ {symbol}: Low = {low_price}, High = {high_price}")
            # التحقق إذا كان السعر الهدف ضمن نطاق الشمعة
            if low_price <= target_price <= high_price:
                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(
                        f"🚨 تنبيه: **{symbol}** لمس السعر المطلوب **{target_price}** خلال آخر 15 دقيقة!"
                    )
                    logger.debug(f"تنبيه مرسل لـ {symbol} في القناة {channel_id}")
                alerts_to_remove.append(alert_data)
            else:
                logger.debug(f"{symbol} لم يلمس السعر المطلوب {target_price} (النطاق: {low_price}-{high_price})")
        except Exception as e:
            logger.error(f"خطأ في جلب البيانات لـ {symbol}: {e}")
    # حذف التنبيهات التي تم إطلاقها من ملف CSV
    if alerts_to_remove:
        for alert_data in alerts_to_remove:
            alerts.remove(alert_data)
        save_alerts()

@bot.event
async def on_ready():
    logger.info(f"تم تسجيل الدخول كـ {bot.user}")
    # بدء فحص الأسعار عند تسجيل الدخول
    await check_prices.start()
    
    # بعد الفحص، إرسال رسالة سؤال في القناة المحددة
    control_channel = bot.get_channel(CHANNEL_ID)
    if control_channel:
        await control_channel.send("هل تغلق البوت؟ (أرسل **1** للإغلاق أو **2** للاستمرار)")
        try:
            # انتظار رد المستخدم لمدة 60 ثانية
            def check(m):
                return m.channel.id == CHANNEL_ID and m.content in ["1", "2"]
            msg = await bot.wait_for('message', timeout=60.0, check=check)
            if msg.content == "1":
                await control_channel.send("سيتم إغلاق البوت...")
                await bot.close()
            elif msg.content == "2":
                await control_channel.send("البوت سيستمر في العمل. لإغلاقه لاحقًا، أرسل **1** في هذه القناة.")
        except asyncio.TimeoutError:
            await control_channel.send("انقضت المهلة، سيتم إغلاق البوت تلقائيًا.")
            await bot.close()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    # التحقق من رسائل القناة المحددة للتحكم
    if message.channel.id == CHANNEL_ID and message.content == "1":
        await message.channel.send("سيتم إغلاق البوت...")
        await bot.close()
        return
    await bot.process_commands(message)

bot.run(TOKEN)
