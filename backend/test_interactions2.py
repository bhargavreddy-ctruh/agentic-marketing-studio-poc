import asyncio

from google import genai


async def main():
    client = genai.Client(api_key='fake_key')
    try:
        response = await client.aio.interactions.create(
            model="gemini-1.5-flash",
            messages=[{"role": "user", "content": "What is the weather?"}],
        )
        print("Success:", response)
    except Exception as e:
        print("Error:", type(e), e)

asyncio.run(main())
