# bot/telegram_api.py
from aiogram import Bot
import os
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
