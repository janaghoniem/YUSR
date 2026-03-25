from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    # Trigger connection
    client.server_info()
    print("✅ MongoDB connection successful!")
    print(f"📊 Databases: {client.list_database_names()}")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")