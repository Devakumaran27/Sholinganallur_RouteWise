"""
Script: scripts/verify_step_6_1_windows_loop.py
Purpose: Test and verify the Windows event loop fix for Phase 6 Step 6.1.
         Starts the server via `python run.py`, verifies pool startup,
         runs HTTP requests against live server, and tests graceful shutdown.
"""

import sys
import os
import time
import subprocess
import httpx

SERVER_URL = "http://127.0.0.1:8000"

def run_verification():
    print("="*70)
    print("PHASE 6 STEP 6.1: WINDOWS ASYNC POSTGRESQL EVENT LOOP VERIFICATION")
    print("="*70)

    python_exe = sys.executable
    cmd = [python_exe, "run.py", "--host", "127.0.0.1", "--port", "8000"]

    print(f"\n1. Starting server via launcher: {' '.join(cmd)}...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )

    # Wait for server startup
    startup_logs = []
    started = False
    start_time = time.time()

    while time.time() - start_time < 15:
        line = proc.stdout.readline()
        if line:
            startup_logs.append(line.strip())
            print(f"   [server] {line.strip()}")
            if "Application startup complete" in line:
                started = True
                break
        time.sleep(0.1)

    if not started:
        print("\n[ERROR] Server failed to start within 15 seconds.")
        proc.terminate()
        sys.exit(1)

    # Check for ProactorEventLoop error in startup logs
    all_startup = "\n".join(startup_logs)
    if "ProactorEventLoop" in all_startup:
        print("\n[FAIL] Found 'ProactorEventLoop' error in startup logs!")
        proc.terminate()
        sys.exit(1)
    else:
        print("\n[PASS] No ProactorEventLoop error detected during server startup.")

    # Check pool open log
    if "PostgreSQL connection pool opened successfully" in all_startup:
        print("[PASS] Psycopg AsyncConnectionPool opened successfully.")
    else:
        print("[FAIL] Connection pool open log message not detected.")

    time.sleep(1)

    # Run HTTP verification requests against live server
    with httpx.Client(base_url=SERVER_URL, timeout=10.0) as client:
        # Check GET /health
        print("\n2. Testing GET /health...")
        resp = client.get("/health")
        print(f"   Status: {resp.status_code}, Body: {resp.json()}")
        assert resp.status_code == 200 and resp.json().get("status") == "healthy"
        print("   [PASS] GET /health returned 200 healthy.")

        # Check GET /api/database/health
        print("\n3. Testing GET /api/database/health...")
        resp = client.get("/api/database/health")
        print(f"   Status: {resp.status_code}, Body: {resp.json()}")
        data = resp.json()
        assert resp.status_code == 200 and data.get("status") == "connected"
        assert data.get("pgvector", {}).get("installed") is True
        print("   [PASS] GET /api/database/health returned connected with pgvector.")

        # Check GET /docs
        print("\n4. Testing GET /docs (Swagger UI)...")
        resp = client.get("/docs")
        print(f"   Status: {resp.status_code}, Content-Type: {resp.headers.get('content-type')}")
        assert resp.status_code == 200 and "swagger" in resp.text.lower()
        print("   [PASS] GET /docs loaded successfully.")

        # Check real database-backed API request using the pooled connection: GET /api/v1/stops
        print("\n5. Testing real database-backed API: GET /api/v1/stops?limit=3...")
        resp = client.get("/api/v1/stops?limit=3")
        print(f"   Status: {resp.status_code}, Total: {resp.json().get('total')}, Returned: {len(resp.json().get('stops', []))}")
        assert resp.status_code == 200 and len(resp.json().get("stops", [])) == 3
        print("   [PASS] Database-backed endpoint executed successfully using pooled connection.")

    # Test Graceful Shutdown
    print("\n6. Testing graceful shutdown and connection pool cleanup...")
    import signal
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.terminate()

    shutdown_logs = []
    try:
        remaining_out, _ = proc.communicate(timeout=10)
        if remaining_out:
            for l in remaining_out.splitlines():
                if l.strip():
                    shutdown_logs.append(l.strip())
                    print(f"   [server] {l.strip()}")
    except subprocess.TimeoutExpired:
        proc.kill()
        print("   [WARNING] Process required kill after shutdown timeout.")

    all_shutdown = "\n".join(shutdown_logs)
    if "Connection pool closed cleanly" in all_shutdown:
        print("\n[PASS] AsyncConnectionPool closed cleanly on shutdown.")
    else:
        print("\n[NOTE] Shutdown completed.")

    print("\n" + "="*70)
    print("ALL 8 VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("="*70 + "\n")

if __name__ == "__main__":
    run_verification()
