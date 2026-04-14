import time
import requests

API_BASE = "http://localhost:8081"
WORKER_ID = "dev-worker-01"


def heartbeat():
    r = requests.post(
        f"{API_BASE}/api/workers/{WORKER_ID}/heartbeat",
        json={"status": "ONLINE"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def get_next_step():
    r = requests.get(
        f"{API_BASE}/api/workers/{WORKER_ID}/next-step",
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def post_result(step_id: int, status: str, result_payload: str):
    r = requests.post(
        f"{API_BASE}/api/steps/{step_id}/result",
        json={"status": status, "result_payload": result_payload},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def execute_step(step: dict):
    step_type = step["type"]

    if step_type == "OBSERVE":
        print(f"[worker] OBSERVE step {step['id']}")
        time.sleep(0.5)
        return "SUCCESS", "mock observation complete"

    if step_type == "WAIT":
        print(f"[worker] WAIT step {step['id']}")
        time.sleep(1)
        return "SUCCESS", "wait complete"

    return "FAILED", f"unsupported step type: {step_type}"


def main():
    print(f"[worker] starting worker {WORKER_ID}")
    while True:
        try:
            hb = heartbeat()
            print(f"[worker] heartbeat ok: {hb['id']}")

            step = get_next_step()
            if not step:
                print("[worker] no work available")
                time.sleep(2)
                continue

            print(f"[worker] leased step {step['id']} ({step['type']})")
            status, payload = execute_step(step)
            result = post_result(step["id"], status, payload)
            print(f"[worker] posted result: {result}")

            time.sleep(1)

        except Exception as e:
            print(f"[worker] error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()