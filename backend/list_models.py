import asyncio

from google import genai

from src.core.config import settings


async def main():
    api_key = settings.gemini_api_key
    if not api_key:
        print("No GEMINI_API_KEY found in settings!")
        return
    client = genai.Client(api_key=api_key)
    try:
        response = await client.aio.models.list()
        print("Available Models:")
        async for model in response:
            print(f"- {model.name}")
    except Exception as e:
        print("Error listing models:", e)

asyncio.run(main())
