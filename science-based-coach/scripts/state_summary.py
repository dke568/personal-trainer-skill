#!/usr/bin/env python3
"""Derived training view. Never infers missing dates, completion or comparable technique."""
import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from coach_tools import active_events, iso_day, weekly_volume

INPUTS = ('profile.json', 'events.jsonl', 'programme.md', 'checkpoint.json')

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def build(files, *, as_of, source_head):
    day = iso_day(as_of, 'as_of')
    profile = json.loads(files['profile.json'])
    checkpoint = json.loads(files['checkpoint.json'])
    events = [json.loads(line) for line in files['events.jsonl'].split('\n') if line.strip()]
    active = [e for e in active_events(events) if e['source']['kind'] != 'synthetic_example']
    exact, uncertain, plans, measurements = [], [], [], []
    def stamp(e):
        return {k: e.get(k) for k in ('id', 'type', 'status', 'date', 'date_precision', 'period', 'source')}
    for e in active:
        row = stamp(e)
        if e['type'] == 'session':
            row['exercises'] = e['exercises']
            row['comparability'] = 'Same exercise/load basis is insufficient: verify machine, ROM, technique, rest, RIR and equipment.'
            if e['status'] == 'completed' and e['date_precision'] == 'day':
                exact.append(row)
            else:
                uncertain.append(row)
        elif e['type'] == 'plan':
            row['exercises'] = e['exercises'];row['performed'] = False
            plans.append(row)
        elif e['type'] == 'measurement':
            row['measurements'] = e['measurements'];measurements.append(row)
    exact.sort(key=lambda e: (e['date'], e['id']))
    measurements.sort(key=lambda e: (e['date'] or '', e['id']))
    movement_views = {}
    for row in exact + uncertain:
        for ex in row['exercises']:
            key = json.dumps([ex['exercise_id'], ex['load_basis'], ex.get('equipment_id')], ensure_ascii=False)
            group = movement_views.setdefault(key, {'exercise_id': ex['exercise_id'],
                'load_basis': ex['load_basis'], 'equipment_id': ex.get('equipment_id'),
                'dated_completed': [], 'uncertain': [], 'comparability_verified': False})
            target = 'dated_completed' if row in exact else 'uncertain'
            group[target].append({'event_id': row['id'], 'date': row['date'],
                'date_precision': row['date_precision'], 'period': row.get('period'),
                'status': row['status'], 'sets': ex['sets']})
    for group in movement_views.values():group['dated_completed'] = group['dated_completed'][-3:]
    periods = []
    for offset in (7, 0):
        end = day - timedelta(days=offset); start = end - timedelta(days=6)
        periods.append(weekly_volume(events, start.isoformat(), end.isoformat()))
    result = {'schema_version': 2, 'athlete_id': profile['athlete_id'], 'as_of': as_of,
              'source_parent_head': source_head,
              'input_sha256': {name: digest(files[name]) for name in INPUTS},
              'training_mode': profile.get('training_mode'),
              'not_a_new_observation': True, 'movement_views': list(movement_views.values()),
              'dated_completed_sessions': exact[-6:],
              'sessions_with_uncertain_date_or_completion': uncertain,
              'active_plans_not_performed': plans, 'measurements': measurements,
              'rolling_seven_day_windows': periods,
              'open_questions': checkpoint.get('open_questions', []),
              'next_step': checkpoint['next_step'],
              'limits': ['Unknown date is not chronological evidence.',
                         'Incomplete coverage: zero recorded sets does not mean zero training.',
                         'A derived summary never replaces primary events or updates old measurement dates.']}
    lines = ['# Сводка для продолжения', '', 'Сформирована: '+as_of+'. Это дата сводки, а не новых замеров.',
             'Источник: [события](events.jsonl), [план](programme.md), [профиль](profile.json).', '',
             '## Подтверждённые с точной датой (последние шесть)', '']
    if result['training_mode']:
        mode=result['training_mode'];lines[4:4]=['Режим: '+mode['mode']+'; приоритет: '+str(mode.get('priority'))+'.', '']
    def session(e):
        label = e['date'] or e.get('period') or 'дата неизвестна'
        lines.append('- '+label+' · '+e['id']+' · '+e['status']+' / '+e['date_precision'])
        for ex in e['exercises']:
            sets = [s for s in ex['sets'] if s['kind']=='working']
            text = ', '.join(str(s['load_kg'])+'×'+str(s.get('reps', '?')) for s in sets)
            lines.append('  - '+ex.get('name', ex['exercise_id'])+': '+text+'; '+ex['load_basis'])
    for e in result['dated_completed_sessions']:session(e)
    if not exact: lines.append('Нет подтверждённых записей с точной датой; это не отсутствие тренировок.')
    lines.extend(['', '## Отдельно: дата или выполнение не подтверждены полностью', ''])
    for e in uncertain:session(e)
    lines.extend(['', '## Недельный учёт', ''])
    for w in periods:
        lines.append('- '+w['start']+' — '+w['end']+': зарегистрировано рабочих подходов '+str(w['working_sets'])+
                     '. Покрытие неизвестно; это нижняя граница учёта, не полный объём.')
    lines.extend(['', '## Планы', '', 'Все предложения ниже НЕ являются выполненной работой.'])
    lines.extend('- '+p['id'] for p in plans)
    lines.extend(['', '## Нерешённые вопросы', ''])
    lines.extend('- '+q for q in result['open_questions'])
    lines.extend(['', '## Следующий шаг', '', result['next_step'], '',
                  'Перед сравнением проверить амплитуду, тренажёр, запас, паузы и экипировку.',
                  'Числа с неопределённой датой не упорядочивать по дате записи.',
                  'Полная машинная сводка: [summary.json](summary.json).', ''])
    return result, '\n'.join(lines)

def verify(files):
    saved = json.loads(files['summary.json'])
    rebuilt, markdown = build(files, as_of=saved['as_of'], source_head=saved['source_parent_head'])
    if saved.get('schema_version') == 1:
        rebuilt.pop('training_mode', None)
        rebuilt['schema_version'] = 1
    if rebuilt != saved or markdown != files['SUMMARY.md']:
        raise ValueError('stale or altered derived summary; regenerate with the primary state')
    return {'status': 'verified', 'as_of': saved['as_of'], 'not_a_new_observation': True}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['build','verify'])
    p.add_argument('--directory',required=True);p.add_argument('--as-of');p.add_argument('--source-head')
    a=p.parse_args();folder=Path(a.directory)
    files={f.name:f.read_text(encoding='utf-8') for f in folder.iterdir() if f.is_file()}
    if a.command=='verify':print(json.dumps(verify(files)));return
    data,text=build(files,as_of=a.as_of,source_head=a.source_head)
    (folder/'summary.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (folder/'SUMMARY.md').write_text(text,encoding='utf-8')
    print(json.dumps({'status':'derived_locally_not_published','athlete_id':data['athlete_id']}))
if __name__=='__main__':main()
