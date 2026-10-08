import asyncio

from google import genai
from google.genai import types


async def main():
    client = genai.Client(api_key="fake")
    try:
        await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": "hello"}]}],
            config=types.GenerateContentConfig(
                system_instruction="You are a bot",
                tools=[{"function_declarations": [{"name": "test", "description": "test"}]}]
            )
        )
    except Exception as e:
        print("Error:", type(e), e)

asyncio.run(main())
