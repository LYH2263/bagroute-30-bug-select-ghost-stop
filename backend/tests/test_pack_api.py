from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import (
    BagItem,
    DeliveryRoute,
    PackBag,
    RejectRecord,
    SubscriberStop,
)


def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 不用 with 触发 lifespan，避免连默认 postgres 建表
    client = TestClient(app)
    return client, TestingSession


def _seed_route(db, name: str = "测试线"):
    route = DeliveryRoute(name=name, max_weight_kg=8.0, max_volume_l=18.0)
    db.add(route)
    db.flush()
    db.add_all(
        [
            SubscriberStop(route_id=route.id, seq=1, name="甲站", weight_kg=2.0, volume_l=4.0),
            SubscriberStop(route_id=route.id, seq=2, name="乙站", weight_kg=3.0, volume_l=5.0),
            SubscriberStop(route_id=route.id, seq=3, name="丙站", weight_kg=1.0, volume_l=2.0),
            # 勾到它会因超重被拒收；用来验证未勾选时拒收也不写
            SubscriberStop(route_id=route.id, seq=4, name="超大站", weight_kg=9.5, volume_l=6.0),
        ]
    )
    db.commit()
    return route.id


def _packed_stop_ids(db):
    return {it.stop_id for it in db.scalars(select(BagItem)).all()}


def _reject_stop_ids(db):
    return {r.stop_id for r in db.scalars(select(RejectRecord)).all()}


def test_partial_pack_excludes_unselected_from_bags_and_rejects():
    client, Session = _make_client()
    db = Session()
    route_id = _seed_route(db)
    stop_ids = [s.id for s in db.scalars(select(SubscriberStop).order_by(SubscriberStop.seq)).all()]
    db.close()
    first_two, third, oversized = stop_ids[:2], stop_ids[2], stop_ids[3]

    resp = client.post("/api/pack", json={"route_id": route_id, "stop_ids": first_two})
    assert resp.status_code == 200, resp.text
    bags = resp.json()
    packed = {item["stop_id"] for b in bags for item in b["items"]}
    assert packed == set(first_two)
    assert third not in packed

    db = Session()
    # 袋明细与勾选集合对得上
    assert _packed_stop_ids(db) == set(first_two)
    # 第三站不在拒收；未勾选的超大站同样不写入拒收
    assert _reject_stop_ids(db) == set()
    assert third not in _reject_stop_ids(db)
    assert oversized not in _reject_stop_ids(db)
    db.close()


def test_empty_selection_fails_without_writing_db():
    client, Session = _make_client()
    db = Session()
    route_id = _seed_route(db)
    stop_ids = [s.id for s in db.scalars(select(SubscriberStop).order_by(SubscriberStop.seq)).all()]
    db.close()

    # 先整线装袋，留下已有袋明细与拒收
    ok = client.post("/api/pack", json={"route_id": route_id})
    assert ok.status_code == 200

    db = Session()
    before_bag_ids = {b.id for b in db.scalars(select(PackBag)).all()}
    before_items = sorted(
        (it.bag_id, it.stop_id) for it in db.scalars(select(BagItem)).all()
    )
    before_rejects = sorted((r.stop_id, r.reason) for r in db.scalars(select(RejectRecord)).all())
    db.close()
    assert before_items  # 确认库里确实有旧数据

    resp = client.post("/api/pack", json={"route_id": route_id, "stop_ids": []})
    assert resp.status_code == 400, resp.text

    db = Session()
    after_bag_ids = {b.id for b in db.scalars(select(PackBag)).all()}
    after_items = sorted(
        (it.bag_id, it.stop_id) for it in db.scalars(select(BagItem)).all()
    )
    after_rejects = sorted((r.stop_id, r.reason) for r in db.scalars(select(RejectRecord)).all())
    db.close()
    # 空勾选失败后，已有袋明细/拒收原样保留
    assert after_bag_ids == before_bag_ids
    assert after_items == before_items
    assert after_rejects == before_rejects


def test_full_route_pack_without_stop_ids_covers_all_stops():
    client, Session = _make_client()
    db = Session()
    route_id = _seed_route(db)
    stop_ids = [s.id for s in db.scalars(select(SubscriberStop).order_by(SubscriberStop.seq)).all()]
    db.close()

    resp = client.post("/api/pack", json={"route_id": route_id})
    assert resp.status_code == 200, resp.text
    packed = {item["stop_id"] for b in resp.json() for item in b["items"]}
    assert packed == set(stop_ids[:3])  # 超大站被拒收

    db = Session()
    assert _reject_stop_ids(db) == {stop_ids[3]}
    db.close()


def test_repack_clears_ghost_bags_and_rejects_for_route():
    client, Session = _make_client()
    db = Session()
    route_id = _seed_route(db)
    stop_ids = [s.id for s in db.scalars(select(SubscriberStop).order_by(SubscriberStop.seq)).all()]
    db.close()
    first_two, third, oversized = stop_ids[:2], stop_ids[2], stop_ids[3]

    # 先整线装袋：四站都参与，超大站留下拒收
    resp = client.post("/api/pack", json={"route_id": route_id})
    assert resp.status_code == 200, resp.text
    db = Session()
    assert _packed_stop_ids(db) == set(stop_ids[:3])
    assert _reject_stop_ids(db) == {oversized}
    db.close()

    # 再只勾选前两站重装：旧袋与旧拒收必须随本次集合重写
    resp = client.post("/api/pack", json={"route_id": route_id, "stop_ids": first_two})
    assert resp.status_code == 200, resp.text
    db = Session()
    assert _packed_stop_ids(db) == set(first_two)
    assert third not in _packed_stop_ids(db)
    assert _reject_stop_ids(db) == set()
    assert oversized not in _reject_stop_ids(db)
    db.close()


def test_repack_does_not_touch_other_route_data():
    client, Session = _make_client()
    db = Session()
    route_a = _seed_route(db)
    route_b = _seed_route(db, "邻线")
    a_ids = [s.id for s in db.scalars(select(SubscriberStop).where(SubscriberStop.route_id == route_a).order_by(SubscriberStop.seq)).all()]
    b_ids = [s.id for s in db.scalars(select(SubscriberStop).where(SubscriberStop.route_id == route_b).order_by(SubscriberStop.seq)).all()]
    db.close()

    # 两条线都整线装袋，各有一条超大站拒收
    assert client.post("/api/pack", json={"route_id": route_a}).status_code == 200
    assert client.post("/api/pack", json={"route_id": route_b}).status_code == 200

    # 对 A 线只勾前两站重装，B 线的袋与拒收原样保留
    resp = client.post("/api/pack", json={"route_id": route_a, "stop_ids": a_ids[:2]})
    assert resp.status_code == 200, resp.text
    db = Session()
    assert _reject_stop_ids(db) == {b_ids[3]}
    assert {it.stop_id for it in db.scalars(select(BagItem)).all()} == set(a_ids[:2]) | set(b_ids[:3])
    db.close()


def test_unknown_stop_id_rejected():
    client, Session = _make_client()
    db = Session()
    route_id = _seed_route(db)
    db.close()

    resp = client.post("/api/pack", json={"route_id": route_id, "stop_ids": [99999]})
    assert resp.status_code == 400
