import asyncio
from google import genai

async def main():
    client = genai.Client(api_key='fake_key')
    try:
        response = await client.aio.interactions.create(
            model="gemini-1.5-flash",
            input="What is the weather?",
            tools=[{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            }]
        )
        print("Success:", response)
    except Exception as e:
        print("Error:", type(e), e)

asyncio.run(main())
