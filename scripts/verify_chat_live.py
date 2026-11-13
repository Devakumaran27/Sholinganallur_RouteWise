import sys, asyncio, json, os
sys.path.insert(0, os.path.abspath("."))
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.testclient import TestClient
from app.main import app

queries = [
    'Which bus connects Sholinganallur to T. Nagar directly?',
    'Which routes serve Sholinganallur?',
    'What are the scheduled departures from Sholinganallur?',
    'Which stops have missing coordinates?',
    'Does the dataset provide live bus tracking?',
]

with TestClient(app) as client:
    for idx, q in enumerate(queries, 1):
        print(f"=== QUERY {idx}: {q} ===")
        resp = client.post("/api/v1/chat/ask", json={"question": q, "top_k": 3})
        if resp.status_code != 200:
            print("ERROR:", resp.status_code, resp.text)
            continue
        data = resp.json()
        print("ANSWER:\n", data["answer"])
        print("\nGROUNDING SOURCES:")
        for s in data["grounding_sources"]:
            print(f"  - [{s['chunk_id']}] {s['title']} (sim: {s['similarity']})")
        if data.get("structured_context_used"):
            print("STRUCTURED CONTEXT KEYS:", list(data["structured_context_used"].keys()))
        print("LIMITATIONS:", data.get("limitations", []))
        print("\n" + "="*60 + "\n")
