#!/usr/bin/env python3
"""Prepare an atomic GitHub diary change and verify provider read-back. No network or credentials."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import PurePosixPath, Path
import re
import sys
from coach_tools import active_events, iso_day, require, validate_event
from state_summary import build as build_summary


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git_blob(text):
    raw = text.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def load_json(text):
    def invalid(value):
        raise ValueError("non-finite JSON number: " + value)
    return json.loads(text, parse_constant=invalid)


def jsonl(text):
    require(not text or text.endswith("\n"), "JSONL must end with LF; preserve damaged input")
    return [load_json(line) for line in text.split("\n") if line.strip()]


def append_lines(text, records):
    jsonl(text)
    return text + "".join(canonical(record) + "\n" for record in records)


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def validate_ledger(text):
    records = jsonl(text)
    previous = None
    ids = set()
    for entry in records:
        require(isinstance(entry, dict), "invalid change entry")
        require(entry.get("id") not in ids, "duplicate change id")
        require(entry.get("previous_entry_hash") == previous, "broken change chain")
        unsigned = {k: v for k, v in entry.items() if k != "entry_hash"}
        require(sha256(canonical(unsigned)) == entry.get("entry_hash"), "changed historical entry")
        ids.add(entry["id"])
        previous = entry["entry_hash"]
    return records


def prepare(snapshot, change):
    require(snapshot.get("schema_version") == 1 and change.get("schema_version") == 1,
            "unsupported schema")
    require(valid_sha(snapshot.get("head")) and valid_sha(snapshot.get("tree")), "missing base SHA")
    config = snapshot.get("config", {})
    require(config.get("provider") == "github", "GitHub storage configuration required")
    repository = config.get("repository_full_name", "")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "invalid repository")
    branch = config.get("branch")
    require(isinstance(branch, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
            and ".." not in branch and "//" not in branch and not branch.endswith(("/", "."))
            and all(not part.endswith(".lock") and not part.startswith(".") for part in branch.split("/")),
            "invalid branch")
    athlete = config.get("athlete_id")
    require(isinstance(athlete, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", athlete), "invalid athlete")
    require(change.get("athlete_id") == athlete, "athlete mismatch")
    require(change.get("publication_scope") == "training_only", "training-only scope required")
    require(change.get("kind") in {"fact_correction", "workout_log", "plan_adjustment",
                                  "preference_update", "context_update"}, "invalid change kind")
    require(change.get("status") in {"confirmed", "proposed"}, "invalid change status")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", change.get("id", "")), "invalid change id")
    for field in ["summary", "reason", "next_step"]:
        require(isinstance(change.get(field), str) and change[field].strip(), "missing " + field)
    iso_day(change.get("recorded_at"), "recorded_at")
    source = change.get("source")
    require(isinstance(source, dict) and source.get("kind") in {"user_report", "assistant_proposal"}
            and isinstance(source.get("note"), str) and source["note"].strip(), "invalid change source")
    if source["kind"] == "assistant_proposal":
        require(change["status"] == "proposed", "proposal cannot become a confirmed change")
    questions = change.get("open_questions")
    require(questions is None or (isinstance(questions, list) and all(isinstance(q, str) for q in questions)), "invalid questions")
    prefix = "coach-data/" + athlete + "/"
    files = snapshot.get("files")
    require(isinstance(files, dict) and all(isinstance(v, str) for v in files.values()), "invalid files")
    for path in files:
        require(path.startswith(prefix) and ".." not in PurePosixPath(path).parts
                and "\\" not in path, "out-of-scope snapshot path")
    for name in ["profile.json", "programme.md", "events.jsonl", "decisions.md", "handoff.md"]:
        require(prefix + name in files, "missing snapshot file " + name)
    # The durable policy is configuration, not a hidden credential or clinical profile.
    storage_path = prefix + "storage.json"
    bootstrap = storage_path not in files
    if bootstrap:
        require(snapshot.get("bootstrap_storage") is True and prefix + "changes.jsonl" not in files,
                "missing storage configuration; explicit bootstrap required")
    else:
        require(load_json(files[storage_path]) == config, "configuration snapshot mismatch")
    ledger_path = prefix + "changes.jsonl"
    # Existing checkpoints prove this is an initialized diary, not a new ledger.
    checkpoint_paths = [prefix + name for name in ["checkpoint.json", "CURRENT.md"]]
    initialized = ledger_path in files or any(path in files for path in checkpoint_paths)
    require(bootstrap or initialized, "missing initialized ledger; explicit recovery required")
    if initialized:
        require(ledger_path in files and all(path in files for path in checkpoint_paths),
                "incomplete initialized diary; preserve history and recover missing files")
    ledger_text = files.get(ledger_path, "")
    ledger = validate_ledger(ledger_text)
    if initialized:
        require(ledger, "empty initialized ledger; preserve history and recover missing entries")
    fingerprint = sha256(canonical(change))
    prior_events = jsonl(files[prefix + "events.jsonl"])
    active_events(prior_events)
    load_json(files[prefix + "profile.json"])
    if ledger:
        require(isinstance(ledger[-1].get("state_hashes"), dict), "missing state integrity map")
        required_state = {prefix + name for name in ["profile.json", "programme.md", "events.jsonl",
                          "decisions.md", "handoff.md", "storage.json", "checkpoint.json", "CURRENT.md"]}
        require(required_state <= set(ledger[-1]["state_hashes"]), "incomplete state integrity map")
        for path, expected in ledger[-1]["state_hashes"].items():
            require(path in files and sha256(files[path]) == expected, "snapshot integrity mismatch: " + path)
    for entry in ledger:
        if entry["id"] == change["id"]:
            require(entry["payload_sha256"] == fingerprint, "change id reused with different data")
            return {"schema_version": 1, "status": "already_recorded_in_snapshot",
                    "repository_full_name": repository, "branch": branch,
                    "snapshot_head": snapshot["head"], "change_id": change["id"], "files": []}
    prior_questions = load_json(files[prefix + "checkpoint.json"]).get("open_questions", []) if ledger else []
    require(isinstance(prior_questions, list) and all(isinstance(q, str) for q in prior_questions),
            "invalid inherited questions")
    if questions is None:
        questions = list(prior_questions)
    resolutions = change.get("resolved_questions", [])
    require(isinstance(resolutions, list) and all(isinstance(r, dict)
            and isinstance(r.get("question"), str) and isinstance(r.get("reason"), str)
            and r["reason"].strip() for r in resolutions), "invalid question resolutions")
    removed = set(prior_questions) - set(questions)
    require(removed == {r["question"] for r in resolutions},
            "removed questions need explicit resolutions with reasons")
    event_path = prefix + "events.jsonl"
    prior_events = jsonl(files[event_path])
    active_events(prior_events)
    new_events = change.get("events", [])
    require(isinstance(new_events, list) and all(isinstance(e, dict) for e in new_events),
            "events must be a list of objects")
    if change["kind"] in {"fact_correction", "workout_log"}:
        require(new_events, "factual change needs a provenance event")
    if change["kind"] == "fact_correction":
        require(any(e.get("supersedes") for e in new_events), "fact correction must supersede an earlier fact")
    for event in new_events:
        validate_event(event)
        if source["kind"] == "assistant_proposal" or change["status"] == "proposed":
            require(event["source"]["kind"] == "assistant_proposal" and event["type"] in {"plan", "note"}
                    and event["status"] in {"planned", "adjusted"},
                    "proposed change cannot rewrite performed facts")
        require(event["source"]["kind"] != "synthetic_example", "do not publish synthetic athlete events")
        require(event["recorded_at"] == change["recorded_at"], "event/change recorded_at mismatch")
    active_events(prior_events + new_events)
    updates = change.get("updates", {})
    require(isinstance(updates, dict), "invalid updates")
    if "programme.md" in updates:
        require(change["kind"] == "plan_adjustment" and any(e["type"] == "plan" for e in new_events),
                "programme change requires an explicitly planned event")
    output = {}
    if bootstrap:
        output[storage_path] = json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    for name, update in updates.items():
        require(name in {"profile.json", "programme.md"}, "unsupported state update path")
        require(isinstance(update, dict) and isinstance(update.get("content"), str), "invalid update")
        path = prefix + name
        require(update.get("before_sha256") == sha256(files[path]), "stale update: " + name)
        if name.endswith(".json"):
            require(change["status"] == "confirmed" and source["kind"] == "user_report",
                    "a proposal cannot rewrite factual profile state")
            profile = load_json(update["content"])
            require(profile.get("athlete_id") == athlete, "profile athlete mismatch")
        output[path] = update["content"]
        if name == "programme.md":
            output[path] = "Статус последней правки: " + change["status"] + "; выполнение не подтверждено.\n\n" + output[path]
    if new_events:
        output[event_path] = append_lines(files[event_path], new_events)
    decision = ("\n\n## " + change["recorded_at"] + " · " + change["id"] + "\n"
                + "Статус: " + change["status"] + ".\n\n" + change["summary"] + "\n\n"
                + "Основание: " + change["reason"] + "\n\n"
                + "Следующий шаг: " + change["next_step"] + "\n")
    output[prefix + "decisions.md"] = files[prefix + "decisions.md"] + decision
    checkpoint = {"schema_version": 1, "athlete_id": athlete, "updated_at": change["recorded_at"],
                  "last_change_id": change["id"], "status": change["status"],
                  "summary": change["summary"], "next_step": change["next_step"],
                  "open_questions": questions, "parent_head": snapshot["head"],
                  "primary_files": ["profile.json", "programme.md", "events.jsonl", "changes.jsonl", "decisions.md"]}
    output[prefix + "checkpoint.json"] = json.dumps(checkpoint, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    profile = load_json(output.get(prefix + "profile.json", files[prefix + "profile.json"]))
    qtext = "\n".join("- " + q for q in questions) or "- См. неопределённости профиля и первичных событий."
    handoff = ("# Точка продолжения: " + athlete + "\n\n"
               + "Последняя корректировка: " + change["recorded_at"] + ".\n"
               + "Это дата записи; даты замеров и тренировок сохраняются в первичных событиях.\n"
               + "Профиль: снимок " + str(profile.get("as_of", "дата неизвестна")) + ".\n\n"
               + "## Последнее изменение\n" + change["summary"] + "\n\n"
               + "Статус: " + change["status"] + "; ID: " + change["id"] + ".\n\n"
               + "## Что читать\n[Профиль](profile.json), [план](programme.md), "
               + "[события](events.jsonl), [корректировки](changes.jsonl), "
               + "[обоснования](decisions.md), [состояние продолжения](checkpoint.json), "
               + "[сводка последних записей](SUMMARY.md).\n\n"
               + "## Следующий шаг\n" + change["next_step"] + "\n\n"
               + "## Неопределённости\n" + qtext + "\n\n"
               + "План не подтверждает выполнение. Не восстанавливай неизвестные даты. "
               + "Перед новым советом прочитай последнюю удалённую версию.\n")
    output[prefix + "handoff.md"] = handoff
    output[prefix + "CURRENT.md"] = handoff
    summary_inputs = dict(files)
    summary_inputs.update(output)
    summary, summary_md = build_summary({path[len(prefix):]: content for path, content in summary_inputs.items()},
                                        as_of=change["recorded_at"], source_head=snapshot["head"])
    output[prefix + "summary.json"] = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output[prefix + "SUMMARY.md"] = summary_md
    entry = {"schema_version": 1, "id": change["id"], "recorded_at": change["recorded_at"],
             "kind": change["kind"], "status": change["status"], "source": source,
             "summary": change["summary"], "reason": change["reason"],
             "next_step": change["next_step"], "open_questions": questions,
             "resolved_questions": resolutions,
             "event_ids": [e["id"] for e in new_events], "parent_head": snapshot["head"],
             "payload_sha256": fingerprint,
             "previous_entry_hash": ledger[-1]["entry_hash"] if ledger else None,
             "updated_files": {path: sha256(content) for path, content in sorted(output.items())}}
    state_files = dict(files)
    state_files.update(output)
    entry["state_hashes"] = {path: sha256(text) for path, text in sorted(state_files.items()) if path != ledger_path}
    entry["entry_hash"] = sha256(canonical(entry))
    output[ledger_path] = append_lines(ledger_text, [entry])
    manifest = [{"path": path, "mode": "100644", "type": "blob", "content": text,
                 "before_sha256": sha256(files[path]) if path in files else None,
                 "expected_blob_sha": git_blob(text)} for path, text in sorted(output.items())]
    return {"schema_version": 1, "status": "prepared_not_saved", "repository_full_name": repository,
            "branch": branch, "expected_head": snapshot["head"], "base_tree_sha": snapshot["tree"],
            "change_id": change["id"], "payload_sha256": fingerprint, "files": manifest,
            "expected_state_blobs": {path: git_blob(text) for path, text in state_files.items() if path != ledger_path}}


def verify(bundle, commit, tree, head):
    require(bundle.get("status") == "prepared_not_saved", "need a prepared bundle")
    require(tree.get("truncated") is False, "cannot verify an incomplete tree")
    require(valid_sha(tree.get("sha")) and valid_sha(commit.get("tree", {}).get("sha")), "missing tree SHA")
    api = "https://api.github.com/repos/" + bundle["repository_full_name"]
    require(commit.get("url") == api + "/git/commits/" + commit.get("sha", ""), "commit repository mismatch")
    require(tree.get("url") == api + "/git/trees/" + tree["sha"], "tree repository mismatch")
    require(head.get("ref") == "refs/heads/" + bundle["branch"]
            and head.get("url") == api + "/git/refs/heads/" + bundle["branch"]
            and head.get("object", {}).get("type") == "commit", "repository/branch/type mismatch")
    require(valid_sha(commit.get("sha")), "missing commit SHA")
    require(commit.get("tree", {}).get("sha") == tree.get("sha"), "commit/tree mismatch")
    require([p.get("sha") for p in commit.get("parents", [])] == [bundle["expected_head"]],
            "unexpected parent; do not overwrite concurrent history")
    require(head.get("object", {}).get("sha") == commit["sha"], "remote head changed; reread latest state")
    actual = {item["path"]: item for item in tree.get("tree", [])}
    expected = dict(bundle.get("expected_state_blobs", {}))
    expected.update({item["path"]: item["expected_blob_sha"] for item in bundle["files"]})
    require(expected, "empty read-back manifest")
    for path, blob in expected.items():
        found = actual.get(path, {})
        require(found.get("type") == "blob" and found.get("mode") == "100644" and found.get("sha") == blob,
                "read-back mismatch: " + path)
    return {"status": "verified_saved", "change_id": bundle["change_id"], "commit": commit["sha"],
            "url": "https://github.com/" + bundle["repository_full_name"] + "/commit/" + commit["sha"],
            "verified_files": len(bundle["files"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--snapshot", required=True); p.add_argument("--change", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("verify")
    for name in ["bundle", "commit", "tree", "head"]: p.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        def read(name): return load_json(Path(getattr(args, name)).read_text(encoding="utf-8"))
        if args.command == "prepare":
            result = prepare(read("snapshot"), read("change"))
            Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False))
        else:
            print(json.dumps(verify(read("bundle"), read("commit"), read("tree"), read("head")), ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
