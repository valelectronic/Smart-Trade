import os
import psycopg2
from dotenv import load_dotenv

# Load your local .env file
load_dotenv()

# Get the connection string
connection_string = os.getenv('DATABASE_URL')
print(f"Attempting to connect to: {connection_string[:30]}...")

try:
    # Try to connect
    conn = psycopg2.connect(connection_string)
    cur = conn.cursor()
    
    # Run a simple query
    cur.execute('SELECT NOW();')
    time = cur.fetchone()[0]
    print(f"✅ SUCCESS! Connected to Neon. Current time: {time}")
    
    cur.close()
    conn.close()
except Exception as e:
    print(f"❌ FAILED to connect: {e}")