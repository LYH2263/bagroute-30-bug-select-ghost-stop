from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import BagItem, DeliveryRoute, PackBag, RejectRecord, SubscriberStop
from app.schemas.schemas import (
    BagItemOut,
    BagOut,
    PackRequest,
    RejectOut,
    RouteOut,
    StopOut,
    WeightOut,
)
from app.services.pack_engine import StopItem, pack_route

api_router = APIRouter()


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/routes", response_model=list[RouteOut])
def routes(db: Session = Depends(get_db)):
    return db.scalars(select(DeliveryRoute).order_by(DeliveryRoute.id)).all()


@api_router.get("/stops", response_model=list[StopOut])
def stops(route_id: int | None = None, db: Session = Depends(get_db)):
    q = select(SubscriberStop).order_by(SubscriberStop.route_id, SubscriberStop.seq)
    if route_id is not None:
        q = q.where(SubscriberStop.route_id == route_id)
    return db.scalars(q).all()


@api_router.post("/pack", response_model=list[BagOut])
def pack(body: PackRequest, db: Session = Depends(get_db)):
    route = db.get(DeliveryRoute, body.route_id)
    if not route:
        raise HTTPException(404, "路线不存在")
    # 显式空勾选：失败且不动已有袋明细（必须在清库之前校验）。
    if body.stop_ids is not None and len(body.stop_ids) == 0:
        raise HTTPException(400, "勾选站点为空，未执行装袋")

    stops = db.scalars(
        select(SubscriberStop).where(SubscriberStop.route_id == route.id).order_by(SubscriberStop.seq)
    ).all()
    if body.stop_ids is not None:
        selected = set(body.stop_ids)
        route_stop_ids = {s.id for s in stops}
        unknown = selected - route_stop_ids
        if unknown:
            raise HTTPException(400, f"站点不属于该路线：{sorted(unknown)}")
        # 只装本次勾选的站点：未勾选站不入袋，也不进本次拒收
        stops = [s for s in stops if s.id in selected]

    # 清掉该路线上一轮的袋与拒收，保证本次结果不残留旧行
    old_bags = db.scalars(select(PackBag).where(PackBag.route_id == route.id)).all()
    for b in old_bags:
        for it in list(b.items):
            db.delete(it)
        db.delete(b)
    old_rejects = db.scalars(select(RejectRecord).where(RejectRecord.route_id == route.id)).all()
    for r in old_rejects:
        db.delete(r)
    db.flush()

    items = [
        StopItem(s.id, s.seq, s.weight_kg, s.volume_l, s.name) for s in stops
    ]
    result = pack_route(items, route.max_weight_kg, route.max_volume_l)
    out_bags: list[PackBag] = []
    for bag in result.bags:
        row = PackBag(
            route_id=route.id,
            bag_index=bag.bag_index,
            weight_kg=round(bag.weight_kg, 3),
            volume_l=round(bag.volume_l, 3),
        )
        db.add(row)
        db.flush()
        for it in bag.items:
            db.add(
                BagItem(
                    bag_id=row.id,
                    stop_id=it.stop_id,
                    stop_name=it.label,
                    weight_kg=it.weight_kg,
                    volume_l=it.volume_l,
                )
            )
        out_bags.append(row)
    for stop, reason in result.rejects:
        db.add(
            RejectRecord(
                route_id=route.id,
                stop_id=stop.stop_id,
                stop_name=stop.label,
                reason=reason,
            )
        )
    db.commit()
    return [
        BagOut(
            id=b.id,
            route_id=b.route_id,
            bag_index=b.bag_index,
            weight_kg=b.weight_kg,
            volume_l=b.volume_l,
            items=[
                BagItemOut(
                    stop_id=i.stop_id,
                    stop_name=i.stop_name,
                    weight_kg=i.weight_kg,
                    volume_l=i.volume_l,
                )
                for i in db.scalars(select(BagItem).where(BagItem.bag_id == b.id)).all()
            ],
        )
        for b in out_bags
    ]


@api_router.get("/bags", response_model=list[BagOut])
def bags(db: Session = Depends(get_db)):
    rows = db.scalars(select(PackBag).order_by(PackBag.route_id, PackBag.bag_index)).all()
    out = []
    for b in rows:
        items = db.scalars(select(BagItem).where(BagItem.bag_id == b.id)).all()
        out.append(
            BagOut(
                id=b.id,
                route_id=b.route_id,
                bag_index=b.bag_index,
                weight_kg=b.weight_kg,
                volume_l=b.volume_l,
                items=[
                    BagItemOut(
                        stop_id=i.stop_id,
                        stop_name=i.stop_name,
                        weight_kg=i.weight_kg,
                        volume_l=i.volume_l,
                    )
                    for i in items
                ],
            )
        )
    return out


@api_router.get("/rejects", response_model=list[RejectOut])
def rejects(db: Session = Depends(get_db)):
    return db.scalars(select(RejectRecord).order_by(RejectRecord.id.desc())).all()


@api_router.get("/weights", response_model=list[WeightOut])
def weights(db: Session = Depends(get_db)):
    bags = db.scalars(select(PackBag).order_by(PackBag.id)).all()
    out = []
    for b in bags:
        route = db.get(DeliveryRoute, b.route_id)
        assert route
        out.append(
            WeightOut(
                bag_id=b.id,
                bag_index=b.bag_index,
                route_id=b.route_id,
                weight_kg=b.weight_kg,
                volume_l=b.volume_l,
                fill_weight_pct=round(100 * b.weight_kg / route.max_weight_kg, 1),
                fill_volume_pct=round(100 * b.volume_l / route.max_volume_l, 1),
            )
        )
    return out
