"""Remote-commit contracts: no publication or network calls are performed by these tests."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
SKILL=Path(os.environ.get('COACH_SKILL_ROOT',ROOT/'science-based-coach'))
sys.path.insert(0,str(SKILL/'scripts'))
import fork_diary as diary
P='coach-data/danya/'
HEAD='1'*40
TREE='2'*40
COMMIT='3'*40

def snapshot():
    config={'provider':'github','repository_full_name':'example/coach','branch':'main','athlete_id':'danya'}
    return {'schema_version':1,'head':HEAD,'tree':TREE,'config':config,'bootstrap_storage':True,'files':{
        P+'profile.json':json.dumps({'athlete_id':'danya','as_of':'2026-11-20'}),
        P+'programme.md':'Old plan\n',P+'events.jsonl':'',P+'decisions.md':'Old decisions\n',P+'handoff.md':'Old handoff\n'}}

def change():
    return {'schema_version':1,'id':'change-1','athlete_id':'danya','publication_scope':'training_only',
        'kind':'context_update','status':'confirmed','recorded_at':'2026-11-21',
        'source':{'kind':'user_report','note':'User explicitly requested a diary.'},
        'summary':'Save corrections immediately.','reason':'User preference.','next_step':'Read the current plan.','open_questions':[]}

def files(bundle):return {x['path']:x['content'] for x in bundle['files']}

def receipt(bundle):
    commit={'sha':COMMIT,'url':'https://api.github.com/repos/example/coach/git/commits/'+COMMIT,'tree':{'sha':'4'*40},'parents':[{'sha':HEAD}]}
    expected=dict(bundle['expected_state_blobs']);expected.update({f['path']:f['expected_blob_sha'] for f in bundle['files']})
    tree={'sha':'4'*40,'url':'https://api.github.com/repos/example/coach/git/trees/'+'4'*40,'truncated':False,'tree':[{'path':path,'type':'blob','mode':'100644','sha':blob} for path,blob in expected.items()]}
    head={'ref':'refs/heads/main','url':'https://api.github.com/repos/example/coach/git/refs/heads/main','object':{'sha':COMMIT,'type':'commit'}}
    return commit,tree,head

class ForkDiary(unittest.TestCase):
    def test_prepare_does_not_mutate_snapshot_or_report_saved(self):
        s=snapshot(); before=copy.deepcopy(s);b=diary.prepare(s,change())
        self.assertEqual(s,before);self.assertEqual(b['status'],'prepared_not_saved')
        self.assertEqual(b['expected_head'],HEAD)
    def test_checkpoint_handoff_and_decision_share_change_id(self):
        b=diary.prepare(snapshot(),change());f=files(b)
        self.assertEqual(json.loads(f[P+'checkpoint.json'])['last_change_id'],'change-1')
        self.assertIn('change-1',f[P+'handoff.md']);self.assertIn('change-1',f[P+'decisions.md'])
        self.assertEqual(f[P+'CURRENT.md'],f[P+'handoff.md'])
    def test_same_request_after_remote_read_does_not_duplicate(self):
        s=snapshot();c=change();b=diary.prepare(s,c);s['files'].update(files(b));s['head']=COMMIT
        duplicate=diary.prepare(s,c)
        self.assertEqual(duplicate['status'],'already_recorded_in_snapshot');self.assertEqual(duplicate['files'],[])
    def test_reusing_change_id_with_changed_payload_is_rejected(self):
        s=snapshot();c=change();s['files'].update(files(diary.prepare(s,c)));c['summary']='Different fact'
        with self.assertRaisesRegex(ValueError,'reused'):diary.prepare(s,c)
    def test_later_entry_preserves_chain_and_old_bytes(self):
        s=snapshot();c=change();first=files(diary.prepare(s,c));s['files'].update(first)
        c['id']='change-2';second=files(diary.prepare(s,c))
        self.assertTrue(second[P+'changes.jsonl'].startswith(first[P+'changes.jsonl']))
        self.assertEqual(len(diary.validate_ledger(second[P+'changes.jsonl'])),2)
        self.assertTrue(second[P+'decisions.md'].startswith(first[P+'decisions.md']))
    def test_changed_historical_ledger_is_rejected(self):
        s=snapshot();s['files'].update(files(diary.prepare(s,change())))
        s['files'][P+'changes.jsonl']=s['files'][P+'changes.jsonl'].replace('Save corrections','Hide corrections')
        c=change();c['id']='change-2'
        with self.assertRaisesRegex(ValueError,'historical'):diary.prepare(s,c)
    def test_malformed_jsonl_is_not_repaired_silently(self):
        s=snapshot();s['files'][P+'events.jsonl']='{}'
        with self.assertRaisesRegex(ValueError,'end with LF'):diary.prepare(s,change())
    def test_stale_state_replacement_is_rejected(self):
        c=change();c['updates']={'profile.json':{'before_sha256':'bad','content':'{}'}}
        with self.assertRaisesRegex(ValueError,'stale update'):diary.prepare(snapshot(),c)
    def test_profile_changes_require_user_confirmed_facts(self):
        s=snapshot();c=change();c['status']='proposed';c['source']['kind']='assistant_proposal'
        c['updates']={'profile.json':{'before_sha256':diary.sha256(s['files'][P+'profile.json']),'content':s['files'][P+'profile.json']}}
        with self.assertRaisesRegex(ValueError,'factual profile'):diary.prepare(s,c)
    def test_programme_change_without_plan_event_is_rejected(self):
        s=snapshot();c=change();c['kind']='plan_adjustment'
        c['updates']={'programme.md':{'before_sha256':diary.sha256(s['files'][P+'programme.md']),'content':'Future plan'}}
        with self.assertRaisesRegex(ValueError,'planned event'):diary.prepare(s,c)
    def test_arbitrary_paths_cannot_be_written(self):
        c=change();c['updates']={'../private.json':{'content':'private','before_sha256':None}}
        with self.assertRaisesRegex(ValueError,'unsupported state'):diary.prepare(snapshot(),c)
    def test_snapshot_path_traversal_is_rejected(self):
        s=snapshot();s['files'][P+'../private.json']='secret'
        with self.assertRaisesRegex(ValueError,'out-of-scope'):diary.prepare(s,change())
    def test_factual_correction_requires_provenance_event(self):
        c=change();c['kind']='fact_correction'
        with self.assertRaisesRegex(ValueError,'provenance event'):diary.prepare(snapshot(),c)
    def test_unscoped_publication_is_rejected(self):
        c=change();c.pop('publication_scope')
        with self.assertRaisesRegex(ValueError,'training-only'):diary.prepare(snapshot(),c)
    def test_remote_readback_verifies_every_blob(self):
        b=diary.prepare(snapshot(),change());r=diary.verify(b,*receipt(b))
        self.assertEqual(r['status'],'verified_saved');self.assertEqual(r['verified_files'],len(b['files']))
    def test_partial_tree_or_missing_blob_never_reports_saved(self):
        b=diary.prepare(snapshot(),change());commit,tree,head=receipt(b)
        tree['tree'].pop()
        with self.assertRaisesRegex(ValueError,'read-back mismatch'):diary.verify(b,commit,tree,head)
        tree['truncated']=True
        with self.assertRaisesRegex(ValueError,'incomplete tree'):diary.verify(b,commit,tree,head)
    def test_concurrent_parent_or_head_change_never_reports_saved(self):
        b=diary.prepare(snapshot(),change());commit,tree,head=receipt(b)
        head['object']['sha']='f'*40
        with self.assertRaisesRegex(ValueError,'head changed'):diary.verify(b,commit,tree,head)
        commit['parents']=[{'sha':'e'*40}]
        with self.assertRaisesRegex(ValueError,'unexpected parent'):diary.verify(b,commit,tree,head)
    def test_unicode_and_all_macro_bytes_roundtrip(self):
        c=change();c['summary']='Текст\u2028и ещё текст'
        b=diary.prepare(snapshot(),c);ledger=diary.validate_ledger(files(b)[P+'changes.jsonl'])
        self.assertEqual(ledger[0]['summary'],c['summary'])

    def test_retry_checks_state_before_acknowledging_id(self):
        s=snapshot();c=change();s['files'].update(files(diary.prepare(s,c)));s['files'][P+'decisions.md']='Lost history'
        with self.assertRaisesRegex(ValueError,'integrity mismatch'):diary.prepare(s,c)
    def test_incomplete_tree_identity_cannot_confirm_save(self):
        b=diary.prepare(snapshot(),change());commit,tree,head=receipt(b)
        del tree['sha'];del commit['tree']['sha']
        with self.assertRaisesRegex(ValueError,'missing tree SHA'):diary.verify(b,commit,tree,head)
    def test_wrong_repository_branch_and_symlink_cannot_confirm_save(self):
        b=diary.prepare(snapshot(),change());commit,tree,head=receipt(b)
        head['ref']='refs/heads/other'
        with self.assertRaisesRegex(ValueError,'repository/branch'):diary.verify(b,commit,tree,head)
        head=receipt(b)[2];tree['tree'][0]['mode']='120000'
        with self.assertRaisesRegex(ValueError,'read-back mismatch'):diary.verify(b,commit,tree,head)
    def test_a_fact_correction_is_not_an_extra_workout(self):
        c=change();c['kind']='fact_correction';c['events']=[{'schema_version':1,'id':'note','type':'note','status':'completed','date':'2026-11-21','date_precision':'day','recorded_at':'2026-11-21','source':c['source']}]
        with self.assertRaisesRegex(ValueError,'must supersede'):diary.prepare(snapshot(),c)
    def test_assistant_cannot_smuggle_a_user_fact_inside_a_proposal(self):
        c=change();c.update(status='proposed',source={'kind':'assistant_proposal','note':'Unconfirmed'})
        c['events']=[{'schema_version':1,'id':'fact','type':'measurement','status':'completed','date':'2026-11-21','date_precision':'day','recorded_at':'2026-11-21','source':{'kind':'user_report','note':'Invented'},'measurements':[{'metric':'weight','value':90,'unit':'kg'}]}]
        with self.assertRaisesRegex(ValueError,'performed facts'):diary.prepare(snapshot(),c)
    def test_invalid_ref_names_are_rejected(self):
        s=snapshot();s['config']['branch']='bad name.lock';s['files'][P+'storage.json']=json.dumps(s['config'])
        with self.assertRaisesRegex(ValueError,'invalid branch'):diary.prepare(s,change())

    def test_proposed_user_change_cannot_rewrite_facts(self):
        c=change();c['status']='proposed';c['events']=[{'schema_version':1,'id':'fact','type':'measurement','status':'completed','date':'2026-11-21','date_precision':'day','recorded_at':'2026-11-21','source':{'kind':'user_report','note':'Unconfirmed'},'measurements':[{'metric':'weight','value':90,'unit':'kg'}]}]
        with self.assertRaisesRegex(ValueError,'performed facts'):diary.prepare(snapshot(),c)
    def test_recalled_assistant_measurement_is_not_a_fact(self):
        c=change();c['events']=[{'schema_version':1,'id':'fact','type':'measurement','status':'recalled','date':None,'date_precision':'unknown','recorded_at':'2026-11-21','source':{'kind':'assistant_proposal','note':'Assumed'},'measurements':[{'metric':'weight','value':90,'unit':'kg'}]}]
        with self.assertRaisesRegex(ValueError,'factual event'):diary.prepare(snapshot(),c)
    def test_malformed_event_is_a_controlled_error(self):
        c=change();c['kind']='fact_correction';c['events']=[None]
        with self.assertRaisesRegex(ValueError,'list of objects'):diary.prepare(snapshot(),c)
    def test_removing_unchanged_profile_cannot_confirm_save(self):
        b=diary.prepare(snapshot(),change());commit,tree,head=receipt(b)
        tree['tree']=[x for x in tree['tree'] if x['path']!=P+'profile.json']
        with self.assertRaisesRegex(ValueError,'read-back mismatch'):diary.verify(b,commit,tree,head)
    def test_initial_storage_requires_explicit_bootstrap(self):
        s=snapshot();s.pop('bootstrap_storage')
        with self.assertRaisesRegex(ValueError,'bootstrap required'):diary.prepare(s,change())
        s['bootstrap_storage']=True;b=diary.prepare(s,change())
        self.assertEqual(json.loads(files(b)[P+'storage.json']),s['config'])

    def test_initialized_diary_cannot_lose_its_ledger_or_checkpoint(self):
        s=snapshot();s['files'].update(files(diary.prepare(s,change())))
        for name in ['changes.jsonl','checkpoint.json','CURRENT.md']:
            with self.subTest(missing=name):
                damaged=copy.deepcopy(s);del damaged['files'][P+name]
                c=change();c['id']='change-2'
                with self.assertRaisesRegex(ValueError,'incomplete initialized'):
                    diary.prepare(damaged,c)

    def test_empty_initialized_ledger_never_resets_history(self):
        s=snapshot();s['files'].update(files(diary.prepare(s,change())))
        s['files'][P+'changes.jsonl']=''
        with self.assertRaisesRegex(ValueError,'empty initialized'):
            diary.prepare(s,change())

    def test_losing_all_checkpoint_files_is_not_initialization(self):
        s=snapshot();s['files'].update(files(diary.prepare(s,change())))
        for name in ['changes.jsonl','checkpoint.json','CURRENT.md']:del s['files'][P+name]
        with self.assertRaisesRegex(ValueError,'missing initialized ledger'):
            diary.prepare(s,change())

    def test_integrity_map_cannot_omit_primary_state(self):
        s=snapshot();s['files'].update(files(diary.prepare(s,change())))
        entries=diary.jsonl(s['files'][P+'changes.jsonl'])
        entry=entries[-1];entry['state_hashes'].pop(P+'profile.json')
        entry.pop('entry_hash');entry['entry_hash']=diary.sha256(diary.canonical(entry))
        s['files'][P+'changes.jsonl']=diary.append_lines('',entries)
        with self.assertRaisesRegex(ValueError,'incomplete state integrity'):
            diary.prepare(s,change())

    def test_omitted_questions_preserve_unresolved_context(self):
        s=snapshot();c=change();c['open_questions']=['Unknown session date']
        s['files'].update(files(diary.prepare(s,c)))
        c=change();c['id']='change-2';del c['open_questions']
        out=files(diary.prepare(s,c))
        self.assertEqual(json.loads(out[P+'checkpoint.json'])['open_questions'],['Unknown session date'])
        self.assertIn('Unknown session date',out[P+'CURRENT.md'])

    def test_explicit_question_resolution_is_allowed(self):
        s=snapshot();c=change();c['open_questions']=['Unknown session date']
        s['files'].update(files(diary.prepare(s,c)))
        c=change();c['id']='change-2';c['reason']='User clarified the session date.';c['resolved_questions']=[{'question':'Unknown session date','reason':'User supplied the exact date.'}]
        out=files(diary.prepare(s,c))
        self.assertEqual(json.loads(out[P+'checkpoint.json'])['open_questions'],[])

    def test_clearing_questions_without_resolution_is_rejected(self):
        s=snapshot();c=change();c['open_questions']=['Unknown session date']
        s['files'].update(files(diary.prepare(s,c)))
        c=change();c['id']='change-2'
        with self.assertRaisesRegex(ValueError,'explicit resolutions'):
            diary.prepare(s,c)

if __name__=='__main__':unittest.main()
