"""Archive completeness is separate from the human assessment of response quality."""
import hashlib
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]

class EvaluationArchive(unittest.TestCase):
    def test_new_runs_retain_full_prompts_answers_criteria_and_pinned_version(self):
        p=ROOT/'tests/evaluations/2026-11-21/responses-and-ratings.json'
        d=json.loads(p.read_text(encoding='utf-8'))
        self.assertTrue(d['synthetic']);self.assertEqual(len(d['answers']),10)
        self.assertEqual(len({r['id'] for r in d['answers']}),10)
        for r in d['answers']:
            self.assertGreater(len(r['prompt']),30);self.assertGreater(len(r['response']),100)
            self.assertTrue(r['actions_taken'])
        self.assertEqual({r['id'] for r in d['answers']},{r['id'] for r in d['assessment']})
        for r in d['assessment']:self.assertTrue(r['criteria']);self.assertTrue(r['note'])
        v=d['skill_version'];self.assertEqual(len(v['skill_commit']),40);self.assertEqual(len(v['skill_tree']),40)
        self.assertTrue(v['file_sha256'])
    def test_tested_behavioral_materials_match_recorded_version(self):
        d=json.loads((ROOT/'tests/evaluations/2026-11-21/responses-and-ratings.json').read_text(encoding='utf-8'))
        archived=json.loads((ROOT/'tests/evaluations/2026-11-21/materials.json').read_text(encoding='utf-8'))
        # Historical materials stay pinned while the current coach can evolve.
        for name,text in archived['files'].items():
            self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(),
                             d['skill_version']['file_sha256'][name],name)

    def test_mode_evaluation_retains_eight_full_answers_and_historical_materials(self):
        folder=ROOT/'tests/evaluations/2026-11-21-modes'
        d=json.loads((folder/'responses-and-ratings.json').read_text(encoding='utf-8'))
        m=json.loads((folder/'materials.json').read_text(encoding='utf-8'))
        self.assertEqual(len(d['answers']),8)
        self.assertEqual({r['id'] for r in d['answers']},{r['id'] for r in d['assessment']})
        for r in d['answers']:self.assertTrue(r['prompt']);self.assertGreater(len(r['response']),100)
        for name,text in m['files'].items():self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(),m['sha256'][name])

if __name__=='__main__':unittest.main()
