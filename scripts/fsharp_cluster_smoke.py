#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


TERMINAL = {"completed", "failed", "cancelled"}


def http_json(base_url: str, method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def poll_job(base_url: str, job_id: str, timeout: int):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = http_json(base_url, "GET", f"/rewrite/jobs/{job_id}")
        if job["status"] in TERMINAL:
            return job
        time.sleep(2)
    raise TimeoutError(f"Job {job_id} did not reach a terminal state within {timeout}s")


def wait_for_campaign_completion(base_url: str, campaign_id: str, timeout: int):
    deadline = time.time() + timeout
    while time.time() < deadline:
        campaign = http_json(base_url, "GET", f"/rewrite/campaigns/{campaign_id}")
        if campaign["summary"].get("completed_runs", 0) >= 1:
            return campaign
        time.sleep(1)
    raise TimeoutError(f"Campaign {campaign_id} did not complete a run within {timeout}s")


def run_benchmark_smoke(base_url: str, manifest_path: str, seed: int, timeout: int):
    campaign = http_json(
        base_url,
        "POST",
        "/rewrite/campaigns/queued",
        {
            "name": "Cluster smoke F# benchmark",
            "objective": "Validate the F# worker pod hot path in K3s.",
            "notes": "Reproducible benchmark cluster smoke.",
            "programId": None,
            "benchmarkSpecs": [
                {
                    "label": f"seed-{seed}",
                    "request": {
                        "datasetManifestPath": manifest_path,
                        "externalTestManifestPath": None,
                        "trainSplit": "train",
                        "testSplit": "test",
                        "seed": seed,
                    },
                }
            ],
        },
    )
    campaign = wait_for_campaign_completion(base_url, campaign["campaignId"], timeout)
    launch = http_json(
        base_url,
        "POST",
        f"/rewrite/campaigns/{campaign['campaignId']}/k8s-plan/launch",
        {},
    )
    item = launch["launch_receipt"]["items"][0]
    dispatch_job = poll_job(base_url, item["dispatchJobId"], timeout)
    target_job = poll_job(base_url, item["targetJobId"], timeout)
    return {
        "mode": "benchmark",
        "campaign_id": campaign["campaignId"],
        "dispatch_job_id": item["dispatchJobId"],
        "target_job_id": item["targetJobId"],
        "dispatch_job": dispatch_job,
        "target_job": target_job,
    }


def run_sounio_smoke(base_url: str, kernel_path: str, require_snio: bool, timeout: int):
    launch = http_json(
        base_url,
        "POST",
        "/rewrite/jobs/sounio-runtime/launch-k8s",
        {
            "label": "Cluster smoke Sounio runtime",
            "kernelPath": kernel_path,
            "persistArtifacts": True,
            "requireSnio": require_snio,
        },
    )
    item = launch["launch_item"]
    dispatch_job = poll_job(base_url, item["dispatchJobId"], timeout)
    target_job = poll_job(base_url, launch["target_job_id"], timeout)
    return {
        "mode": "sounio",
        "dispatch_job_id": item["dispatchJobId"],
        "target_job_id": launch["target_job_id"],
        "dispatch_job": dispatch_job,
        "target_job": target_job,
    }


def main():
    parser = argparse.ArgumentParser(description="Run a reproducible F# cluster smoke against the rewrite API.")
    parser.add_argument("--api-base", default="http://127.0.0.1:5124")
    parser.add_argument("--mode", choices=["benchmark", "sounio"], required=True)
    parser.add_argument("--manifest-path", default="/datasets/fixture-mini/small_benchmark_manifest.json")
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--kernel-path", default="/app/sounio/kernels/runtime_probe.sio")
    parser.add_argument("--require-snio", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    try:
        if args.mode == "benchmark":
            result = run_benchmark_smoke(args.api_base, args.manifest_path, args.seed, args.timeout)
        else:
            result = run_sounio_smoke(args.api_base, args.kernel_path, args.require_snio, args.timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 1

    print(json.dumps(result, indent=2))
    if result["dispatch_job"]["status"] != "completed" or result["target_job"]["status"] != "completed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
