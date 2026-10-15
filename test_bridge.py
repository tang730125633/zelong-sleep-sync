"""Isolated tests: no production credentials, requests, or health records."""
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).parent/'server'))
tmp=tempfile.TemporaryDirectory()
os.environ['HEALTH_DATA_DIR']=tmp.name
import wellbeing
from native_health import to_export
from build_shortcuts import build,validate

def row(kind,value,start,end,unit='',source='Apple Watch'):
    return dict(kind=kind,value=value,start=start+'+08:00',end=end+'+08:00',unit=unit,source=source)

sleep=[
    row('Sleep','In Bed','2026-10-11T22:45:00','2026-10-12T07:15:00'),
    row('Sleep','Asleep','2026-10-11T23:00:00','2026-10-12T07:00:00'),
    row('Sleep','Core','2026-10-11T23:00:00','2026-10-12T02:00:00'),
    row('Sleep','Deep','2026-10-12T02:00:00','2026-10-12T03:00:00'),
    row('Sleep','REM','2026-10-12T03:00:00','2026-10-12T04:00:00'),
    row('Sleep','Awake','2026-10-12T04:00:00','2026-10-12T04:30:00'),
    row('Sleep','Core','2026-10-12T04:30:00','2026-10-12T07:00:00'),
    row('Sleep','Asleep','2026-10-11T22:00:00','2026-10-12T09:00:00',source='Other sleep app'),
]
step=row('Steps','1,200','2026-10-12T10:00:00','2026-10-12T10:10:00','count')
steps=[step,copy.deepcopy(step),row('Steps','9000','2026-10-12T10:00:00','2026-10-12T10:15:00','count','iPhone')]
heart=[row('Heart Rate','65 bpm','2026-10-12T10:00:00','2026-10-12T10:00:00','bpm'),row('Heart Rate','85','2026-10-12T11:00:00','2026-10-12T11:00:00','bpm')]
payload={'data':{'native':{'version':1,'start':'2026-10-10T00:00:00+08:00','end':'2026-10-13T12:00:00+08:00','batches':[sleep,steps,heart,[],[],[]]}}}
exported=to_export(payload)['data']['metrics']
by_name={m['name']:m for m in exported if m['name']!='sleep_analysis'}
assert by_name['step_count']['data'][0]['qty']==1200
assert by_name['heart_rate']['data'][0]['qty']==75
summary=next(m for m in exported if m['name']=='sleep_analysis' and m['data'] and 'totalSleep' in m['data'][0])['data'][0]
assert summary['date']=='2026-10-12' and summary['totalSleep']==7.5
assert summary['core']==5.5 and summary['deep']==1 and summary['rem']==1 and summary['awake']==.5
assert summary['inBed']==8.5 and summary['sources']=='Apple Watch'
unknown=copy.deepcopy(payload)
for sample in unknown['data']['native']['batches'][0]:sample['source']=''
unknown['data']['native']['batches'][0][4]['value']='快速动眼睡眠'
unknown['data']['native']['batches'][0][5]['value']='清醒时间'
unknown_metrics=to_export(unknown)['data']['metrics']
unknown_summary=next(m for m in unknown_metrics if m['name']=='sleep_analysis' and 'totalSleep' in m['data'][0])['data'][0]
assert unknown_summary['totalSleep']==7.5 and '未提供' in unknown_summary['sources']
first=wellbeing.import_sleep(payload);second=wellbeing.import_sleep(payload)
assert first['changed']>0 and second['changed']==0
snapshot=wellbeing.health_snapshot()
assert snapshot['sleep'][0]['totalSleep']==7.5
assert snapshot['sync']['sources'][0]['source']=='iphone-native'
with wellbeing.connect() as db:
    state={'source':'iphone-sleep','interval':900,'status':'ok','checked':time.time()-999999,'success':time.time()-999999}
    db.execute('INSERT INTO health_sync VALUES (?,?)',('iphone-sleep',json.dumps(state)))
assert wellbeing.health_snapshot()['sync']['status']=='ok'
assert wellbeing.sleep_day_detail('2026-10-12')['segments']

bad=[]
for field,value in [('value','NaN'),('unit','kg'),('value','100 kcal'),('source','')]:
    b=copy.deepcopy(payload);b['data']['native']['batches'][1][0][field]=value;bad.append(b)
b=copy.deepcopy(payload);b['data']['native']['batches'][0][2]['value']='unknown stage';bad.append(b)
b=copy.deepcopy(payload);b['data']['native']['batches'][1][1]['value']='1201';bad.append(b)
b=copy.deepcopy(payload);b['data']['native']['start']='2026-10-10T01:00:00+08:00';bad.append(b)
b=copy.deepcopy(payload);b['data']['native']['batches']=[[]];bad.append(b)
b=copy.deepcopy(payload);b['data']['native']['batches'][0].append(row('Sleep','Deep','2026-10-12T00:00:00','2026-10-12T01:00:00'));bad.append(b)
before=[dict(r) for r in wellbeing.connect().execute('SELECT * FROM sleep_days')]
for b in bad:
    try:wellbeing.import_sleep(b)
    except ValueError:pass
    else:raise AssertionError('Invalid native payload was accepted')
assert before==[dict(r) for r in wellbeing.connect().execute('SELECT * FROM sleep_days')]
for days in [2,7]:
    workflow=build(days,'test');validate(workflow)
    filters=[a['WFWorkflowActionParameters']['WFContentItemFilter']['Value']['WFActionParameterFilterTemplates'] for a in workflow['WFWorkflowActions'] if a['WFWorkflowActionIdentifier'].endswith('filter.health.quantity')]
    assert filters[4][0]['Values']['Enumeration']['Value']=='Exercise Time'
    blob=json.dumps(workflow)
    assert 'com.ifunography' not in blob and '/api/health/import' in blob
    for a in workflow['WFWorkflowActions']:
        if a['WFWorkflowActionIdentifier'].endswith('.gettext'):
            assert 'WFTextActionText' in a['WFWorkflowActionParameters']
        if a['WFWorkflowActionIdentifier'].endswith('text.combine'):
            assert a['WFWorkflowActionParameters']['text']['WFSerializationType']=='WFTextTokenAttachment'
            assert 'WFInput' not in a['WFWorkflowActionParameters']
    assert '"CoercionItemClass": "WFHKSampleContentItem"' in blob
sleep_workflow=build(2,'sleep-test',['Sleep'])
assert len(sleep_workflow['WFWorkflowActions'])==18
assert not any(a['WFWorkflowActionIdentifier'].endswith('properties.health.quantity') for a in sleep_workflow['WFWorkflowActions'])
print('PASS: sleep union/stages, midnight attribution, single-source quantities, units, idempotency, native sync, bad-input rollback and shortcut structure')
tmp.cleanup()
