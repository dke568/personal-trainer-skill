#!/usr/bin/env python3
"""Portable training-data backup and fail-closed restore to a NEW directory. No network."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
from coach_tools import active_events, require
from fork_diary import validate_ledger, canonical, sha256, valid_sha
from state_summary import verify as verify_summary

REQUIRED = {'profile.json','programme.md','events.jsonl','decisions.md','handoff.md',
            'storage.json','changes.jsonl','checkpoint.json','CURRENT.md'}
ALLOWED = REQUIRED | {'summary.json','SUMMARY.md'}

def validate_state(files):
    require(REQUIRED <= set(files) <= ALLOWED, 'missing or unexpected backup files')
    require(all(isinstance(v,str) for v in files.values()), 'backup data must be text')
    profile=json.loads(files['profile.json']);config=json.loads(files['storage.json'])
    require(profile['athlete_id']==config['athlete_id'], 'athlete mismatch')
    ledger=validate_ledger(files['changes.jsonl']);require(ledger,'empty ledger')
    active_events([json.loads(line) for line in files['events.jsonl'].split('\n') if line.strip()])
    prefix='coach-data/'+config['athlete_id']+'/'
    hashes=ledger[-1].get('state_hashes',{})
    require({prefix+n for n in REQUIRED-{'changes.jsonl'}} <= set(hashes),'incomplete integrity map')
    for path,expected in hashes.items():
        require(path.startswith(prefix),'out of scope state hash')
        name=path[len(prefix):]
        require(name in ALLOWED and name in files and sha256(files[name])==expected,'state mismatch: '+name)
    checkpoint=json.loads(files['checkpoint.json'])
    require(checkpoint['last_change_id']==ledger[-1]['id'],'checkpoint/change mismatch')
    if 'summary.json' in files or 'SUMMARY.md' in files:
        require({'summary.json','SUMMARY.md'}<=set(files),'incomplete summary')
        verify_summary(files)
    return config

def create(files, *, repository, source_commit, created_at):
    config=validate_state(files)
    require(valid_sha(source_commit),'invalid source commit')
    require(repository==config['repository_full_name'],'repository mismatch')
    from coach_tools import iso_day
    iso_day(created_at,'created_at')
    result={'schema_version':1,'kind':'training_diary_backup','created_at':created_at,
            'repository_full_name':repository,'source_commit':source_commit,
            'athlete_id':config['athlete_id'],'files':files,
            'sha256':{name:sha256(text) for name,text in sorted(files.items())},
            'limits':'Checksums detect corruption, not authenticity. Store separately from the fork.'}
    result['manifest_sha256']=sha256(canonical(result))
    return result

def check(bundle):
    require(isinstance(bundle,dict), 'backup must be an object')
    from coach_tools import iso_day
    iso_day(bundle.get('created_at'),'created_at')
    require(bundle.get('schema_version')==1 and bundle.get('kind')=='training_diary_backup','invalid backup')
    unsigned={k:v for k,v in bundle.items() if k!='manifest_sha256'}
    require(sha256(canonical(unsigned))==bundle.get('manifest_sha256'),'manifest checksum mismatch')
    files=bundle['files']
    require(set(bundle['sha256'])==set(files),'checksum coverage mismatch')
    for name,text in files.items():
        require(name in ALLOWED and sha256(text)==bundle['sha256'][name],'file checksum mismatch')
    config=validate_state(files)
    require(config['athlete_id']==bundle['athlete_id'] and
            config['repository_full_name']==bundle['repository_full_name'] and
            valid_sha(bundle.get('source_commit')),'backup provenance mismatch')
    return files

def restore(bundle, destination):
    files=check(bundle);destination=Path(destination)
    require(not destination.exists(),'restore destination already exists; never overwrite live state')
    require(destination.parent.is_dir(),'restore parent must exist')
    # Stage under the same parent, then rename; no partial target on a write failure.
    temporary=Path(tempfile.mkdtemp(prefix='.coach-restore-',dir=destination.parent))
    try:
        for name,text in files.items():
            with (temporary/name).open('w',encoding='utf-8',newline='\n') as handle:handle.write(text)
        loaded={n:(temporary/n).read_text(encoding='utf-8') for n in files}
        require(loaded==files,'restore byte mismatch')
        validate_state(loaded)
        temporary.rename(destination)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return {'status':'restored_to_new_directory','files':len(files),'source_commit':bundle['source_commit']}

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('create');a.add_argument('--directory',required=True);a.add_argument('--repository',required=True)
    a.add_argument('--source-commit',required=True);a.add_argument('--created-at',required=True);a.add_argument('--out',required=True)
    a=sub.add_parser('check');a.add_argument('--backup',required=True)
    a=sub.add_parser('restore');a.add_argument('--backup',required=True);a.add_argument('--destination',required=True)
    a=p.parse_args()
    try:
        if a.command=='create':
            folder=Path(a.directory)
            files={name:(folder/name).read_text(encoding='utf-8') for name in ALLOWED if (folder/name).is_file()}
            b=create(files,repository=a.repository,source_commit=a.source_commit,created_at=a.created_at)
            with Path(a.out).open('x',encoding='utf-8',newline='\n') as f:json.dump(b,f,ensure_ascii=False,indent=2);f.write('\n')
            print(json.dumps({'status':'created_locally_not_uploaded','files':len(files),'manifest_sha256':b['manifest_sha256']}))
        else:
            b=json.loads(Path(a.backup).read_text(encoding='utf-8'))
            print(json.dumps(restore(b,a.destination) if a.command=='restore' else {'status':'verified','files':len(check(b))}))
    except (ValueError,KeyError,TypeError,OSError) as exc:p.exit(2,str(exc)+'\n')
if __name__=='__main__':main()
