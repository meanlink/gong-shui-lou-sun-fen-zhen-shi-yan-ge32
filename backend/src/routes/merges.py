from fastapi import APIRouter, Depends, HTTPException, Query
from src.config.database import get_db
from src.schemas.merge import MergeCreate, SegmentUpdate, SegmentAdd, VersionInput, DecisionInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import merge
from src.utils.serialization import dataset_dict
router = APIRouter(prefix='/merges')


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return merge.listing(db)


@router.post('', status_code=201)
def create(data: MergeCreate, db=Depends(get_db), u=Depends(can_write)):
    session = merge.create(db, data, u)
    db.commit()
    left, right = merge._copies(db, session)
    return merge.summary(session, left, right, [])


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    return merge.detail(db, id)


@router.get('/{id}/conflicts')
def conflicts(id: int, db=Depends(get_db), u=Depends(current_user)):
    return merge.detail(db, id)['conflicts']


@router.get('/{id}/edits')
def edits(id: int, side: str = Query('left'), segment_id: str | None = Query(None), db=Depends(get_db), u=Depends(current_user)):
    if side not in ('left', 'right'):
        raise HTTPException(422, 'side 须为 left 或 right')
    return merge.edits_for(db, id, side, segment_id)


@router.put('/{id}/copies/{side}/segments/{segment_id}')
def update_segment(id: int, side: str, segment_id: str, data: SegmentUpdate, db=Depends(get_db), u=Depends(can_write)):
    merge.update_segment(db, merge._session(db, id), side, segment_id, data.version, data.values, u)
    return merge.detail(db, id)


@router.delete('/{id}/copies/{side}/segments/{segment_id}')
def delete_segment(id: int, side: str, segment_id: str, version: int = Query(...), db=Depends(get_db), u=Depends(can_write)):
    merge.delete_segment(db, merge._session(db, id), side, segment_id, version, u)
    return merge.detail(db, id)


@router.post('/{id}/copies/{side}/segments', status_code=201)
def add_segment(id: int, side: str, data: SegmentAdd, db=Depends(get_db), u=Depends(can_write)):
    merge.add_segment(db, merge._session(db, id), side, data.version, data.record, u)
    return merge.detail(db, id)


@router.post('/{id}/copies/{side}/restore/{segment_id}')
def restore_segment(id: int, side: str, segment_id: str, data: VersionInput, db=Depends(get_db), u=Depends(can_write)):
    merge.restore_segment(db, merge._session(db, id), side, segment_id, data.version, u)
    return merge.detail(db, id)


@router.post('/{id}/copies/{side}/confirm')
def confirm(id: int, side: str, data: VersionInput, db=Depends(get_db), u=Depends(can_write)):
    merge.confirm(db, merge._session(db, id), side, data.version, u)
    return merge.detail(db, id)


@router.post('/{id}/conflicts/{conflict_id}/decide')
def decide(id: int, conflict_id: int, data: DecisionInput, db=Depends(get_db), u=Depends(admin_only)):
    merge.decide(db, id, conflict_id, data, u)
    return merge.detail(db, id)


@router.post('/{id}/close')
def close(id: int, db=Depends(get_db), u=Depends(admin_only)):
    session, dataset = merge.close(db, id, u)
    payload = merge.detail(db, id)
    payload['dataset'] = dataset_dict(dataset) if dataset else None
    payload['session']['output_dataset_id'] = session.output_dataset_id
    return payload
