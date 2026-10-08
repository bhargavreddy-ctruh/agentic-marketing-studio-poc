from google import genai
import inspect
import sys
client = genai.Client(api_key='fake_key')
sig = inspect.signature(client.interactions.create)
print(sig.parameters)
