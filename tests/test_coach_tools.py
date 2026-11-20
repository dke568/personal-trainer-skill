"""Contracts that protect athlete data, provenance and numerical interpretation."""
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path(os.environ.get("COACH_SKILL_ROOT", ROOT / "science-based-coach"))
spec = importlib.util.spec_from_file_location("coach_tools", SKILL / "scripts/coach_tools.py")
coach = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coach)


def session(event_id="session-1", day="2026-11-15"):
    return {
        "schema_version": 1, "id": event_id, "type": "session",
        "status": "completed", "date": day, "date_precision": "day",
        "recorded_at": "2026-11-20",
        "source": {"kind": "user_report", "note": "Explicit performed sets"},
        "exercises": [{
            "exercise_id": "bench", "load_basis": "barbell_total",
            "direct_muscles": ["chest"], "indirect_muscles": ["triceps"],
            "sets": [
                {"kind": "warmup", "load_kg": 20, "reps": 10, "rir": None},
                {"kind": "working", "load_kg": 80, "reps": 5, "rir": None}
            ]}]}


def volume(events, **kwargs):
    return coach.weekly_volume(events, "2026-11-09", "2026-11-15", **kwargs)


class Arithmetic(unittest.TestCase):
    def test_all_protein_energy_is_counted(self):
        self.assertEqual(coach.macro_energy(200, 100, 50)["total_kcal"], 1650)

    def test_reported_macro_range(self):
        self.assertEqual(coach.macro_energy(200, 100, 70)["total_kcal"], 1830)

    def test_negative_or_nonfinite_macro_rejected(self):
        for bad in [-1, math.nan, math.inf, True]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                coach.macro_energy(bad, 100, 50)

    def test_estimate_is_not_a_measured_maximum(self):
        result = coach.estimated_1rm(80, 5)
        self.assertFalse(result["measured_maximum"])
        self.assertAlmostEqual(result["epley_kg"], 93.33)

    def test_single_rep_is_not_inflated(self):
        self.assertEqual(coach.estimated_1rm(100, 1)["epley_kg"], 100)

    def test_high_rep_maximum_estimate_rejected(self):
        with self.assertRaises(ValueError):
            coach.estimated_1rm(60, 30)


class Provenance(unittest.TestCase):
    def test_assistant_cannot_confirm_a_workout(self):
        event = session()
        event["source"]["kind"] = "assistant_proposal"
        with self.assertRaises(ValueError):
            coach.validate_event(event)

    def test_plan_cannot_be_marked_completed(self):
        event = session()
        event["type"] = "plan"
        with self.assertRaises(ValueError):
            coach.validate_event(event)

    def test_unknown_date_stays_unknown(self):
        event = session()
        event.update(date=None, date_precision="month", period="2026-11")
        self.assertEqual(coach.validate_event(event)["date"], None)
        self.assertEqual(volume([event])["working_sets"], 0)

    def test_completed_future_date_rejected(self):
        with self.assertRaises(ValueError):
            coach.validate_event(session(day="2026-11-21"))

    def test_completed_future_month_rejected(self):
        event = session()
        event.update(date=None, date_precision="month", period="2026-12")
        with self.assertRaises(ValueError):
            coach.validate_event(event)

    def test_day_precision_requires_actual_date(self):
        event = session()
        event["date"] = None
        with self.assertRaises(ValueError):
            coach.validate_event(event)

    def test_nonfinite_load_rejected(self):
        event = session()
        event["exercises"][0]["sets"][1]["load_kg"] = math.nan
        with self.assertRaises(ValueError):
            coach.validate_event(event)

    def test_direct_and_indirect_cannot_double_count_same_muscle(self):
        event = session()
        event["exercises"][0]["indirect_muscles"] = ["chest"]
        with self.assertRaises(ValueError):
            coach.validate_event(event)


class Volume(unittest.TestCase):
    def test_warmups_are_excluded_and_unknown_rir_remains_unknown(self):
        result = volume([session()])
        self.assertEqual(result["working_sets"], 1)
        self.assertEqual(result["working_sets_with_rir"], 0)

    def test_missing_sessions_do_not_become_complete_coverage(self):
        self.assertTrue(volume([session()])["is_known_minimum_only"])
        self.assertEqual(volume([session()])["coverage"], "unknown")

    def test_fractional_volume_is_a_model(self):
        for coefficient in [0, 0.5, 1]:
            result = volume([session()], indirect=coefficient)
            self.assertEqual(result["muscles"]["triceps"]["fractional_estimate"], coefficient)
            self.assertEqual(result["muscles"]["chest"]["direct_working_sets"], 1)

    def test_recalled_inprogress_planned_and_synthetic_are_excluded(self):
        events = []
        for i, status in enumerate(["recalled", "in_progress"]):
            event = session("s-" + str(i))
            event["status"] = status
            events.append(event)
        planned = session("plan")
        planned.update(type="plan", status="planned")
        events.append(planned)
        synthetic = session("example")
        synthetic["source"]["kind"] = "synthetic_example"
        events.append(synthetic)
        self.assertEqual(volume(events)["working_sets"], 0)

    def test_reported_unverified_date_not_used_as_weekly_fact(self):
        event = session()
        event["date_precision"] = "reported_unverified"
        self.assertEqual(volume([event])["working_sets"], 0)

    def test_outside_period_excluded(self):
        self.assertEqual(volume([session(day="2026-11-08")])["working_sets"], 0)


class Persistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / "events.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def test_append_preserves_history_and_correction_replaces_active_fact(self):
        first = session()
        coach.append_event(self.log, first)
        before = self.log.read_bytes()
        correction = session("correction")
        correction["supersedes"] = first["id"]
        correction["exercises"][0]["sets"][1]["reps"] = 6
        coach.append_event(self.log, correction)
        self.assertTrue(self.log.read_bytes().startswith(before))
        active = coach.active_events(coach.read_events(self.log))
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["exercises"][0]["sets"][1]["reps"], 6)
        self.assertEqual(volume(coach.read_events(self.log))["working_sets"], 1)

    def test_duplicate_is_rejected_without_changing_file(self):
        coach.append_event(self.log, session())
        before = self.log.read_bytes()
        with self.assertRaises(ValueError):
            coach.append_event(self.log, session())
        self.assertEqual(self.log.read_bytes(), before)

    def test_stale_correction_is_rejected(self):
        first = session()
        second = session("second")
        second["supersedes"] = first["id"]
        third = session("third")
        third["supersedes"] = first["id"]
        with self.assertRaises(ValueError):
            coach.active_events([first, second, third])

    def test_inactive_lock_file_is_preserved_and_does_not_block(self):
        lock = Path(str(self.log) + ".lock")
        lock.write_text("someone else")
        coach.append_event(self.log, session())
        self.assertEqual(lock.read_text(), "someone else")

    def test_active_lock_blocks_another_writer(self):
        with coach.exclusive_lock(self.log):
            with self.assertRaises(ValueError):
                coach.append_event(self.log, session())
        self.assertFalse(self.log.exists())

    def test_process_death_releases_the_lock(self):
        code = (
            "import importlib.util,sys;from pathlib import Path;"
            "s=importlib.util.spec_from_file_location('c',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "ctx=m.exclusive_lock(Path(sys.argv[2]));ctx.__enter__();"
            "print('locked',flush=True);sys.stdin.read()"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(SKILL / "scripts/coach_tools.py"), str(self.log)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with self.assertRaises(ValueError):
                coach.append_event(self.log, session())
            child.kill()
            child.communicate(timeout=5)
            coach.append_event(self.log, session())
            self.assertEqual(len(coach.read_events(self.log)), 1)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

    def test_unicode_line_separator_survives_roundtrip_and_future_append(self):
        event = session()
        event["source"]["note"] = "User text\u2028with separator\u0085and another"
        coach.append_event(self.log, event)
        coach.append_event(self.log, session("next"))
        self.assertEqual(coach.read_events(self.log)[0]["source"]["note"], event["source"]["note"])

    def test_symlink_alias_uses_same_lock(self):
        coach.append_event(self.log, session())
        alias = self.log.parent / "alias.jsonl"
        try:
            alias.symlink_to(self.log)
        except OSError:
            self.skipTest("symlink creation unavailable on this host")
        with coach.exclusive_lock(self.log):
            with self.assertRaises(ValueError):
                coach.append_event(alias, session("next"))
        self.assertEqual(len(coach.read_events(self.log)), 1)

    def test_hardlink_alias_is_rejected_without_corrupting_journal(self):
        coach.append_event(self.log, session())
        alias = self.log.parent / "hardlink.jsonl"
        try:
            os.link(self.log, alias)
        except OSError:
            self.skipTest("hardlink creation unavailable on this host")
        before = self.log.read_bytes()
        with self.assertRaises(ValueError):
            coach.append_event(alias, session("next"))
        self.assertEqual(self.log.read_bytes(), before)

    def test_synthetic_correction_cannot_remove_real_work(self):
        original = session()
        correction = session("example-correction")
        correction["source"]["kind"] = "synthetic_example"
        correction["supersedes"] = original["id"]
        with self.assertRaises(ValueError):
            coach.active_events([original, correction])

    def test_missing_log_is_not_reported_as_a_successful_read(self):
        with self.assertRaises(ValueError):
            coach.read_events(self.log)

    def test_malformed_missing_newline_is_preserved_and_rejected(self):
        self.log.write_text(json.dumps(session()))
        before = self.log.read_bytes()
        with self.assertRaises(ValueError):
            coach.append_event(self.log, session("next"))
        self.assertEqual(self.log.read_bytes(), before)

    def test_execution_can_reference_an_older_plan_version(self):
        first = session("plan-v1")
        first.update(type="plan", status="planned")
        second = copy.deepcopy(first)
        second.update(id="plan-v2", supersedes="plan-v1")
        performed = session("performed")
        performed["fulfills_plan_id"] = "plan-v1"
        active = coach.active_events([first, second, performed])
        self.assertEqual({e["id"] for e in active}, {"plan-v2", "performed"})


class RepositoryContracts(unittest.TestCase):
    def test_source_identifiers_and_required_metadata(self):
        result = coach.audit_registry(SKILL / "references/sources.json")
        self.assertGreaterEqual(result["sources"], 20)

    def test_real_seed_is_valid_and_does_not_invent_november_weeks(self):
        path = ROOT / "coach-data/danya/events.jsonl"
        records = coach.read_events(path)
        report = coach.weekly_volume(records, "2026-11-01", "2026-11-20")
        self.assertEqual(report["coverage"], "unknown")
        self.assertNotIn("bench-november-80x5", report["records_used"])
        self.assertNotIn("plan-bench-sunday-80-main", report["records_used"])

    def test_public_package_excludes_private_context(self):
        public_skill = ROOT / "science-based-coach"
        self.assertFalse((public_skill / "references/user-context.json").exists())

    def test_skill_links_resolve(self):
        import re
        skill = SKILL / "SKILL.md"
        for target in re.findall(r"\]\(([^)]+)\)", skill.read_text(encoding="utf-8")):
            if not target.startswith("https://"):
                self.assertTrue((SKILL / target).exists(), target)


if __name__ == "__main__":
    unittest.main()
