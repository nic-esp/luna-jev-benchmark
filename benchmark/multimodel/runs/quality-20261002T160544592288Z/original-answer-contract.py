#!/usr/bin/env python3
"""A small paired OpenRouter benchmark. Python 3.9+, standard library only."""
import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import random
import ssl
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
MODELS = {"Jev": "typesafe/jev-1.13", "Luna": "openai/gpt-6-luna"}
SCHEMA = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["noul"]},
    "noul": {"type": "number", "minimum": 0, "maximum": 1}},
    "required": ["type", "noul"], "additionalProperties": False}
SYSTEM = ("Answer the single typed question using the supplied state and criteria. "
          "Return only the answer object with exactly two fields: type (the string noul) "
          "and noul (a number from 0 to 1 representing the probability that the answer is yes). "
          "The probability of no is 1 minus noul. For a random experiment, estimate the "
          "event probability under the stated sampling process. Do not return explanations.")

def shared_input(case):
    return {"state": case["state"], "questions": {"answer": {
        "type": "noul", "instructions": case["question"], "criteria": case["criteria"]}}}

def payload_for(case, model_label):
    shared = shared_input(case)
    if model_label == "Jev":
        return "/api/alpha/decisions", {"model": MODELS[model_label], **shared}
    if model_label != "Luna":
        raise ValueError("Unknown model label")
    return "/api/v1/chat/completions", {
        "model": MODELS[model_label],
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": json.dumps(shared, ensure_ascii=False, sort_keys=True)}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "noul_answer", "strict": True, "schema": SCHEMA}},
        "reasoning": {"effort": "none"}, "max_tokens": 128,
        "prompt_cache_options": {"mode": "explicit"},
        "provider": {"only": ["OpenAI"], "allow_fallbacks": False, "require_parameters": True},
        "stream": False,
    }

def validate_answer(answer):
    if not isinstance(answer, dict) or set(answer) != {"type", "noul"}:
        raise ValueError("Answer must have exactly type and noul fields")
    p = answer["noul"]
    if answer["type"] != "noul" or isinstance(p, bool) or not isinstance(p, (float, int)):
        raise ValueError("Answer has wrong field types")
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("Probability is outside [0,1]")
    return float(p)

def validate_model(label, response_model):
    requested = MODELS[label]
    if not isinstance(response_model, str) or not (
        response_model == requested or response_model.startswith(requested + "-20")):
        raise ValueError("Unexpected or missing served model: " + str(response_model))

class Client:
    def __init__(self, key, timeout=60):
        self.key, self.timeout, self.connection = key, timeout, None

    def close(self):
        if self.connection:
            self.connection.close()
        self.connection = None

    def run(self, case, label, phase, sequence, request_override=None, endpoint_override=None, base_label=None):
        base_label = base_label or label
        endpoint, payload = payload_for(case, base_label)
        payload = request_override if request_override is not None else payload
        endpoint = endpoint_override or endpoint
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        record = {"case_id": case["id"], "experiment": case["experiment"],
                  "target": case["target"], "target_kind": case["target_kind"],
                  "model_label": label, "requested_model": MODELS[base_label],
                  "phase": phase, "sequence": sequence,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "request": payload, "endpoint": endpoint,
                  "probability": None, "valid": False, "error": None,
                  "cost_usd": None, "usage": {}, "response_model": None, "provider": None}
        start = time.perf_counter()
        try:
            if self.connection is None:
                self.connection = http.client.HTTPSConnection("openrouter.ai", timeout=self.timeout,
                                                               context=ssl.create_default_context())
            self.connection.request("POST", endpoint, body=encoded, headers={
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                "X-Title": "Luna versus Jev local pilot"})
            response = self.connection.getresponse()
            body = response.read().decode("utf-8")
            record["http_status"] = response.status
            data = json.loads(body)
            record["response"] = data
            if response.status != 200 or "error" in data:
                raise ValueError("HTTP/API error: " + json.dumps(data)[:1200])
            usage = data.get("usage") or {}
            record.update(usage=usage, response_model=data.get("model"), provider=data.get("provider"),
                          generation_id=data.get("id"))
            cost = usage.get("cost")
            if isinstance(cost, (float, int)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                record["cost_usd"] = float(cost)
            validate_model(base_label, data.get("model"))
            if base_label == "Jev":
                answer = data["answers"]["answer"]
            else:
                choice = data["choices"][0]
                record["finish_reason"] = choice.get("finish_reason")
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Completion did not finish normally")
                answer = json.loads(choice["message"]["content"])
            record["probability"] = validate_answer(answer)
            record["answer"] = answer
            record["valid"] = True
        except Exception as exc:
            record["error"] = (type(exc).__name__ + ": " + str(exc)).replace(self.key, "[REDACTED]")
            self.close()
        record["latency_s"] = time.perf_counter() - start
        # Protect logs even if an upstream error unexpectedly echoes a credential.
        return json.loads(json.dumps(record).replace(self.key, "[REDACTED]"))

def load_cases(path):
    cases = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    for c in cases:
        if c["target_kind"] not in ("label", "probability") or not 0 <= c["target"] <= 1:
            raise ValueError("Invalid target")
        if c["target_kind"] == "label" and c["target"] not in (0, 1):
            raise ValueError("Nonbinary label")
        if len(json.dumps(shared_input(c)).encode()) > 25000:
            raise ValueError("Case exceeds pilot input limit")
    return cases

def warmup_cases():
    return [
        {"id": "warmup-yes", "experiment": "warmup", "state": {"rule": "Approve if the number is at least 10.", "number": 15},
         "question": "Is approval required under the rule?", "criteria": {"true": "The rule requires approval.", "false": "The rule does not require approval."}, "target": 1, "target_kind": "label"},
        {"id": "warmup-no", "experiment": "warmup", "state": {"rule": "Approve if the number is at least 10.", "number": 4},
         "question": "Is approval required under the rule?", "criteria": {"true": "The rule requires approval.", "false": "The rule does not require approval."}, "target": 0, "target_kind": "label"},
    ]

def run_benchmark(key, cases_path=ROOT / "data/cases.jsonl", out_dir=None, limit=None,
                  budget=1.0, seed=20261002, progress=None):
    if not key.startswith("sk-or-"):
        raise ValueError("Expected an OpenRouter API key")
    if budget <= 0 or not math.isfinite(budget):
        raise ValueError("Budget must be positive and finite")
    cases = load_cases(cases_path)
    rng = random.Random(seed)
    rng.shuffle(cases)
    if limit is not None:
        cases = cases[:limit]
    out_dir = Path(out_dir or ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out_dir.mkdir(parents=True, exist_ok=False)
    raw_path = out_dir / "responses.jsonl"
    metadata = {"models": MODELS, "created_at": datetime.now(timezone.utc).isoformat(),
                "seed": seed, "cases": len(cases), "calls_planned": 2 * len(cases),
                "budget_usd": budget, "luna_reasoning": "none", "luna_provider": "OpenAI",
                "protocol": "One non-streaming request per model per case; sequential paired random order; one shared persistent HTTPS connection; no automatic retries; two warmups per model excluded from measured results.",
                "cases_sha256": hashlib.sha256(Path(cases_path).read_bytes()).hexdigest(),
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "status": "running", "charged_usd": 0.0}
    meta_path = out_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    client = Client(key)
    cost = 0.0
    sequence = 0
    try:
        with raw_path.open("a", buffering=1) as raw:
            for phase, phase_cases in [("warmup", warmup_cases()), ("measured", cases)]:
                for case in phase_cases:
                    labels = list(MODELS)
                    rng.shuffle(labels)
                    for label in labels:
                        # Conservative reservation: every UTF-8 byte as an input token,
                        # plus format overhead and 128 completion tokens. Service pricing
                        # can change, so the response charge is also checked after each call.
                        _, request = payload_for(case, label)
                        reserve = (len(json.dumps(request).encode()) + 4096) * 0.0000002 + 128 * 0.00000075
                        if cost + reserve > budget:
                            raise RuntimeError("Budget guard stopped before next request")
                        record = client.run(case, label, phase, sequence)
                        sequence += 1
                        raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                        if record["cost_usd"] is not None:
                            cost += record["cost_usd"]
                        metadata.update(charged_usd=cost, calls_completed=sequence)
                        meta_path.write_text(json.dumps(metadata, indent=2))
                        msg = {"phase": phase, "completed": sequence, "model": label,
                               "case": case["id"], "valid": record["valid"],
                               "latency_s": round(record["latency_s"], 3), "charged_usd": cost}
                        if progress:
                            progress(msg)
                        else:
                            print(json.dumps(msg), flush=True)
                        if phase == "warmup" and not record["valid"]:
                            raise RuntimeError("Warmup failed: " + str(record["error"]))
                        if record["cost_usd"] is None:
                            raise RuntimeError("Missing reported cost; stopped to retain budget accounting")
                        if cost > budget:
                            raise RuntimeError("Observed charge exceeded budget; stopped")
        metadata["status"] = "complete"
    except Exception as exc:
        metadata.update(status="stopped", error=str(exc).replace(key, "[REDACTED]"))
    finally:
        client.close()
        metadata.update(charged_usd=cost, finished_at=datetime.now(timezone.utc).isoformat())
        meta_path.write_text(json.dumps(metadata, indent=2))
    if metadata["status"] == "complete":
        from report import generate_report
        generate_report(raw_path, out_dir, metadata=metadata)
    print(json.dumps({"run_dir": str(out_dir), **metadata}), flush=True)
    return out_dir, metadata

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "data/cases.jsonl")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--budget", type=float, default=1.0)
    parser.add_argument("--key-stdin", action="store_true", help="Read a key from stdin without saving it")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        cases = load_cases(args.cases)
        print(json.dumps({"cases": len(cases), "requests": 2 * len(cases), "models": MODELS,
                          "first_payloads": {m: payload_for(cases[0], m) for m in MODELS}}, indent=2))
        return
    key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("Set OPENROUTER_API_KEY or use --key-stdin. Never put a key in a command argument.")
    _, meta = run_benchmark(key, args.cases, args.out_dir, args.limit, args.budget)
    if meta["status"] != "complete":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
