#!/usr/bin/env python3
"""Return goal-specific coaching rules. Does not change an athlete or publish a diary."""
import argparse
import json
from pathlib import Path

def guidance(mode, priority=None):
    if mode not in {'hypertrophy','strength','hybrid'}:raise ValueError('unknown training mode')
    if mode=='hybrid' and priority not in {'hypertrophy','strength'}:
        raise ValueError('hybrid mode requires an explicit priority')
    if mode!='hybrid' and priority is not None:
        raise ValueError('priority is only accepted for hybrid mode')
    config=json.loads((Path(__file__).resolve().parents[1]/'references/training-modes.json').read_text(encoding='utf-8'))
    result=dict(next(m for m in config['modes'] if m['id']==mode))
    result.update(priority=priority,status='guidance_only_not_selected_or_saved',
                  existing_weekly_volume_must_be_reviewed=True)
    if mode=='hybrid':result['primary_outcome']=next(m['primary_outcome'] for m in config['modes'] if m['id']==priority)
    return result

def validate_selection(selection):
    if not isinstance(selection,dict):raise ValueError('training_mode must be an object')
    guidance(selection.get('mode'),selection.get('priority'))
    if selection.get('source')!='user_report' or selection.get('status')!='confirmed':
        raise ValueError('a selected mode requires explicit user confirmation')
    from coach_tools import iso_day
    iso_day(selection.get('selected_at'),'selected_at')
    return selection

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',required=True,choices=['hypertrophy','strength','hybrid'])
    p.add_argument('--priority',choices=['hypertrophy','strength']);a=p.parse_args()
    try:print(json.dumps(guidance(a.mode,a.priority),ensure_ascii=False,indent=2))
    except ValueError as e:p.exit(2,str(e)+'\n')
if __name__=='__main__':main()
