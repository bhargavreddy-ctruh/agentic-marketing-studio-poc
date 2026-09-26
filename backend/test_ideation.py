import asyncio

from src.services.ideation.ideation_service import run_ideation


async def main():
    state = {
        "brief": {
            "idea": "Text description of the image",
            "latest_element_storage_ref": "123",
            "latest_element_type": "image",
            "latest_element_description": "A cool image"
        },
        "user_message": "edit this image actual price 35000 strike out then display the discounted price for this product"
    }
    new_state = await run_ideation(state)
    print("New user message:", new_state.get("user_message"))
    print("New brief:", new_state.get("brief"))

asyncio.run(main())
