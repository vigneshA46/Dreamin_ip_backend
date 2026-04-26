import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"   # ✅ required for Railway
    )


def init_db():
    """
    Create required tables if they don't exist.
    Safe to call multiple times.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS broker_access_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            broker_name TEXT NOT NULL,
            client_id TEXT NOT NULL,

            access_token TEXT NOT NULL,
            expiry_time TIMESTAMP NOT NULL,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),

            UNIQUE (broker_name, client_id)
        );
    """)

    # Optional but recommended index
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_broker_token_lookup
        ON broker_access_tokens (broker_name, client_id);
    """)

    conn.commit()
    cur.close()
    conn.close()

    print("✅ Database initialized (broker_access_tokens)")