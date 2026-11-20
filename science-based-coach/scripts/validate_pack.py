#!/usr/bin/env python3
"""Check referential integrity of the coaching pack, not scientific validity."""
import argparse
import json
from pathlib import Path
import re
from coach_tools import audit_registry

def validate(skill, scenarios=None):
    skill=Path(skill)
    def read(name):return json.loads((skill/'references'/name).read_text(encoding='utf-8'))
    def check(ok,message):
        if not ok:raise ValueError(message)
    sources=read('sources.json')['sources'];ids={s['id'] for s in sources}
    audit_registry(skill/'references/sources.json')
    check(len(sources)>=50,'incomplete core source coverage')
    check(len({s['doi'].lower() for s in sources})==len(sources),'duplicate DOI')
    app=read('critical-appraisals.json')['appraisals'];check(len(app)>=15,'incomplete appraisals')
    check(len({a['id'] for a in app})==len(app),'duplicate appraisal')
    source_map={s['id']:s for s in sources}
    for a in app:
        check(a['id'] in ids,'unknown appraisal source')
        check(source_map[a['id']]['access']=='full_text_sections','appraisal exceeds recorded access')
        check(a['review_scope']==source_map[a['id']]['review_scope'],'appraisal scope mismatch')
        for key in ['question','methods_reviewed','main_result','risk_and_measurement_limits','funding_and_conflicts','application','unresolved','working_confidence']:
            check(isinstance(a.get(key),str) and bool(a[key].strip()),'missing appraisal '+key)
    cards=read('decision-cards.json')['cards'];check(len(cards)>=30,'incomplete cards')
    check(len({c['id'] for c in cards})==len(cards),'duplicate card')
    for c in cards:
        check(set(c['evidence_ids'])<=ids,'unknown card citation')
        for key in ['when','check_before','action','review_or_rollback','basis','limits','persistence']:
            check(bool(c.get(key)),'missing card '+key)
    topics=read('topics.json')['topics'];check(len(topics)>=12,'incomplete topic routes')
    for t in topics:
        check(set(t['evidence_ids'])<=ids,'unknown topic citation')
        p=skill/'references'/t['reference'];check(p.is_file(),'missing topic reference '+str(p))
    edu=read('education-sources.json')['sources'];check(len(edu)>=3,'incomplete educational coverage')
    check(not(ids & {e['id'] for e in edu}),'education counted as research')
    for file in [skill/'SKILL.md',*(skill/'references').glob('*.md')]:
        for link in re.findall(r'\]\(([^)]+)\)',file.read_text(encoding='utf-8')):
            if not link.startswith(('https://','http://','#')):
                check((file.parent/link.split('#')[0]).is_file(),'broken link '+str(file)+': '+link)
    cases=0
    if scenarios:
        data=json.loads(Path(scenarios).read_text(encoding='utf-8'))['cases'];cases=len(data)
        check(cases>=25 and len({x['id'] for x in data})==cases,'incomplete or duplicate scenarios')
        check(all(x.get('synthetic') is True and x.get('criteria') and x.get('prompt') for x in data),'invalid scenario')
    return {'sources':len(sources),'critical_appraisals':len(app),'decision_cards':len(cards),'topics':len(topics),'educational_outlines':len(edu),'scenario_definitions':cases,'check':'structure and references only; not scientific or behavioral efficacy validation'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--skill',default=str(Path(__file__).resolve().parents[1]));p.add_argument('--scenarios');a=p.parse_args()
    try:print(json.dumps(validate(a.skill,a.scenarios),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError,TypeError) as exc:p.exit(2,str(exc)+'\n')
