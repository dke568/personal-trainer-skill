"""Derived views and independent restoration contracts; all mutation is temporary."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SKILL=Path(os.environ.get('COACH_SKILL_ROOT',ROOT/'science-based-coach'))
sys.path.insert(0,str(SKILL/'scripts'))
import fork_diary as diary
import state_summary as summary
import diary_backup as backup
from test_fork_diary import snapshot,change,files,P

class StateAndBackup(unittest.TestCase):
    def state(self):
        s=snapshot();s['files'].update(files(diary.prepare(s,change())))
        return {k[len(P):]:v for k,v in s['files'].items()}
    def bundle(self):return backup.create(self.state(),repository='example/coach',source_commit='a'*40,created_at='2026-11-21')
    def test_summary_is_generated_atomically_and_verifiable(self):
        s=self.state();self.assertEqual(summary.verify(s)['status'],'verified')
        self.assertEqual(json.loads(s['summary.json'])['next_step'],'Read the current plan.')
    def test_changed_primary_state_invalidates_summary(self):
        s=self.state();s['programme.md']+='Different plan'
        with self.assertRaisesRegex(ValueError,'stale'):summary.verify(s)
    def test_real_baseline_never_turns_unknown_date_or_plan_into_latest_completed(self):
        folder=ROOT/'coach-data/danya';f={n:(folder/n).read_text(encoding='utf-8') for n in summary.INPUTS}
        d,md=summary.build(f,as_of='2026-11-21',source_head='a'*40)
        exact={x['id'] for x in d['dated_completed_sessions']}
        self.assertNotIn('bench-november-80x5',exact)
        uncertain={x['id']:x for x in d['sessions_with_uncertain_date_or_completion']}
        self.assertEqual(uncertain['leg-press-november-in-progress']['status'],'in_progress')
        self.assertEqual(len(uncertain['leg-press-november-in-progress']['exercises'][0]['sets']),1)
        self.assertTrue(all(p['performed'] is False for p in d['active_plans_not_performed']))
        self.assertTrue(all(w['is_known_minimum_only'] for w in d['rolling_seven_day_windows']))
        self.assertIn('не полный объём',md)
        bench=[g for g in d['movement_views'] if g['exercise_id']=='bench_barbell_flat'][0]
        self.assertFalse(bench['comparability_verified'])
    def test_summary_does_not_change_old_measurement_date(self):
        folder=ROOT/'coach-data/danya';f={n:(folder/n).read_text(encoding='utf-8') for n in summary.INPUTS}
        before=copy.deepcopy(f);d,_=summary.build(f,as_of='2026-11-21',source_head='a'*40)
        self.assertEqual(f,before)
        self.assertEqual(next(x for x in d['measurements'] if x['id']=='body-2026-10-22')['date'],'2026-10-22')
    def test_restore_preserves_every_text_byte_in_new_directory(self):
        b=self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'restored';receipt=backup.restore(b,target)
            self.assertEqual(receipt['status'],'restored_to_new_directory')
            self.assertEqual({n:(target/n).read_bytes() for n in b['files']},
                             {n:t.encode('utf-8') for n,t in b['files'].items()})
    def test_restore_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'keep';marker.write_text('untouched')
            with self.assertRaisesRegex(ValueError,'already exists'):backup.restore(self.bundle(),tmp)
            self.assertEqual(marker.read_text(),'untouched')
    def test_corruption_is_rejected_before_creating_destination(self):
        b=self.bundle();b['files']['events.jsonl']='corrupted'
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'restore'
            with self.assertRaisesRegex(ValueError,'checksum'):backup.restore(b,target)
            self.assertFalse(target.exists())
    def test_recomputed_envelope_does_not_hide_inconsistent_diary(self):
        b=self.bundle();b['files']['programme.md']='hidden change'
        b['sha256']['programme.md']=diary.sha256('hidden change')
        b['manifest_sha256']=diary.sha256(diary.canonical({k:v for k,v in b.items() if k!='manifest_sha256'}))
        with self.assertRaisesRegex(ValueError,'state mismatch'):backup.check(b)
    def test_path_escape_and_private_files_are_rejected(self):
        s=self.state();s['../private.txt']='secret'
        with self.assertRaisesRegex(ValueError,'unexpected'):backup.create(s,repository='example/coach',source_commit='a'*40,created_at='2026-11-21')
    def test_backup_requires_matching_repository_and_source_commit(self):
        with self.assertRaisesRegex(ValueError,'repository mismatch'):
            backup.create(self.state(),repository='wrong/repo',source_commit='a'*40,created_at='2026-11-21')
        with self.assertRaisesRegex(ValueError,'source commit'):
            backup.create(self.state(),repository='example/coach',source_commit='unknown',created_at='2026-11-21')
    def test_incomplete_summary_is_not_restorable(self):
        s=self.state();del s['SUMMARY.md']
        with self.assertRaises(ValueError):backup.create(s,repository='example/coach',source_commit='a'*40,created_at='2026-11-21')

    def test_backup_date_is_validated_even_if_envelope_was_rehashed(self):
        b=self.bundle();b['created_at']='not-a-date'
        b['manifest_sha256']=diary.sha256(diary.canonical({k:v for k,v in b.items() if k!='manifest_sha256'}))
        with self.assertRaisesRegex(ValueError,'created_at'):backup.check(b)
    def test_non_object_backup_is_a_controlled_error(self):
        with self.assertRaisesRegex(ValueError,'must be an object'):backup.check([])

    def test_version_one_summary_and_backup_remain_readable(self):
        s=self.state();d=json.loads(s['summary.json']);d.pop('training_mode',None);d['schema_version']=1
        s['summary.json']=json.dumps(d,ensure_ascii=False,indent=2)+'\n'
        self.assertEqual(summary.verify(s)['status'],'verified')

if __name__=='__main__':unittest.main()
