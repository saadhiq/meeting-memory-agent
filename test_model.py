"""Check that a model on the NVIDIA API replies, and how fast it is.
Usage:  python test_model.py nvidia/nemotron-3.5-bolt-e4b"""
import os
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
key = os.environ.get("NVIDIA_API_KEY")
if not key:
    raise SystemExit("No key found. Check .env has NVIDIA_API_KEY=...")

model = sys.argv[1] if len(sys.argv) > 1 else "nvidia/nemotron-3.5-bolt-e4b"
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key, timeout=90)

print(f"Testing {model} ...")
start = time.time()
first_token = None
answer = ""
try:
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Which number is larger, 9.11 or 9.8? Answer in one sentence."}],
        temperature=0,
        max_tokens=500,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        piece = chunk.choices[0].delta.content or ""
        if piece and first_token is None:
            first_token = time.time() - start
        answer += piece
        print(piece, end="", flush=True)
    print(f"\n\nFirst word after {first_token:.1f}s, finished in {time.time() - start:.1f}s"
          if first_token else f"\n\nNo text came back after {time.time() - start:.1f}s")
except Exception as error:
    print(f"\nFAILED after {time.time() - start:.1f}s: {type(error).__name__}: {error}")