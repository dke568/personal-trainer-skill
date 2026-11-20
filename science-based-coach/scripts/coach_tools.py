#!/usr/bin/env python3
"""Deterministic diary and arithmetic. Standard library only; no network calls."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import sys


STATUSES = {"completed", "recalled", "planned", "adjusted", "in_progress", "unknown"}
PRECISIONS = {"day", "month", "unknown", "reported_unverified"}
BASES = {"barbell_total", "plates_only", "machine_stack_label", "bodyweight", "other"}
KINDS = {"user_report", "user_summary", "observed_material", "assistant_proposal",
         "synthetic_example"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, name, *, positive=False, nullable=False):
    if value is None and nullable:
        return
    require(type(value) in (int, float) and math.isfinite(value),
            f"{name}: expected a finite number")
    require(value > 0 if positive else value >= 0, f"{name}: invalid sign")


def iso_day(value, name):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
            f"{name}: expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name}: invalid date") from exc


def validate_event(event):
    require(isinstance(event, dict), "event must be an object")
    for key in ("schema_version", "id", "type", "status", "date",
                "date_precision", "recorded_at", "source"):
        require(key in event, f"missing {key}")
    require(type(event["schema_version"]) is int and event["schema_version"] == 1,
            "unsupported schema_version")
    require(isinstance(event["id"], str) and 0 < len(event["id"].strip()) <= 180,
            "invalid id")
    require(event["type"] in {"session", "measurement", "plan", "note"}, "invalid type")
    require(event["status"] in STATUSES, "invalid status")
    require(event["date_precision"] in PRECISIONS, "invalid date_precision")
    recorded = iso_day(event["recorded_at"], "recorded_at")
    precision = event["date_precision"]
    if precision in {"day", "reported_unverified"}:
        observed = iso_day(event["date"], "date")
        if event["status"] in {"completed", "recalled", "in_progress"}:
            require(observed <= recorded, "a completed/recalled event cannot be in the future")
    else:
        require(event["date"] is None, "imprecise date must be null")
    if precision == "month":
        period = event.get("period")
        require(isinstance(period, str) and re.fullmatch(r"\d{4}-\d{2}", period),
                "month precision requires period=YYYY-MM")
        iso_day(period + "-01", "period")
        if event["status"] in {"completed", "recalled", "in_progress"}:
            require(period <= recorded.strftime("%Y-%m"), "event month cannot be in the future")
    source = event["source"]
    require(isinstance(source, dict), "source must be an object")
    require(source.get("kind") in KINDS, "invalid source.kind")
    require(isinstance(source.get("note"), str) and source["note"].strip(),
            "source.note must describe provenance")
    if event["type"] in {"session", "measurement"} and event["status"] == "completed":
        require(source["kind"] != "assistant_proposal",
                "assistant proposal cannot confirm performed work")
    if event["type"] == "session":
        require(event["status"] in {"completed", "recalled", "in_progress", "unknown"},
                "prescribed sessions must be type=plan")
    if event["type"] == "plan":
        require(event["status"] in {"planned", "adjusted"}, "plan cannot be completed")
    if event["type"] in {"session", "plan"}:
        exercises = event.get("exercises")
        require(isinstance(exercises, list) and len(exercises) > 0, "missing exercises")
        for exercise in exercises:
            require(isinstance(exercise, dict), "exercise must be an object")
            require(isinstance(exercise.get("exercise_id"), str)
                    and exercise["exercise_id"].strip(), "missing exercise_id")
            require(exercise.get("load_basis") in BASES, "missing/invalid load_basis")
            for key in ("direct_muscles", "indirect_muscles"):
                muscles = exercise.get(key, [])
                require(isinstance(muscles, list) and all(
                    isinstance(m, str) and m.strip() for m in muscles), f"invalid {key}")
                require(len(muscles) == len(set(muscles)), f"duplicate {key}")
            require(not (set(exercise.get("direct_muscles", []))
                         & set(exercise.get("indirect_muscles", []))),
                    "a muscle cannot be both direct and indirect in one exercise")
            sets = exercise.get("sets")
            require(isinstance(sets, list) and len(sets) > 0, "missing sets")
            for item in sets:
                require(isinstance(item, dict), "set must be an object")
                require(item.get("kind") in {"working", "warmup"}, "missing set.kind")
                require("load_kg" in item, "missing load_kg")
                number(item["load_kg"], "load_kg", nullable=True)
                if "reps" in item:
                    require(item["reps"] is None or
                            (type(item["reps"]) is int and item["reps"] > 0),
                            "reps must be a positive integer or null")
                else:
                    require(event["type"] == "plan", "missing reps")
                    lo, hi = item.get("reps_min"), item.get("reps_max")
                    require(type(lo) is int and type(hi) is int and 0 < lo <= hi,
                            "plan needs a valid rep range")
                number(item.get("rir"), "rir", nullable=True)
    if event["type"] == "measurement":
        measurements = event.get("measurements")
        require(isinstance(measurements, list) and measurements, "missing measurements")
        for item in measurements:
            require(isinstance(item, dict) and isinstance(item.get("metric"), str)
                    and item["metric"].strip(), "invalid measurement metric")
            require(isinstance(item.get("unit"), str) and item["unit"].strip(),
                    "missing measurement unit")
            if "value" in item:
                number(item["value"], "measurement.value")
            else:
                lo, hi = item.get("low"), item.get("high")
                number(lo, "measurement.low")
                number(hi, "measurement.high")
                require(lo <= hi, "invalid measurement range")
    return event


def active_events(events):
    active, seen, history = {}, set(), {}
    for event in events:
        validate_event(event)
        event_id = event["id"]
        require(event_id not in seen, f"duplicate id: {event_id}")
        supersedes = event.get("supersedes")
        if supersedes is not None:
            require(supersedes in active, "supersedes must reference an active earlier event")
            require(active[supersedes]["type"] == event["type"],
                    "correction must preserve event type")
            require((active[supersedes]["source"]["kind"] == "synthetic_example")
                    == (event["source"]["kind"] == "synthetic_example"),
                    "synthetic examples cannot replace real records, or vice versa")
            del active[supersedes]
        if event.get("fulfills_plan_id") is not None:
            parent = history.get(event["fulfills_plan_id"])
            require(event["type"] == "session" and parent is not None
                    and parent["type"] == "plan", "fulfills_plan_id must reference a plan")
        seen.add(event_id)
        active[event_id] = event
        history[event_id] = event
    return list(active.values())


def read_events(path, *, missing_ok=False):
    path = Path(path)
    if not path.exists():
        require(missing_ok, f"journal does not exist: {path}")
        return []
    records = []
    # JSONL is delimited by LF, not by every Unicode character Python regards
    # as a line separator; U+2028 inside a JSON string is valid user text.
    for line_number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}") from exc
    active_events(records)
    return records


@contextmanager
def exclusive_lock(path):
    path = Path(path).expanduser().resolve()
    lock = Path(str(path) + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("journal is locked; no changes made") from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("journal is locked; no changes made") from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def append_event(log_path, event):
    path = Path(log_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    validate_event(event)
    with exclusive_lock(path):
        if path.exists():
            require(path.stat().st_nlink == 1,
                    "journals with multiple hard links are unsupported; use one canonical file")
        existing = read_events(path, missing_ok=True)
        active_events(existing + [event])
        # Preserve every pre-existing byte; reject malformed no-newline files
        # rather than accidentally joining two JSON objects.
        if path.exists() and path.stat().st_size:
            with path.open("rb") as stream:
                stream.seek(-1, os.SEEK_END)
                require(stream.read(1) == b"\n", "journal must end with a newline")
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return {"appended": event["id"], "records": len(existing) + 1}


def macro_energy(protein, carbs, fat):
    for key, value in (("protein", protein), ("carbs", carbs), ("fat", fat)):
        number(value, key)
    return {"protein_kcal": protein * 4, "carbohydrate_kcal": carbs * 4,
            "fat_kcal": fat * 9, "total_kcal": protein * 4 + carbs * 4 + fat * 9,
            "method": "4/4/9; all protein calories included"}


def estimated_1rm(load, reps):
    number(load, "load", positive=True)
    require(type(reps) is int and 1 <= reps <= 10, "estimate only supports 1–10 reps")
    epley = load if reps == 1 else load * (1 + reps / 30)
    brzycki = load * 36 / (37 - reps)
    return {"epley_kg": round(epley, 2), "brzycki_kg": round(brzycki, 2),
            "measured_maximum": False,
            "limitations": "RIR and technique not corrected; estimate, not a lifted maximum"}


def weekly_volume(events, start, end, *, indirect=0.5, coverage_complete=False):
    start_day, end_day = iso_day(start, "start"), iso_day(end, "end")
    require(start_day <= end_day, "start must not follow end")
    number(indirect, "indirect coefficient")
    require(indirect <= 1, "indirect coefficient must be between 0 and 1")
    direct, secondary = defaultdict(int), defaultdict(int)
    included, excluded = [], []
    total_sets = known_rir = unclassified = 0
    for event in active_events(events):
        reason = None
        if event["source"]["kind"] == "synthetic_example":
            reason = "synthetic example"
        elif event["type"] != "session" or event["status"] != "completed":
            reason = "not a confirmed completed session"
        elif event["date_precision"] != "day":
            reason = "unknown or unverified exact date"
        elif not start_day <= iso_day(event["date"], "date") <= end_day:
            continue
        if reason:
            excluded.append({"id": event["id"], "reason": reason})
            continue
        included.append(event["id"])
        for exercise in event["exercises"]:
            work = [s for s in exercise["sets"] if s["kind"] == "working"]
            count = len(work)
            total_sets += count
            known_rir += sum(s.get("rir") is not None for s in work)
            dm, im = exercise.get("direct_muscles", []), exercise.get("indirect_muscles", [])
            if not dm and not im:
                unclassified += count
            for muscle in dm:
                direct[muscle] += count
            for muscle in im:
                secondary[muscle] += count
    muscles = {}
    for muscle in sorted(set(direct) | set(secondary)):
        muscles[muscle] = {
            "direct_working_sets": direct[muscle],
            "indirect_working_sets": secondary[muscle],
            "fractional_estimate": direct[muscle] + indirect * secondary[muscle]}
    return {
        "start": start, "end": end,
        "scope": "known reported working sets; not automatically hard sets",
        "coverage": "complete_by_explicit_attestation" if coverage_complete else "unknown",
        "is_known_minimum_only": not coverage_complete,
        "indirect_coefficient": indirect,
        "working_sets": total_sets, "working_sets_with_rir": known_rir,
        "unclassified_working_sets": unclassified,
        "muscles": muscles, "records_used": included, "records_excluded": excluded}


def audit_registry(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    require(data.get("schema_version") == 1, "invalid registry schema")
    records = data.get("sources", [])
    require(isinstance(records, list) and records, "empty sources")
    seen = set()
    for item in records:
        require(item["id"] not in seen, "duplicate source id")
        seen.add(item["id"])
        for key in ("title", "year", "url", "design", "takeaway", "limitations",
                    "access", "checked_at"):
            require(item.get(key) is not None and item.get(key) != "", f"source missing {key}")
        iso_day(item["checked_at"], "checked_at")
        require(item["url"].startswith("https://"), "source requires an HTTPS URL")
        if item.get("pmid"):
            require(str(item["pmid"]).isdigit(), "invalid PMID")
            require(str(item["pmid"]) in item["url"], "PMID/URL mismatch")
        if item.get("doi"):
            require(re.fullmatch(r"10\.\d{4,9}/\S+", item["doi"]), "invalid DOI")
    return {"sources": len(records), "unique_ids": sorted(seen),
            "check": "offline structure/identifiers, not independent scientific validation"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    macros = sub.add_parser("macros")
    for name in ("protein", "carbs", "fat"):
        macros.add_argument("--" + name, type=float, required=True)
    maximum = sub.add_parser("e1rm")
    maximum.add_argument("--load", type=float, required=True)
    maximum.add_argument("--reps", type=int, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--log", required=True)
    append = sub.add_parser("append")
    append.add_argument("--log", required=True)
    append.add_argument("--event", required=True)
    volume = sub.add_parser("weekly-volume")
    volume.add_argument("--log", required=True)
    volume.add_argument("--start", required=True)
    volume.add_argument("--end", required=True)
    volume.add_argument("--indirect", type=float, default=0.5)
    volume.add_argument("--coverage-complete", action="store_true",
                        help="Use only after explicit confirmation that all sessions are logged")
    evidence = sub.add_parser("audit-sources")
    evidence.add_argument("--registry", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "macros":
            result = macro_energy(args.protein, args.carbs, args.fat)
        elif args.command == "e1rm":
            result = estimated_1rm(args.load, args.reps)
        elif args.command == "validate":
            events = read_events(args.log)
            result = {"records": len(events), "active_records": len(active_events(events))}
        elif args.command == "append":
            result = append_event(args.log, json.loads(Path(args.event).read_text(encoding="utf-8")))
        elif args.command == "weekly-volume":
            result = weekly_volume(read_events(args.log), args.start, args.end,
                                   indirect=args.indirect, coverage_complete=args.coverage_complete)
        else:
            result = audit_registry(args.registry)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
