import asyncio
from aiogram import Bot, Dispatcher

from app.config import get_settings
from app.services.ai_provider import AIProvider
from app.handlers import mental_profile
from app.handlers import pro_menu
from app.handlers import start


async def main():
    s = get_settings()

    bot = Bot(token=s.bot_token)
    dp = Dispatcher()

    # создаём AIProvider (общий)
    mental_profile.ai = AIProvider(
        api_key=s.proxyapi_key,
        base_url=s.proxyapi_base_url,
        model=s.gpt_model,
    )

    # подключаем роутеры
    dp.include_router(start.router)
    dp.include_router(pro_menu.router)
    dp.include_router(mental_profile.router)

    # сбрасываем старые апдейты и отключаем webhook (если он был)
    await bot.delete_webhook(drop_pending_updates=True)

    print("🤖 Bot started and polling Telegram...")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
