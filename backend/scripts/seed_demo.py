import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, MergeSession, ProofCopy, ProofEdit, MergeConflict
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        _seed_merge(db,d)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,MergeSession,ProofCopy,MergeConflict]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts

def _seed_merge(db,d):
    """演示一次两队分头校对：各自修订若干管段，并留出三类待裁决冲突。"""
    name='演示合校 · 夜间流量复检'
    if db.scalar(select(MergeSession).where(MergeSession.name==name)):
        return
    base=[{'segment_id':r['segment_id'],'night_flow':r['night_flow'],'pressure_drop':r['pressure_drop'],'acoustic_db':r['acoustic_db'],'pipe_age':int(r['pipe_age']),'confirmed_leak':int(r['confirmed_leak'])} for r in d.records]
    index={row['segment_id']:row for row in base}
    needed=['NC-005','NC-006','NC-007','NC-011','NC-012']
    if not all(sid in index for sid in needed):
        return
    import copy as _cp
    left=_cp.deepcopy(base); right=_cp.deepcopy(base)
    left_map={r['segment_id']:r for r in left}; right_map={r['segment_id']:r for r in right}
    now=time.time()
    left_edits=[('NC-005','pressure_drop',3.2),('NC-007','acoustic_db',-2.5),('NC-011','night_flow',1.8)]
    right_edits=[('NC-006','acoustic_db',4.1),('NC-007','acoustic_db',-4.0),('NC-011','night_flow',2.4),('NC-012','pipe_age',2)]
    delete_left='NC-012'
    added_left={'segment_id':'NC-281','night_flow':31.4,'pressure_drop':21.2,'acoustic_db':47.5,'pipe_age':22,'confirmed_leak':1}
    added_right=dict(added_left,night_flow=33.1,acoustic_db=49.0)
    revs={'left':{},'right':{}}
    for side,records,edits in (('left',left,left_edits),('right',right,right_edits)):
        for sid,field,delta in edits:
            target=left_map if side=='left' else right_map
            target[sid][field]=round(float(index[sid][field])+delta,2)
            revs[side][sid]=1
    left=[r for r in left if r['segment_id']!=delete_left]
    revs['left'][delete_left]=1
    left.append(added_left); revs['left']['NC-281']=1
    right.append(added_right); revs['right']['NC-281']=1
    admin=db.scalar(select(User).where(User.role=='admin'))
    session=MergeSession(name=name,base_dataset_id=d.id,base_checksum=d.checksum,base_records=base,status='open',created_by=admin.id,created_at=now)
    db.add(session); db.flush()
    left_copy=ProofCopy(session_id=session.id,side='left',label='甲队',records=left,row_revs=revs['left'],version=0,created_at=now,updated_at=now)
    right_copy=ProofCopy(session_id=session.id,side='right',label='乙队',records=right,row_revs=revs['right'],version=0,created_at=now,updated_at=now)
    db.add(left_copy); db.add(right_copy); db.flush()
    for side,edits,copy,target in (('left',left_edits,left_copy,left_map),('right',right_edits,right_copy,right_map)):
        for sid,field,delta in edits:
            db.add(ProofEdit(copy_id=copy.id,segment_id=sid,field=field,action='update',old_value=index[sid][field],new_value=target[sid][field],actor=admin.email,row_rev=1,copy_version=0,at=now))
    db.add(ProofEdit(copy_id=left_copy.id,segment_id=delete_left,field='*',action='delete',old_value=index[delete_left],new_value=None,actor=admin.email,row_rev=1,copy_version=0,at=now))
    db.add(ProofEdit(copy_id=left_copy.id,segment_id='NC-281',field='*',action='add',old_value=None,new_value=added_left,actor=admin.email,row_rev=1,copy_version=0,at=now))
    db.add(ProofEdit(copy_id=right_copy.id,segment_id='NC-281',field='*',action='add',old_value=None,new_value=added_right,actor=admin.email,row_rev=1,copy_version=0,at=now))
    db.add(Audit(actor='seed',action='merge.demo',detail={'session':session.id,'rows':len(base)},at=now))

if __name__=='__main__':seed()
