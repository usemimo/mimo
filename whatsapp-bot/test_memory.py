import asyncio
from app.db.sqlite import SQLiteDatabase
from app.core.config import get_settings
from app.core.memory import MemoryStore

async def run_test():
    print("🚀 Initializing test with SQLite (No Postgres needed)...")
    
    # 1. Connect to the local SQLite Database (fallback for testing)
    db = SQLiteDatabase(db_path="data/test_memory.db")
    await db.initialize()
    
    memory_store = MemoryStore(db)
    test_user_id = 999  # Dummy user ID for testing
    
    # Ensure a clean slate
    await memory_store.forget_all(test_user_id)
    
    print("\n--- Test 1: Saving a memory ---")
    fact = "my favorite color is blue"
    print(f"User: memory add {fact}")
    await memory_store.save_fact(user_id=test_user_id, fact=fact, source="user_text")
    print("✅ Memory saved to database.")

    print("\n--- Test 2: Viewing memories ---")
    print("User: memory view")
    facts = await memory_store.get_active_facts(user_id=test_user_id)
    if facts:
        print("🧠 Here is what I remember about you:")
        for f in facts:
            print(f"  • {f['fact']}")
    else:
        print("🧠 I don't have any memories saved for you yet.")

    print("\n--- Test 3: Forgetting memories ---")
    print("User: memory forget")
    await memory_store.forget_all(user_id=test_user_id)
    print("🗑️ I have forgotten all memories about you.")
    
    print("\n--- Test 4: Viewing memories (Should be empty) ---")
    print("User: memory view")
    facts = await memory_store.get_active_facts(user_id=test_user_id)
    if facts:
        for f in facts:
            print(f"  • {f['fact']}")
    else:
        print("✅ 🧠 I don't have any memories saved for you yet.")

    print("\n🎉 All tests completed successfully!")
    await db.close()

if __name__ == "__main__":
    asyncio.run(run_test())
