"""两队分头校对的可合并副本：共同底稿、逐段合并、冲突裁决与幂等结案。"""
import copy as _deepcopy
import math
import time
from fastapi import HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from src.models import Dataset, MergeSession, ProofCopy, ProofEdit, MergeConflict
from src.services.business import metadata, analyze
from src.services.audit import record
from src.utils.serialization import checksum

SIDES = ('left', 'right')
SIDE_LABELS = {'left': '第一队', 'right': '第二队'}
COLUMNS = metadata()['columns']
FIELDS = [c['key'] for c in COLUMNS]
FIELD_LABELS = {c['key']: c['label'] for c in COLUMNS}
VALUE_FIELDS = [f for f in FIELDS if f != 'segment_id']
KIND_LABELS = {
    'same_field_edit': '同项异改',
    'delete_edit': '删改相撞',
    'same_id_add': '同号新增异文',
}


# ---------- 校验与规范化 ----------

def _label(key):
    return FIELD_LABELS.get(key, key)


def canonical_value(field, value):
    if field == 'segment_id':
        if value is None or isinstance(value, bool):
            raise ValueError('管段编号不能为空')
        text = str(value).strip()
        if not text or len(text) > 80:
            raise ValueError('管段编号须为 1~80 个字符')
        return text
    if isinstance(value, bool) or value is None or value == '':
        raise ValueError(f'{_label(field)}必须为有限数值')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{_label(field)}必须为有限数值')
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{_label(field)}须为非负有限数值')
    if field == 'confirmed_leak':
        if number not in (0.0, 1.0):
            raise ValueError('复核漏点只接受 0 或 1')
        return int(number)
    return number


def canonical_record(record, allow_extra=False):
    """整条规范化；缺字段、未知字段、数值不合法都拒绝。"""
    if not isinstance(record, dict):
        raise ValueError('管段记录必须是对象')
    unknown = set(record) - set(FIELDS)
    if unknown and not allow_extra:
        raise ValueError('包含未知字段：' + '、'.join(sorted(map(str, unknown))))
    sid_raw = record.get('segment_id')
    if sid_raw is None or sid_raw == '':
        raise ValueError('缺少字段 管段编号')
    row = {'segment_id': canonical_value('segment_id', sid_raw)}
    for field in VALUE_FIELDS:
        if record.get(field) is None or record.get(field) == '':
            raise ValueError(f'缺少字段 {_label(field)}')
        row[field] = canonical_value(field, record[field])
    return row


def _record_map(records):
    return {row['segment_id']: row for row in records}


def _same(a, b):
    return a == b


# ---------- 读取 ----------

def _session(db, session_id, lock=False):
    query = select(MergeSession).where(MergeSession.id == session_id)
    if lock:
        query = query.with_for_update()
    session = db.scalar(query)
    if not session:
        raise HTTPException(404, '合校会话不存在')
    return session


def _copy(db, session, side, lock=False):
    if side not in SIDES:
        raise HTTPException(404, '校对副本不存在')
    query = select(ProofCopy).where(ProofCopy.session_id == session.id, ProofCopy.side == side)
    if lock:
        query = query.with_for_update()
    copy = db.scalar(query)
    if not copy:
        raise HTTPException(500, '校对副本缺失')
    return copy


def _copies(db, session):
    return _copy(db, session, 'left'), _copy(db, session, 'right')


def _ensure_open(session):
    if session.status == 'closed':
        raise HTTPException(409, '该合校已结案，不能再修改；原批次、试验与简报仍可查看')


# ---------- 创建 ----------

def create(db, data, user):
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, '合校名称去除空格后至少需要两个字符')
    if len(name) > 120:
        raise HTTPException(422, '合校名称过长，最多 120 个字符')
    if db.scalar(select(MergeSession).where(MergeSession.name == name)):
        raise HTTPException(409, '该合校名称已存在，请修改名称')
    dataset = db.get(Dataset, data.base_dataset_id)
    if not dataset:
        raise HTTPException(404, '共同底稿批次不存在')
    base = [canonical_record(row, allow_extra=True) for row in dataset.records]
    ids = [row['segment_id'] for row in base]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, '共同底稿存在重复管段编号，不能作为合校底稿')
    now = time.time()
    session = MergeSession(
        name=name, base_dataset_id=dataset.id, base_checksum=dataset.checksum,
        base_records=base, status='open', created_by=user.id, created_at=now,
    )
    db.add(session)
    db.flush()
    labels = [data.left_label.strip() or SIDE_LABELS['left'], data.right_label.strip() or SIDE_LABELS['right']]
    for side, label in zip(SIDES, labels):
        db.add(ProofCopy(
            session_id=session.id, side=side, label=label,
            records=_deepcopy.deepcopy(base), row_revs={row['segment_id']: 0 for row in base},
            version=0, confirmed_version=None, created_at=now, updated_at=now,
        ))
    record(db, user.email, 'merge.create', {'id': session.id, 'base': dataset.id, 'rows': len(base)})
    db.flush()
    return session


# ---------- 副本编辑 ----------

def _rev(row_revs, sid):
    return int((row_revs or {}).get(sid, 0))


def _bump(copy, sid, now):
    """版本已由 _claim_version 原子推进；这里只更新该段修订号与确认状态。"""
    revs = dict(copy.row_revs or {})
    revs[sid] = _rev(revs, sid) + 1
    copy.row_revs = revs
    copy.confirmed_version = None
    copy.confirmed_at = None
    copy.confirmed_by = None
    copy.updated_at = now
    return revs[sid]


def _log(db, copy, sid, field, action, old, new, user, rev):
    db.add(ProofEdit(
        copy_id=copy.id, segment_id=sid, field=field, action=action,
        old_value=old, new_value=new, actor=user.email, row_rev=rev,
        copy_version=copy.version, at=time.time(),
    ))


def _claim_version(db, copy, expected, now):
    """以版本号为条件做一次原子占用：并发下只有一方能从 v 推进到 v+1。"""
    if not isinstance(expected, int):
        raise HTTPException(422, '缺少副本版本号')
    bumped = db.execute(
        update(ProofCopy)
        .where(ProofCopy.id == copy.id, ProofCopy.version == expected)
        .values(version=ProofCopy.version + 1, updated_at=now,
                confirmed_version=None, confirmed_at=None, confirmed_by=None)
    ).rowcount
    if bumped != 1:
        db.rollback()
        fresh = db.get(ProofCopy, copy.id)
        current = fresh.version if fresh else '未知'
        raise HTTPException(409, f'副本已更新到版本 {current}，请刷新后重试')
    db.expire(copy)
    db.refresh(copy)
    return copy


def update_segment(db, session, side, segment_id, expected_version, values, user):
    session = _session(db, session.id, lock=True)
    _ensure_open(session)
    copy = _copy(db, session, side, lock=True)
    if not isinstance(values, dict) or not values:
        raise HTTPException(422, '没有需要保存的字段改动')
    if 'segment_id' in values:
        raise HTTPException(422, '管段编号不可修改')
    for field, value in values.items():
        if field not in VALUE_FIELDS:
            raise HTTPException(422, f'未知字段 {field}')
        try:
            canonical_value(field, value)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    current = _deepcopy.deepcopy(copy.records)
    index = next((i for i, row in enumerate(current) if row['segment_id'] == segment_id), None)
    if index is None:
        if any(row['segment_id'] == segment_id for row in session.base_records):
            raise HTTPException(409, '该管段已在副本中删除；请先恢复底稿再修改')
        raise HTTPException(404, '副本中不存在该管段')
    old = current[index]
    changed = [f for f in VALUE_FIELDS if f in values and canonical_value(f, values[f]) != old[f]]
    if not changed:
        # 没有实际改动时不推进版本，也不让原有确认失效。
        return session, copy
    _claim_version(db, copy, expected_version, time.time())
    records = _deepcopy.deepcopy(copy.records)
    index = next((i for i, row in enumerate(records) if row['segment_id'] == segment_id), None)
    if index is None:
        db.rollback()
        raise HTTPException(409, '该管段已在副本中删除；请先恢复底稿再修改')
    old = records[index]
    merged = dict(old)
    for field, value in values.items():
        merged[field] = canonical_value(field, value)
    try:
        merged = canonical_record(merged)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    changed = [f for f in VALUE_FIELDS if merged[f] != old[f]]
    if not changed:
        db.rollback()
        return session, copy
    records[index] = merged
    copy.records = records
    rev = _bump(copy, segment_id, time.time())
    for field in changed:
        _log(db, copy, segment_id, field, 'update', old[field], merged[field], user, rev)
    record(db, user.email, 'merge.copy.edit', {
        'session': session.id, 'side': side, 'segment': segment_id, 'fields': changed, 'rev': rev,
    })
    db.commit()
    return session, copy


def delete_segment(db, session, side, segment_id, expected_version, user):
    session = _session(db, session.id, lock=True)
    _ensure_open(session)
    copy = _copy(db, session, side, lock=True)
    if not any(row['segment_id'] == segment_id for row in copy.records):
        raise HTTPException(404, '副本中不存在该管段')
    _claim_version(db, copy, expected_version, time.time())
    records = _deepcopy.deepcopy(copy.records)
    index = next((i for i, row in enumerate(records) if row['segment_id'] == segment_id), None)
    if index is None:
        db.rollback()
        raise HTTPException(404, '副本中不存在该管段')
    old = records.pop(index)
    copy.records = records
    rev = _bump(copy, segment_id, time.time())
    _log(db, copy, segment_id, '*', 'delete', old, None, user, rev)
    record(db, user.email, 'merge.copy.delete', {'session': session.id, 'side': side, 'segment': segment_id, 'rev': rev})
    db.commit()
    return session, copy


def add_segment(db, session, side, expected_version, record_data, user):
    session = _session(db, session.id, lock=True)
    _ensure_open(session)
    copy = _copy(db, session, side, lock=True)
    try:
        row = canonical_record(record_data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    sid = row['segment_id']
    if any(base['segment_id'] == sid for base in session.base_records):
        raise HTTPException(409, '共同底稿已有该管段编号；请编辑或恢复底稿，不能作为新增')
    if any(existing['segment_id'] == sid for existing in copy.records):
        raise HTTPException(409, '副本已有该管段编号')
    _claim_version(db, copy, expected_version, time.time())
    records = _deepcopy.deepcopy(copy.records)
    if any(existing['segment_id'] == sid for existing in records):
        db.rollback()
        raise HTTPException(409, '副本已有该管段编号')
    records.append(row)
    copy.records = records
    rev = _bump(copy, sid, time.time())
    _log(db, copy, sid, '*', 'add', None, row, user, rev)
    record(db, user.email, 'merge.copy.add', {'session': session.id, 'side': side, 'segment': sid, 'rev': rev})
    db.commit()
    return session, copy


def restore_segment(db, session, side, segment_id, expected_version, user):
    session = _session(db, session.id, lock=True)
    _ensure_open(session)
    copy = _copy(db, session, side, lock=True)
    base = next((row for row in session.base_records if row['segment_id'] == segment_id), None)
    if base is None:
        raise HTTPException(404, '底稿不含该管段，无法恢复')
    if any(row['segment_id'] == segment_id for row in copy.records):
        raise HTTPException(409, '副本中仍有该管段，无需恢复')
    _claim_version(db, copy, expected_version, time.time())
    records = _deepcopy.deepcopy(copy.records)
    if any(row['segment_id'] == segment_id for row in records):
        db.rollback()
        raise HTTPException(409, '副本中仍有该管段，无需恢复')
    restored = _deepcopy.deepcopy(base)
    records.append(restored)
    copy.records = records
    rev = _bump(copy, segment_id, time.time())
    _log(db, copy, segment_id, '*', 'restore', None, restored, user, rev)
    record(db, user.email, 'merge.copy.restore', {'session': session.id, 'side': side, 'segment': segment_id, 'rev': rev})
    db.commit()
    return session, copy


def confirm(db, session, side, expected_version, user):
    """确认不推进版本：只锁定当前版本，任何后续改动都会使其失效。"""
    session = _session(db, session.id, lock=True)
    _ensure_open(session)
    copy = _copy(db, session, side, lock=True)
    if not isinstance(expected_version, int):
        raise HTTPException(422, '缺少副本版本号')
    if copy.version != expected_version:
        raise HTTPException(409, f'副本已更新到版本 {copy.version}，请刷新后重试')
    if copy.confirmed_version == copy.version:
        return session, copy
    copy.confirmed_version = copy.version
    copy.confirmed_at = time.time()
    copy.confirmed_by = user.email
    record(db, user.email, 'merge.copy.confirm', {
        'session': session.id, 'side': side, 'version': copy.version,
    })
    db.commit()
    return session, copy


# ---------- 合并计算 ----------

def _diff(base, record):
    return [field for field in VALUE_FIELDS if not _same(base[field], record[field])]


def _specs(session, left, right):
    """计算当前两侧的逐段改动与冲突项。"""
    base_rows = session.base_records
    base_map = _record_map(base_rows)
    left_map = _record_map(left.records)
    right_map = _record_map(right.records)
    specs = []
    for base in base_rows:
        sid = base['segment_id']
        lrec = left_map.get(sid)
        rrec = right_map.get(sid)
        lrev = _rev(left.row_revs, sid)
        rrev = _rev(right.row_revs, sid)
        if lrec is None and rrec is None:
            continue
        if lrec is None or rrec is None:
            present = lrec if lrec is not None else rrec
            if _diff(base, present):
                specs.append({
                    'key': (sid, '*', 'delete_edit'), 'segment_id': sid, 'field': '*',
                    'kind': 'delete_edit', 'base_value': base,
                    'left_value': lrec, 'right_value': rrec,
                    'left_rev': lrev, 'right_rev': rrev,
                })
            continue
        for field in VALUE_FIELDS:
            left_changed = not _same(lrec[field], base[field])
            right_changed = not _same(rrec[field], base[field])
            if left_changed and right_changed and not _same(lrec[field], rrec[field]):
                specs.append({
                    'key': (sid, field, 'same_field_edit'), 'segment_id': sid, 'field': field,
                    'kind': 'same_field_edit', 'base_value': base[field],
                    'left_value': lrec[field], 'right_value': rrec[field],
                    'left_rev': lrev, 'right_rev': rrev,
                })
    added_ids = sorted((set(left_map) - set(base_map)) | (set(right_map) - set(base_map)))
    for sid in added_ids:
        lrec = left_map.get(sid)
        rrec = right_map.get(sid)
        lrev = _rev(left.row_revs, sid)
        rrev = _rev(right.row_revs, sid)
        if lrec is not None and rrec is not None and lrec != rrec:
            specs.append({
                'key': (sid, '*', 'same_id_add'), 'segment_id': sid, 'field': '*',
                'kind': 'same_id_add', 'base_value': None,
                'left_value': lrec, 'right_value': rrec,
                'left_rev': lrev, 'right_rev': rrev,
            })
    return specs


def refresh(db, session):
    """让冲突表跟随来源：来源再改则作废旧裁决并重新核对。"""
    left, right = _copies(db, session)
    specs = _specs(session, left, right)
    rows = {c.id: c for c in db.scalars(select(MergeConflict).where(MergeConflict.session_id == session.id))}
    by_key = {(c.segment_id, c.field, c.kind): c for c in rows.values()}
    now = time.time()
    current = []
    for spec in specs:
        row = by_key.get(spec['key'])
        if row is None:
            row = MergeConflict(
                session_id=session.id, segment_id=spec['segment_id'], field=spec['field'],
                kind=spec['kind'], status='open', history=[], created_at=now, updated_at=now,
            )
            db.add(row)
            db.flush()
        else:
            if row.status == 'decided' and (
                row.decision_left_rev != spec['left_rev'] or row.decision_right_rev != spec['right_rev']
            ):
                history = list(row.history or [])
                history.append({
                    'action': 'invalidated', 'reason': 'source_changed', 'at': now,
                    'previous_decision': row.decision, 'decided_by': row.decided_by,
                    'left_rev': spec['left_rev'], 'right_rev': spec['right_rev'],
                })
                row.history = history
                row.status = 'open'
                row.decision = None
                row.decided_by = None
                row.decided_at = None
                row.decision_left_rev = None
                row.decision_right_rev = None
                row.closed_reason = None
            elif row.status == 'resolved':
                history = list(row.history or [])
                history.append({
                    'action': 'reopened', 'reason': 'sources_diverge_again', 'at': now,
                    'left_rev': spec['left_rev'], 'right_rev': spec['right_rev'],
                })
                row.history = history
                row.status = 'open'
                row.decision = None
                row.decided_by = None
                row.decided_at = None
                row.decision_left_rev = None
                row.decision_right_rev = None
                row.closed_reason = None
        row.base_value = spec['base_value']
        row.left_value = spec['left_value']
        row.right_value = spec['right_value']
        row.left_rev = spec['left_rev']
        row.right_rev = spec['right_rev']
        row.updated_at = now
        current.append(row)
    current_keys = {spec['key'] for spec in specs}
    for key, row in by_key.items():
        if key in current_keys:
            continue
        if row.status in ('open', 'decided'):
            if row.status == 'decided':
                history = list(row.history or [])
                history.append({
                    'action': 'invalidated', 'reason': 'sources_aligned', 'at': now,
                    'previous_decision': row.decision, 'decided_by': row.decided_by,
                })
                row.history = history
                row.decision = None
                row.decided_by = None
                row.decided_at = None
                row.decision_left_rev = None
                row.decision_right_rev = None
            row.status = 'resolved'
            row.closed_reason = 'sources_aligned'
            row.updated_at = now
    db.flush()
    return current, left, right


def _decision(row):
    if row is None or row.status != 'decided':
        return None
    decision = row.decision or {}
    choice = decision.get('choice')
    if choice == 'delete':
        return {'delete': True, 'source': 'decision', 'row': row, 'picked': 'delete'}
    if choice == 'left':
        return {'value': row.left_value, 'source': 'decision', 'row': row, 'picked': 'left'}
    if choice == 'right':
        return {'value': row.right_value, 'source': 'decision', 'row': row, 'picked': 'right'}
    if choice == 'custom':
        return {'value': (decision.get('values') or {}), 'source': 'decision', 'row': row, 'picked': 'custom'}
    return None


def compute(session, left, right, conflict_rows):
    base_rows = session.base_records
    base_map = _record_map(base_rows)
    left_map = _record_map(left.records)
    right_map = _record_map(right.records)
    rows_by_key = {
        (c.segment_id, c.field, c.kind): c for c in conflict_rows
        if c.status in ('open', 'decided')
    }
    merged = []
    trace = []
    issues = []
    open_rows = [c for c in conflict_rows if c.status == 'open']

    def field_trace(field, value, source, base, lval, rval):
        return {
            'field': field, 'label': _label(field), 'value': value, 'source': source,
            'base': base, 'left': lval, 'right': rval,
        }

    for base in base_rows:
        sid = base['segment_id']
        lrec = left_map.get(sid)
        rrec = right_map.get(sid)
        lrev = _rev(left.row_revs, sid)
        rrev = _rev(right.row_revs, sid)
        if lrec is None and rrec is None:
            trace.append({'segment_id': sid, 'action': 'deleted', 'source': 'deleted_both',
                          'left_rev': lrev, 'right_rev': rrev, 'fields': []})
            continue
        if lrec is None or rrec is None:
            side = 'left' if lrec is None else 'right'
            present = lrec if lrec is not None else rrec
            if not _diff(base, present):
                trace.append({'segment_id': sid, 'action': 'deleted', 'source': 'deleted_' + side,
                              'left_rev': lrev, 'right_rev': rrev, 'fields': []})
                continue
            row = rows_by_key.get((sid, '*', 'delete_edit'))
            decision = _decision(row)
            if decision is None:
                merged.append(_deepcopy.deepcopy(base))
                trace.append({
                    'segment_id': sid, 'action': 'unresolved', 'source': 'unresolved',
                    'left_rev': lrev, 'right_rev': rrev,
                    'fields': [field_trace(f, base[f], 'unresolved', base[f], lrec[f] if lrec else None,
                                           rrec[f] if rrec else None) for f in VALUE_FIELDS],
                })
                continue
            # 裁决选项：left/right 指保留该侧送校内容（含其删除意图），delete 统一删除。
            choice = (row.decision or {}).get('choice')
            if choice == 'delete' or choice == side:
                trace.append({'segment_id': sid, 'action': 'deleted', 'source': 'decision',
                              'left_rev': lrev, 'right_rev': rrev, 'fields': [],
                              'conflict_id': row.id})
                continue
            chosen = decision.get('value')
            if not isinstance(chosen, dict):
                chosen = present
            try:
                chosen = canonical_record(chosen)
            except ValueError:
                chosen = present
            merged.append(chosen)
            source = 'decision' if choice == 'custom' else choice
            trace.append({
                'segment_id': sid, 'action': 'kept', 'source': source,
                'left_rev': lrev, 'right_rev': rrev, 'conflict_id': row.id,
                'fields': [field_trace(f, chosen[f], source, base[f], lrec[f] if lrec else None,
                                       rrec[f] if rrec else None) for f in VALUE_FIELDS],
            })
            continue

        row_out = {}
        trace_fields = []
        used_decision = False
        for field in VALUE_FIELDS:
            bval, lval, rval = base[field], lrec[field], rrec[field]
            left_changed, right_changed = lval != bval, rval != bval
            if left_changed and right_changed:
                if lval == rval:
                    row_out[field] = lval
                    trace_fields.append(field_trace(field, lval, 'both', bval, lval, rval))
                    continue
                row = rows_by_key.get((sid, field, 'same_field_edit'))
                decision = _decision(row)
                if decision is None:
                    row_out[field] = bval
                    trace_fields.append(field_trace(field, bval, 'unresolved', bval, lval, rval))
                else:
                    chosen = decision.get('value')
                    if isinstance(chosen, dict):
                        chosen = chosen.get(field, bval)
                    value = chosen if chosen is not None else bval
                    row_out[field] = value
                    trace_fields.append(field_trace(field, value, 'decision', bval, lval, rval))
                    used_decision = True
            elif left_changed:
                row_out[field] = lval
                trace_fields.append(field_trace(field, lval, 'left', bval, lval, rval))
            elif right_changed:
                row_out[field] = rval
                trace_fields.append(field_trace(field, rval, 'right', bval, lval, rval))
            else:
                row_out[field] = bval
                trace_fields.append(field_trace(field, bval, 'base', bval, lval, rval))
        record_out = {'segment_id': sid, **row_out}
        merged.append(record_out)
        source = 'decision' if used_decision else ('base' if all(t['source'] == 'base' for t in trace_fields) else 'merged')
        trace.append({'segment_id': sid, 'action': 'merged', 'source': source,
                      'left_rev': lrev, 'right_rev': rrev, 'fields': trace_fields})

    added_ids = sorted((set(left_map) - set(base_map)) | (set(right_map) - set(base_map)))
    for sid in added_ids:
        lrec = left_map.get(sid)
        rrec = right_map.get(sid)
        lrev = _rev(left.row_revs, sid)
        rrev = _rev(right.row_revs, sid)
        if lrec is not None and rrec is not None:
            if lrec == rrec:
                merged.append(_deepcopy.deepcopy(lrec))
                trace.append({'segment_id': sid, 'action': 'added', 'source': 'both_add',
                              'left_rev': lrev, 'right_rev': rrev,
                              'fields': [field_trace(f, lrec[f], 'both', None, lrec[f], rrec[f]) for f in VALUE_FIELDS]})
                continue
            row = rows_by_key.get((sid, '*', 'same_id_add'))
            decision = _decision(row)
            if decision is None:
                merged.append(_deepcopy.deepcopy(lrec))
                trace.append({'segment_id': sid, 'action': 'unresolved_add', 'source': 'unresolved',
                              'left_rev': lrev, 'right_rev': rrev,
                              'fields': [field_trace(f, lrec[f], 'unresolved', None, lrec[f], rrec[f]) for f in VALUE_FIELDS]})
                continue
            if decision.get('delete'):
                trace.append({'segment_id': sid, 'action': 'dropped', 'source': 'decision',
                              'left_rev': lrev, 'right_rev': rrev, 'fields': [], 'conflict_id': row.id})
                continue
            chosen = decision.get('value')
            try:
                chosen = canonical_record(chosen)
            except (ValueError, TypeError):
                chosen = lrec
            merged.append(chosen)
            trace.append({'segment_id': sid, 'action': 'added', 'source': 'decision_add',
                          'left_rev': lrev, 'right_rev': rrev, 'conflict_id': row.id,
                          'fields': [field_trace(f, chosen[f], 'decision', None, lrec[f], rrec[f]) for f in VALUE_FIELDS]})
            continue
        side = 'left' if lrec is not None else 'right'
        record_out = _deepcopy.deepcopy(lrec if lrec is not None else rrec)
        merged.append(record_out)
        trace.append({
            'segment_id': sid, 'action': 'added', 'source': side + '_add',
            'left_rev': lrev, 'right_rev': rrev,
            'fields': [field_trace(f, record_out[f], side, None,
                                   lrec[f] if lrec is not None else None,
                                   rrec[f] if rrec is not None else None) for f in VALUE_FIELDS],
        })

    if open_rows:
        issues.append(f'{len(open_rows)} 项冲突尚未裁决')
    seen = set()
    for row in merged:
        sid = row['segment_id']
        if sid in seen:
            issues.append(f'管段 {sid} 在合校结果中重复')
        seen.add(sid)
        try:
            canonical_record(row)
        except ValueError as exc:
            issues.append(f'管段 {sid}：{exc}')
    if len(merged) < 40:
        issues.append(f'合校批次共 {len(merged)} 段，少于可试验要求的 40 段')
    labels = [int(row.get('confirmed_leak', 0)) for row in merged if row.get('confirmed_leak') in (0, 1)]
    leak = sum(1 for value in labels if value == 1)
    normal = sum(1 for value in labels if value == 0)
    if labels and (leak < 8 or normal < 8):
        issues.append(f'合校后漏点 {leak} 段、非漏点 {normal} 段，每类至少需要 8 段')
    valid = not issues
    return {
        'records': merged, 'trace': trace, 'issues': issues, 'valid': valid,
        'checksum': checksum(merged), 'rows': len(merged),
        'open_conflicts': len(open_rows),
        'decided_conflicts': sum(1 for c in conflict_rows if c.status == 'decided'),
    }


# ---------- 裁决 ----------

def decide(db, session_id, conflict_id, data, user):
    session = _session(db, session_id, lock=True)
    _ensure_open(session)
    refresh(db, session)
    row = db.get(MergeConflict, conflict_id)
    if not row or row.session_id != session.id:
        raise HTTPException(404, '冲突项不存在')
    if row.status == 'resolved':
        raise HTTPException(409, '该冲突已随两侧来源一致而消解，无需裁决')
    if data.left_rev is not None and data.right_rev is not None:
        if data.left_rev != row.left_rev or data.right_rev != row.right_rev:
            raise HTTPException(409, '冲突来源已更新，请刷新后重新核对再裁决')
    choice = (data.choice or '').strip()
    if choice not in ('left', 'right', 'custom', 'delete'):
        raise HTTPException(422, '裁决须选择左副本、右副本、自定义或删除')
    values = data.values or None
    if row.kind == 'same_field_edit':
        if choice == 'delete':
            raise HTTPException(422, '同项异改只能选择一侧或填写自定义值')
        if choice == 'custom':
            if not isinstance(values, dict) or row.field not in values:
                raise HTTPException(422, f'自定义裁决须填写{_label(row.field)}')
            try:
                values = {row.field: canonical_value(row.field, values[row.field])}
            except ValueError as exc:
                raise HTTPException(422, str(exc))
    elif row.kind == 'delete_edit':
        if choice == 'custom':
            try:
                values = canonical_record(values)
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, f'自定义裁决须给出完整管段：{exc}')
        else:
            values = None
    elif row.kind == 'same_id_add':
        if choice == 'custom':
            try:
                values = canonical_record(values)
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, f'自定义裁决须给出完整管段：{exc}')
        else:
            values = None
    note = (data.note or '').strip()[:500]
    previous = row.decision
    history = list(row.history or [])
    if previous:
        history.append({
            'action': 'redecided', 'at': time.time(), 'previous_decision': previous,
            'decided_by': row.decided_by,
        })
    row.history = history
    row.decision = {'choice': choice, 'values': values, 'note': note}
    row.status = 'decided'
    row.decided_by = user.email
    row.decided_at = time.time()
    row.decision_left_rev = row.left_rev
    row.decision_right_rev = row.right_rev
    row.closed_reason = None
    record(db, user.email, 'merge.conflict.decide', {
        'session': session.id, 'conflict': row.id, 'segment': row.segment_id,
        'kind': row.kind, 'choice': choice, 'left_rev': row.left_rev, 'right_rev': row.right_rev,
    })
    db.commit()
    return session


# ---------- 结案 ----------

def close(db, session_id, user):
    session = _session(db, session_id, lock=True)
    if session.status == 'closed':
        dataset = db.get(Dataset, session.output_dataset_id) if session.output_dataset_id else None
        return session, dataset
    _ensure_open(session)
    conflicts, left, right = refresh(db, session)
    merge = compute(session, left, right, conflicts)
    if merge['open_conflicts']:
        raise HTTPException(409, f'仍有 {merge["open_conflicts"]} 项冲突待负责人裁决，暂不能生成新批次')
    for copy, side in ((left, 'left'), (right, 'right')):
        if copy.confirmed_version is None or copy.confirmed_version != copy.version:
            raise HTTPException(409, f'{copy.label}尚未确认当前版本（版本 {copy.version}），请先确认两侧副本')
    if not merge['valid']:
        raise HTTPException(422, '整批未通过可试验校验：' + '；'.join(merge['issues']))
    try:
        analyze(merge['records'], {p['key']: p['default'] for p in metadata().get('parameters', [])})
    except ValueError as exc:
        raise HTTPException(422, f'整批未通过可试验校验：{exc}')
    name = f'合校批次 · {session.name}'
    existing = db.scalar(select(Dataset).where(Dataset.name == name))
    dataset = existing
    if dataset is None:
        dataset = Dataset(
            name=name, source=f'merge-session-{session.id}', records=merge['records'],
            checksum=merge['checksum'], created_at=time.time(),
        )
        db.add(dataset)
        try:
            db.flush()
        except IntegrityError:
            # 并发结案：另一请求已写入同名批次，改用它，保证只成一批
            db.rollback()
            session = _session(db, session_id, lock=True)
            if session.status == 'closed' and session.output_dataset_id:
                return session, db.get(Dataset, session.output_dataset_id)
            dataset = db.scalar(select(Dataset).where(Dataset.name == name))
            if dataset is None:
                raise HTTPException(409, '结案写入冲突，请重试')
    elif dataset.checksum != merge['checksum']:
        # 上次结案中断留下的同名批次：未被任何试验或简报引用时原位续写，保证重试不重复成批。
        from src.models import Job, Report
        referenced = db.scalar(select(func.count()).select_from(Job).where(Job.dataset_id == dataset.id)) or 0
        referenced += db.scalar(select(func.count()).select_from(Report).join(Job, Report.job_id == Job.id).where(Job.dataset_id == dataset.id)) or 0
        if referenced or dataset.source != f'merge-session-{session.id}':
            raise HTTPException(409, f'已存在同名批次但内容不同（上次结案中断且已被引用）；请核对“{name}”后重试')
        dataset.records = merge['records']
        dataset.checksum = merge['checksum']
    session.status = 'closed'
    session.output_dataset_id = dataset.id
    session.result_checksum = merge['checksum']
    session.final_records = merge['records']
    session.final_provenance = merge['trace']
    session.closed_at = time.time()
    session.closed_by = user.email
    record(db, user.email, 'merge.close', {
        'session': session.id, 'dataset': dataset.id, 'checksum': merge['checksum'],
        'rows': merge['rows'], 'left_version': left.version, 'right_version': right.version,
    })
    db.commit()
    return session, dataset


# ---------- 序列化 ----------

def conflict_dict(row):
    return {
        'id': row.id, 'segment_id': row.segment_id, 'field': row.field,
        'field_label': '整段' if row.field == '*' else _label(row.field),
        'kind': row.kind, 'kind_label': KIND_LABELS.get(row.kind, row.kind),
        'status': row.status, 'base': row.base_value, 'left': row.left_value,
        'right': row.right_value, 'left_rev': row.left_rev, 'right_rev': row.right_rev,
        'decision': row.decision, 'decided_by': row.decided_by, 'decided_at': row.decided_at,
        'history': row.history or [], 'closed_reason': row.closed_reason,
    }


def copy_dict(copy, edits=None):
    return {
        'side': copy.side, 'label': copy.label, 'version': copy.version,
        'confirmed_version': copy.confirmed_version, 'confirmed_at': copy.confirmed_at,
        'confirmed_by': copy.confirmed_by, 'records': copy.records, 'row_revs': copy.row_revs,
        'updated_at': copy.updated_at, 'edits': edits,
    }


def summary(session, left=None, right=None, conflicts=None):
    return {
        'id': session.id, 'name': session.name, 'status': session.status,
        'base_dataset_id': session.base_dataset_id, 'base_checksum': session.base_checksum,
        'base_rows': len(session.base_records), 'created_at': session.created_at,
        'closed_at': session.closed_at, 'closed_by': session.closed_by,
        'output_dataset_id': session.output_dataset_id, 'result_checksum': session.result_checksum,
        'left': None if left is None else {'label': left.label, 'version': left.version,
                                           'confirmed_version': left.confirmed_version},
        'right': None if right is None else {'label': right.label, 'version': right.version,
                                             'confirmed_version': right.confirmed_version},
        'open_conflicts': None if conflicts is None else sum(1 for c in conflicts if c.status == 'open'),
        'conflict_total': None if conflicts is None else len(conflicts),
    }


def detail(db, session_id):
    session = _session(db, session_id)
    left, right = _copies(db, session)
    if session.status == 'closed':
        conflicts = list(db.scalars(select(MergeConflict).where(MergeConflict.session_id == session.id).order_by(MergeConflict.id)))
        records = session.final_records or []
        trace = session.final_provenance or []
        merge = {
            'records': records, 'trace': trace, 'issues': [], 'valid': True,
            'checksum': session.result_checksum or checksum(records), 'rows': len(records),
            'open_conflicts': 0, 'decided_conflicts': sum(1 for c in conflicts if c.status == 'decided'),
        }
    else:
        # 冲突行的编号必须稳定，裁决请求按编号提交，因此预览会落一次幂等的物化刷新。
        conflicts, left, right = refresh(db, session)
        conflicts = sorted(conflicts, key=lambda c: (c.segment_id, c.kind, c.field))
        merge = compute(session, left, right, conflicts)
        db.commit()
    edits = list(db.scalars(
        select(ProofEdit).where(ProofEdit.copy_id.in_([left.id, right.id]))
        .order_by(ProofEdit.id.desc()).limit(400)
    ))
    edit_rows = [{
        'id': e.id, 'copy_id': e.copy_id, 'side': left.side if e.copy_id == left.id else right.side,
        'segment_id': e.segment_id, 'field': e.field, 'field_label': '整段' if e.field == '*' else _label(e.field),
        'action': e.action, 'old_value': e.old_value, 'new_value': e.new_value,
        'actor': e.actor, 'row_rev': e.row_rev, 'copy_version': e.copy_version, 'at': e.at,
    } for e in edits]
    by_side = {'left': [], 'right': []}
    for e in edit_rows:
        by_side[e['side']].append(e)
    return {
        'session': summary(session, left, right, conflicts),
        'base_records': session.base_records,
        'copies': [copy_dict(left, by_side['left']), copy_dict(right, by_side['right'])],
        'conflicts': [conflict_dict(c) for c in conflicts],
        'merge': merge,
        'edits': edit_rows,
        'fields': [{'key': f, 'label': _label(f)} for f in FIELDS],
        'kinds': KIND_LABELS,
    }


def edits_for(db, session_id, side, segment_id=None):
    session = _session(db, session_id)
    copy = _copy(db, session, side)
    query = select(ProofEdit).where(ProofEdit.copy_id == copy.id)
    if segment_id:
        query = query.where(ProofEdit.segment_id == segment_id)
    rows = list(db.scalars(query.order_by(ProofEdit.id.desc()).limit(1000)))
    return [{
        'id': e.id, 'segment_id': e.segment_id, 'field': e.field,
        'field_label': '整段' if e.field == '*' else _label(e.field),
        'action': e.action, 'old_value': e.old_value, 'new_value': e.new_value,
        'actor': e.actor, 'row_rev': e.row_rev, 'copy_version': e.copy_version, 'at': e.at,
    } for e in rows]


def listing(db):
    sessions = list(db.scalars(select(MergeSession).order_by(MergeSession.id.desc())))
    out = []
    for session in sessions:
        left, right = _copies(db, session)
        counts = dict(db.execute(
            select(MergeConflict.status, func.count()).where(MergeConflict.session_id == session.id)
            .group_by(MergeConflict.status)
        ).all())
        item = summary(session, left, right)
        item['open_conflicts'] = int(counts.get('open', 0))
        item['conflict_total'] = sum(int(v) for v in counts.values())
        item['decided_conflicts'] = int(counts.get('decided', 0))
        out.append(item)
    return out
