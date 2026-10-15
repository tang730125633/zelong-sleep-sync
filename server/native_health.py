"""Translate read-only iPhone Shortcuts samples into the existing upload format."""
import datetime as dt
import math
import re
import unicodedata
from collections import Counter, defaultdict
from health_metrics import LOCAL_TZ, timestamp

UNKNOWN_SLEEP_SOURCE = 'Apple 健康（设备来源未提供）'

KINDS = {
    'Steps': ('step_count', 'count', 'sum'),
    'Heart Rate': ('heart_rate', 'count/min', 'mean'),
    'Active Calories': ('active_energy', 'kcal', 'sum'),
    'Exercise Minutes': ('apple_exercise_time', 'min', 'sum'),
    'Walking + Running Distance': ('walking_running_distance', 'km', 'sum'),
}
UNITS = {
    'count': ('count', 1), '步': ('count', 1), '计数': ('count', 1),
    'count/min': ('count/min', 1), 'bpm': ('count/min', 1), '次/分': ('count/min', 1), '次/分钟': ('count/min', 1),
    'kcal': ('kcal', 1), '千卡': ('kcal', 1), 'kj': ('kcal', 1 / 4.184),
    'min': ('min', 1), '分钟': ('min', 1), 's': ('min', 1 / 60), '秒': ('min', 1 / 60), 'hr': ('min', 60),
    'km': ('km', 1), '公里': ('km', 1), '千米': ('km', 1), 'm': ('km', .001), '米': ('km', .001), 'mi': ('km', 1.609344),
}
STAGES = {
    '0': 'inBed', '1': 'asleep', '2': 'awake', '3': 'core', '4': 'deep', '5': 'rem',
    'in bed': 'inBed', '卧床': 'inBed', '卧床时间': 'inBed',
    'asleep': 'asleep', 'asleep (unspecified)': 'asleep', 'unspecified': 'asleep', '睡眠': 'asleep', '睡着': 'asleep',
    'awake': 'awake', '清醒': 'awake', '清醒时间': 'awake', 'core': 'core', 'asleep (core)': 'core', '核心': 'core', '核心睡眠': 'core', '睡眠(核心)': 'core',
    'deep': 'deep', 'asleep (deep)': 'deep', '深度': 'deep', '深度睡眠': 'deep', '睡眠(深度)': 'deep',
    'rem': 'rem', 'asleep (rem)': 'rem', '快速动眼期': 'rem', '快速眼动': 'rem', '快速动眼睡眠': 'rem', '快速眼动睡眠': 'rem', 'rem睡眠': 'rem', '睡眠(rem)': 'rem',
}

def clean(value, limit=300, field='文字'):
    if not isinstance(value, str) or not value or len(value) > limit:
        size=len(value) if isinstance(value,(str,list,dict)) else 0
        raise ValueError(f'健康样本 {field} 字段无效（类型 {type(value).__name__}，长度 {size}）')
    return unicodedata.normalize('NFKC', value).strip()

def numeric(value, unit, canonical):
    unit = clean(unit).lower()
    if unit not in UNITS or UNITS[unit][0] != canonical:
        raise ValueError('健康单位不匹配，请检查快捷指令的单位字段')
    if isinstance(value, str):
        value = unicodedata.normalize('NFKC', value).strip()
        match = re.fullmatch(r'([0-9,]+(?:\.[0-9]+)?)\s*([^0-9.,]*)', value)
        if match and match[2]:
            suffix = match[2].strip().lower()
            if suffix not in UNITS or UNITS[suffix] != UNITS[unit]:
                raise ValueError('健康数值携带的单位与单位字段不一致')
            value = match[1]
        if re.fullmatch(r'\d{1,3}(,\d{3})+(\.\d+)?', value):
            value = value.replace(',', '')
        if not re.fullmatch(r'\d+(\.\d+)?', value):
            raise ValueError('健康数值不是有效数字')
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1e7:
        raise ValueError('健康数值超出传输范围')
    return value * UNITS[unit][1]

def iso(value):
    return dt.datetime.fromtimestamp(value, LOCAL_TZ).isoformat()

def source_rank(item):
    source, rows = item
    watch = 'watch' in source.lower() or '手表' in source
    return watch, sum(end - start for start, end, _ in rows), len(rows), source

def sleep_summary(day, source, rows):
    events = defaultdict(Counter)
    for start, end, stage in rows:
        events[start][stage] += 1
        events[end][stage] -= 1
    times = sorted(events)
    active, seconds = Counter(), Counter()
    asleep_bounds, bed_bounds = [], []
    for start, end in zip(times, times[1:]):
        active.update(events[start])
        if active['inBed'] > 0:
            seconds['inBed'] += end - start
            bed_bounds.extend([start, end])
        specific = [stage for stage in ('awake', 'core', 'deep', 'rem') if active[stage] > 0]
        if len(specific) > 1:
            raise ValueError('同一来源存在冲突的睡眠分期，请核对原始记录')
        stage = specific[0] if specific else 'asleep' if active['asleep'] > 0 else None
        if stage:
            seconds[stage] += end - start
            if stage != 'awake':
                asleep_bounds.extend([start, end])
    if not asleep_bounds:
        return None
    result = {'date': day, 'totalSleep': sum(seconds[s] for s in ('core', 'deep', 'rem', 'asleep')) / 3600,
              'sleepStart': iso(min(asleep_bounds)), 'sleepEnd': iso(max(asleep_bounds)), 'sources': source}
    present = {stage for _, _, stage in rows}
    result.update({stage: seconds[stage] / 3600 for stage in present})
    if bed_bounds:
        result.update(inBedStart=iso(min(bed_bounds)), inBedEnd=iso(max(bed_bounds)))
    return result

def to_export(body):
    native = body.get('data', {}).get('native')
    if not isinstance(native, dict) or native.get('version') != 1:
        raise ValueError('原生健康数据版本无效')
    start, end = timestamp(native.get('start')), timestamp(native.get('end'))
    local_start = dt.datetime.fromtimestamp(start, LOCAL_TZ)
    if local_start.time() != dt.time() or not 0 < end - start <= 9 * 86400:
        raise ValueError('请同步从当天零点开始、最多9天的数据')
    batches = native.get('batches')
    if not isinstance(batches, list) or not 0 < len(batches) <= 6 or not all(isinstance(batch, list) for batch in batches):
        raise ValueError('原生健康批次格式错误')
    records = [row for batch in batches for row in batch]
    if not 0 < len(records) <= 30000:
        raise ValueError('未读取到健康样本，或一次样本过多')
    quantities, sleeps, seen = defaultdict(lambda: defaultdict(set)), defaultdict(lambda: defaultdict(set)), {}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('健康样本格式错误')
        kind = clean(row.get('kind'),field='kind')
        source = UNKNOWN_SLEEP_SOURCE if kind=='Sleep' and row.get('source') in (None,'') else clean(row.get('source'),field='source')
        lo, hi = timestamp(row.get('start')), timestamp(row.get('end'))
        if not lo <= hi <= end + 60 or hi < start or hi - lo > 48 * 3600:
            raise ValueError('健康样本不在导出时间范围内')
        if kind == 'Sleep':
            stage = STAGES.get(unicodedata.normalize('NFKC', str(row.get('value', ''))).strip().lower())
            if stage is None or hi <= lo:
                raise ValueError('无法识别睡眠分期，请保留原始分期值')
            cursor = max(lo, start)
            while cursor < hi:
                day = (dt.datetime.fromtimestamp(cursor, LOCAL_TZ) + dt.timedelta(hours=12)).date()
                boundary = dt.datetime.combine(day, dt.time(12), LOCAL_TZ).timestamp()
                stop = min(hi, boundary)
                # The first night can start before the selected export window.
                if day > local_start.date():
                    sleeps[day.isoformat()][source].add((cursor, stop, stage))
                cursor = stop
        elif kind in KINDS:
            name, unit, _ = KINDS[kind]
            value = numeric(row.get('value'), row.get('unit'), unit)
            identity = (kind, source, lo, hi)
            if identity in seen:
                if seen[identity] != value:
                    raise ValueError('同一来源同一时段存在不同数值，请检查原始样本')
                continue
            seen[identity] = value
            if lo < start:
                continue  # Do not overwrite a day using only part of a quantity sample.
            day = dt.datetime.fromtimestamp(lo, LOCAL_TZ).date().isoformat()
            quantities[(kind, day)][source].add((lo, hi, value))
        else:
            raise ValueError('该健康样本类型尚未支持')
    metrics = defaultdict(list)
    for (kind, day), sources in quantities.items():
        # Select one device source, rather than adding overlapping phone/watch totals.
        source, rows = max(sources.items(), key=source_rank)
        name, unit, mode = KINDS[kind]
        values = [value for _, _, value in rows]
        raw = {'date': day, 'qty': sum(values) if mode == 'sum' else sum(values) / len(values), 'source': source}
        if name == 'heart_rate':
            raw.update(Min=min(values), Max=max(values))
        metrics[(name, unit)].append(raw)
    summaries, segments = [], []
    labels = {'awake':'Awake','core':'Core','deep':'Deep','rem':'REM','asleep':'Asleep','inBed':'In Bed'}
    for day, sources in sorted(sleeps.items()):
        usable = [(source, rows) for source, rows in sources.items() if any(s in ('core','deep','rem','asleep') for _,_,s in rows)]
        if not usable:
            continue
        source, rows = max(usable, key=lambda item: (any(s in ('core','deep','rem') for _,_,s in item[1]), *source_rank(item)))
        if source == UNKNOWN_SLEEP_SOURCE and any(stage in ('core','deep','rem') for _,_,stage in rows):
            # Without source identity, generic sleep spans cannot safely extend staged sleep.
            rows={row for row in rows if row[2]!='asleep'}
        summary = sleep_summary(day, source, rows)
        if summary:
            summaries.append(summary)
            segments.extend({'startDate':iso(lo),'endDate':iso(hi),'value':labels[stage],'source':source} for lo,hi,stage in sorted(rows))
    exported = [{'name':name,'units':unit,'data':sorted(rows,key=lambda r:r['date'])} for (name,unit),rows in metrics.items()]
    if summaries:
        exported.extend([{'name':'sleep_analysis','units':'hr','data':summaries}, {'name':'sleep_analysis','units':'hr','data':segments}])
    if not exported:
        raise ValueError('没有可同步的完整记录，请检查健康读取权限与日期范围')
    return {'sync_source':'iphone-native','sync_interval':86400,'data':{'metrics':exported}}
