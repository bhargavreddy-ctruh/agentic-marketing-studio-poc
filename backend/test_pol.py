import asyncio

from src.providers.image.pollinations import get_image_gen_provider


async def test():
    provider = get_image_gen_provider()
    res = await provider.generate(prompt='A vibrant Diwali campaign', aspect_ratio='1:1')
    print('Result:', len(res.image_bytes))
asyncio.run(test())
