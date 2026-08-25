import asyncio

import aiohttp
from bs4 import BeautifulSoup


URL = "https://qazsporttv.kz/ru/program"


async def check_qazsport():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout
    ) as session:

        async with session.get(URL) as response:
            html = await response.text()

            soup = BeautifulSoup(
                html,
                "html.parser"
            )

            page_title = (
                soup.title.get_text(strip=True)
                if soup.title
                else "Заголовок не найден"
            )

            print("Qazsport отвечает ✅")
            print("HTTP статус:", response.status)
            print("Заголовок страницы:", page_title)
            print("Получено символов HTML:", len(html))


async def main():
    await check_qazsport()


if __name__ == "__main__":
    asyncio.run(main())