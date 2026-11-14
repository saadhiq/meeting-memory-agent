"""Quick check: is the NVIDIA API key working, and how fast is the model?"""
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

key = os.environ.get("NVIDIA_API_KEY")
if not key:
    raise SystemExit("No key found. Check that .env exists in this folder and has NVIDIA_API_KEY=...")
print(f"Key loaded: starts with {key[:6]}, {len(key)} characters")

client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key, timeout=60)

start = time.time()
try:
    reply = client.chat.completions.create(
        model="z-ai/glm-5.3-flash",
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        temperature=0,
        max_tokens=200,
    )
    print("Reply:", reply.choices[0].message.content)
    print(f"Tokens: {reply.usage.prompt_tokens} in, {reply.usage.completion_tokens} out")
    print(f"Time: {time.time() - start:.1f} seconds")
except Exception as error:
    print(f"FAILED after {time.time() - start:.1f} seconds: {type(error).__name__}: {error}")