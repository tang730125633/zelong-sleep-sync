"""Build secret-free, native iPhone Health shortcuts; use /usr/bin/python3."""
import json
import plistlib
import uuid
from pathlib import Path

ROOT = Path(__file__).parent
ENDPOINT = 'https://health.example.com/api/health/import'
TYPES = ['Sleep', 'Steps', 'Heart Rate', 'Active Calories', 'Exercise Minutes', 'Walking + Running Distance']

def uid():
    return str(uuid.uuid4()).upper()

def output(identity, name='Result'):
    return {'Type':'ActionOutput','OutputUUID':identity,'OutputName':name}

def attachment(value):
    return {'Value':value,'WFSerializationType':'WFTextTokenAttachment'}

def text(*parts):
    value, refs = '', {}
    for part in parts:
        if isinstance(part, dict):
            refs['{%d, 1}' % (len(value.encode('utf-16-le'))//2)] = part
            value += '\ufffc'
        else:
            value += str(part)
    return {'Value':{'string':value,**({'attachmentsByRange':refs} if refs else {})},'WFSerializationType':'WFTextTokenString'}

def fields(values):
    return {'Value':{'WFDictionaryFieldValueItems':[
        {'WFItemType':0,'WFKey':text(k),'WFValue':v if isinstance(v,dict) else text(v)} for k,v in values.items()
    ]},'WFSerializationType':'WFDictionaryFieldValue'}

def build(days, name, kinds=None, endpoint=ENDPOINT):
    from urllib.parse import urlsplit
    url = urlsplit(endpoint)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use an HTTPS endpoint without credentials, query, or fragment')
    kinds = TYPES if kinds is None else kinds
    assert kinds and len(set(kinds))==len(kinds) and set(kinds)<=set(TYPES)
    actions=[]
    def add(kind, **parameters):
        parameters.setdefault('UUID',uid())
        actions.append({'WFWorkflowActionIdentifier':'is.workflow.actions.'+kind,'WFWorkflowActionParameters':parameters})
        return output(parameters['UUID'])
    def formatted(value):
        return add('format.date',WFDate=text(value),WFDateFormatStyle='ISO 8601',WFISO8601IncludeTime=True)
    add('comment',WFCommentActionText='原生健康同步：只读取健康记录，直接发送到自己配置的 HTTPS 服务，不调用第三方健康 App\n首次使用请在 iPhone 授权读取，运行时保持解锁\n睡眠、步数、心率、活动能量、锻炼分钟、步行跑步距离\n训练明细尚未包含，原有记录保留')
    token=add('gettext',WFTextActionText=text('请在导入时填写健康同步密钥'))
    now=add('date',WFDateActionMode='Current Date')
    earlier=add('adjustdate',WFDate=text(now),WFAdjustOperation='Subtract',WFDuration={'Value':{'Magnitude':days,'Unit':'days'},'WFSerializationType':'WFQuantityFieldValue'})
    start=add('adjustdate',WFDate=text(earlier),WFAdjustOperation='Get Start of Day')
    start_iso,end_iso=formatted(start),formatted(now)
    batches=[]
    for kind in kinds:
        picker_kind = 'Exercise Time' if kind == 'Exercise Minutes' else kind
        predicates=[
            {'Bounded':True,'Operator':4,'Property':'Type','Removable':False,'Values':{'Enumeration':{'Value':picker_kind,'WFSerializationType':'WFStringSubstitutableState'}}},
            {'Bounded':True,'Operator':1003,'Property':'Start Date','Removable':True,'Values':{'Date':attachment(start),'AnotherDate':attachment(now)}}
        ]
        found=add('filter.health.quantity',WFContentItemFilter={'Value':{'WFActionParameterFilterPrefix':1,'WFContentPredicateBoundedDate':False,'WFActionParameterFilterTemplates':predicates},'WFSerializationType':'WFContentPredicateTableTemplate'},WFContentItemLimitEnabled=False)
        group=uid()
        add('repeat.each',WFInput=attachment(found),GroupingIdentifier=group,WFControlFlowMode=0)
        item={'Type':'Variable','VariableName':'Repeat Item'}
        values={prop:{**item,'Aggrandizements':[{'Type':'WFCoercionVariableAggrandizement','CoercionItemClass':'WFHKSampleContentItem'},{'Type':'WFPropertyVariableAggrandizement','PropertyName':prop}]} for prop in ['Value','Unit','Start Date','End Date','Source']}
        lo,hi=formatted(values['Start Date']),formatted(values['End Date'])
        record=add('dictionary',WFItems=fields({'kind':kind,'value':text(values['Value']),'unit':text(values['Unit']),'start':text(lo),'end':text(hi),'source':text(values['Source'])}))
        add('gettext',WFTextActionText=text(record))
        results=add('repeat.each',GroupingIdentifier=group,WFControlFlowMode=2)
        joined=add('text.combine',text=attachment(results),WFTextSeparator='Custom',WFTextCustomSeparator=',')
        batches.append(joined)
    parts=['{"sync_source":"iphone-native","sync_interval":86400,"data":{"native":{"version":1,"start":"',start_iso,'","end":"',end_iso,'","batches":[']
    for i,batch in enumerate(batches):
        parts.extend([',' if i else '', '[',batch,']'])
    parts.append(']}}}')
    payload=add('gettext',WFTextActionText=text(*parts))
    response=add('downloadurl',WFURL=text(endpoint),WFHTTPMethod='POST',WFHTTPBodyType='File',WFRequestVariable=attachment(payload),WFFormValues=fields({}),WFHTTPHeaders=fields({'Authorization':text('Bearer ',token),'Content-Type':'application/json'}))
    add('showresult',Text=text('服务器返回\n',response,'\n\n出现 imported 与 changed 代表接收成功\n如果显示 error，请保留提示，稍后重试'))
    workflow={'WFWorkflowName':name,'WFWorkflowActions':actions,'WFWorkflowClientVersion':'2600.0.0','WFWorkflowMinimumClientVersion':900,'WFWorkflowMinimumClientVersionString':'900','WFWorkflowIcon':{'WFWorkflowIconGlyphNumber':59836,'WFWorkflowIconStartColor':431817727},'WFWorkflowTypes':[],'WFWorkflowInputContentItemClasses':[],'WFWorkflowOutputContentItemClasses':[],'WFWorkflowHasShortcutInputVariables':False,'WFWorkflowHasOutputFallback':False,'WFWorkflowIsDisabledOnLockScreen':True,'WFWorkflowImportQuestions':[{'ActionIndex':1,'Category':'Parameter','ParameterKey':'WFTextActionText','Text':'粘贴你自己服务器的 upload.key，仅发送到配置的同步接口','DefaultValue':''}]}
    validate(workflow)
    return workflow

def validate(workflow):
    actions=workflow['WFWorkflowActions']
    assert all(a['WFWorkflowActionIdentifier'].startswith('is.workflow.actions.') for a in actions)
    assert not any('health.quantity.log' in a['WFWorkflowActionIdentifier'] for a in actions)
    ids={a['WFWorkflowActionParameters']['UUID'] for a in actions}
    assert len(ids)==len(actions)
    def walk(value):
        if isinstance(value,dict):
            if 'OutputUUID' in value:assert value['OutputUUID'] in ids
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(workflow)
    assert 1<=len([a for a in actions if a['WFWorkflowActionIdentifier'].endswith('filter.health.quantity')])<=6
    for action in actions:
        if action['WFWorkflowActionIdentifier'].endswith('filter.health.quantity'):
            dates=action['WFWorkflowActionParameters']['WFContentItemFilter']['Value']['WFActionParameterFilterTemplates'][1:]
            assert len(dates)==1 and dates[0]['Operator']==1003
            assert set(dates[0]['Values'])=={'Date','AnotherDate'}
    for action in actions:
        params=action['WFWorkflowActionParameters']
        if action['WFWorkflowActionIdentifier'].endswith(('adjustdate','format.date')):
            assert params['WFDate']['WFSerializationType']=='WFTextTokenString'
        if action['WFWorkflowActionIdentifier'].endswith('format.date'):
            assert params['WFDateFormatStyle']=='ISO 8601' and params['WFISO8601IncludeTime']
    assert workflow['WFWorkflowImportQuestions'][0]['ActionIndex']==1

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Build a credential-free iPhone sleep shortcut')
    parser.add_argument('--endpoint', required=True, help='Your HTTPS /api/health/import URL')
    args = parser.parse_args()
    dest = ROOT / 'dist'
    dest.mkdir(exist_ok=True)
    name = '睡眠同步'
    workflow = build(2, name, ['Sleep'], endpoint=args.endpoint)
    workflow['WFWorkflowActions'][0]['WFWorkflowActionParameters']['WFCommentActionText'] = '只读取近两天睡眠并发送到配置的私人服务器，不修改苹果健康记录；首次运行需授权'
    (dest / (name + '.unsigned.shortcut')).write_bytes(plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY, sort_keys=False))
    (dest / (name + '.json')).write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
    print('Built sleep-only shortcut with no embedded credentials in dist/')
