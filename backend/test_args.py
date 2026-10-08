import inspect

from google import genai

client = genai.Client(api_key='fake_key')
sig = inspect.signature(client.interactions.create)
print(sig.parameters)
