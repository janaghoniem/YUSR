# test_mistral_rest.py
import os
import requests
from dotenv import load_dotenv
load_dotenv()

api_key = os.environ.get("MISTRAL_API_KEY")
if not api_key:
    print("❌ MISTRAL_API_KEY not found!")
    exit(1)

print(f"✅ API Key found: {api_key[:10]}...")

# REST API endpoint
url = "https://api.mistral.ai/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

payload = {
    "model": "codestral-2508",
    "messages": [
        {"role": "user", "content": "Write a simple Python hello world function"}
    ],
    "temperature": 0.2,
    "max_tokens": 200
}

try:
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    
    result = response.json()
    print("\n✅ Mistral API test successful!")
    print(f"Response: {result['choices'][0]['message']['content'][:200]}...")
    
except requests.exceptions.RequestException as e:
    print(f"\n❌ Error: {e}")
    if hasattr(e, 'response') and e.response:
        print(f"Status: {e.response.status_code}")
        print(f"Response: {e.response.text}")