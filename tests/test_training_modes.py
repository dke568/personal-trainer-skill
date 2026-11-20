import copy,json,os
from pathlib import Path
import sys,unittest
ROOT=Path(__file__).resolve().parents[1]
SKILL=Path(os.environ.get('COACH_SKILL_ROOT',ROOT/'science-based-coach'))
sys.path.insert(0,str(SKILL/'scripts'))
import training_modes as modes
import fork_diary as diary
from test_fork_diary import snapshot,change,files,P

class TrainingModes(unittest.TestCase):
    def test_strength_and_hypertrophy_have_different_outcomes_and_monitoring(self):
        a=modes.guidance('strength');b=modes.guidance('hypertrophy')
        self.assertNotEqual(a['primary_outcome'],b['primary_outcome'])
        self.assertNotEqual(a['monitor'],b['monitor'])
        self.assertIn('PL-METD-2021',a['evidence_ids'])
        self.assertEqual(a['status'],'guidance_only_not_selected_or_saved')
    def test_hybrid_requires_priority_and_does_not_stack_programs(self):
        with self.assertRaises(ValueError):modes.guidance('hybrid')
        a=modes.guidance('hybrid','hypertrophy');b=modes.guidance('hybrid','strength')
        self.assertNotEqual(a['primary_outcome'],b['primary_outcome'])
        self.assertTrue(a['existing_weekly_volume_must_be_reviewed'])
    def test_invalid_mode_or_priority_is_rejected(self):
        for mode,priority in [('bodybuilding_magic',None),('strength','hypertrophy'),('hybrid','both')]:
            with self.subTest(mode=mode,priority=priority):
                with self.assertRaises(ValueError):modes.guidance(mode,priority)
    def test_mode_selection_is_stored_atomically_without_a_completed_workout(self):
        s=snapshot();profile=json.loads(s['files'][P+'profile.json'])
        profile['training_mode']={'mode':'hybrid','priority':'hypertrophy','source':'user_report','status':'confirmed','selected_at':'2026-11-21'}
        c=change();c['kind']='preference_update'
        c['updates']={'profile.json':{'before_sha256':diary.sha256(s['files'][P+'profile.json']),'content':json.dumps(profile)}}
        b=diary.prepare(s,c);out=files(b)
        self.assertEqual(json.loads(out[P+'summary.json'])['training_mode']['priority'],'hypertrophy')
        self.assertNotIn(P+'events.jsonl',out)
        self.assertEqual(s['files'][P+'events.jsonl'],'')
    def test_unconfirmed_mode_cannot_be_saved_as_selected_profile(self):
        s=snapshot();profile=json.loads(s['files'][P+'profile.json'])
        profile['training_mode']={'mode':'strength','priority':None,'source':'assistant_proposal','status':'proposed','selected_at':'2026-11-21'}
        c=change();c['updates']={'profile.json':{'before_sha256':diary.sha256(s['files'][P+'profile.json']),'content':json.dumps(profile)}}
        with self.assertRaisesRegex(ValueError,'user confirmation'):diary.prepare(s,c)
    def test_coverage_maps_every_source_and_separates_survey_evidence(self):
        refs=SKILL/'references';d=json.loads((refs/'evidence-coverage.json').read_text())
        rows={r['source_id']:r for r in d['records']}
        self.assertEqual(len(rows),60)
        self.assertEqual(rows['BB-NATURAL-2026']['design'],'cross_sectional_survey')
        self.assertEqual(rows['PL-CESSATION-2023']['population_relevance'],'powerlifter_participants')
    def test_future_mode_selection_is_rejected(self):
        s=snapshot();profile=json.loads(s['files'][P+'profile.json'])
        profile['training_mode']={'mode':'strength','priority':None,'source':'user_report','status':'confirmed','selected_at':'2027-01-01'}
        c=change();c['updates']={'profile.json':{'before_sha256':diary.sha256(s['files'][P+'profile.json']),'content':json.dumps(profile)}}
        with self.assertRaisesRegex(ValueError,'in the future'):diary.prepare(s,c)

if __name__=='__main__':unittest.main()
