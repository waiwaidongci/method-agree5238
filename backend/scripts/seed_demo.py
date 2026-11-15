import random,json
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.models import User,Instrument,PairDataset,PairedReading,ComparisonRun,Qualification,MergeChannel,MergeEvent,MergeSpecimen,SampleSet
from src.utils.security import hash_password
from src.services.run_service import execute_run
from src.schemas.merge import MergeChannelInput,MergeEventInput,SampleSetInput
from src.services import merge_service

class _SeedActor:
    email = "seed@ciguang.demo"


def seed_merge(db, instruments):
    """演示双侧增量归并：覆盖完整、单侧、冲突阻断、撤回、乱序暂存与冻结排除。"""
    name = "N03 · 双侧增量回传演示"
    if db.scalar(select(MergeChannel).where(MergeChannel.name == name)):
        return
    ch = merge_service.create_channel(db, MergeChannelInput(
        name=name, reference_id=instruments[0].id, candidate_id=instruments[1].id,
        note="两台仪器分开回传；演示乱序暂存、撤回、同版异值阻断与冻结快照。"), _SeedActor())
    cid = ch["id"]

    def ev(side, code, version, kind="reading", value=None):
        return MergeEventInput(side=side, sample_code=code, version=version, kind=kind, value=value)

    # MG-01/02 完整配对
    for code, x, y in [("MG-01", 41.2, 42.0), ("MG-02", 55.6, 56.1)]:
        merge_service.ingest_event(db, cid, ev("reference", code, 1, value=x), _SeedActor())
        merge_service.ingest_event(db, cid, ev("candidate", code, 1, value=y), _SeedActor())
    # MG-03 乱序：v2 先到暂存，补 v1 后自动推进
    merge_service.ingest_event(db, cid, ev("reference", "MG-03", 2, value=60.4), _SeedActor())
    merge_service.ingest_event(db, cid, ev("reference", "MG-03", 1, value=59.8), _SeedActor())
    merge_service.ingest_event(db, cid, ev("candidate", "MG-03", 1, value=61.0), _SeedActor())
    # MG-04 参考侧撤回 -> 缺侧
    merge_service.ingest_event(db, cid, ev("reference", "MG-04", 1, value=33.3), _SeedActor())
    merge_service.ingest_event(db, cid, ev("candidate", "MG-04", 1, value=34.0), _SeedActor())
    merge_service.ingest_event(db, cid, ev("reference", "MG-04", 2, kind="withdrawal"), _SeedActor())
    # MG-05 同版异值 -> 永久阻断
    merge_service.ingest_event(db, cid, ev("reference", "MG-05", 1, value=70.0), _SeedActor())
    merge_service.ingest_event(db, cid, ev("candidate", "MG-05", 1, value=71.2), _SeedActor())
    merge_service.ingest_event(db, cid, ev("reference", "MG-05", 1, value=70.9), _SeedActor())
    # MG-06 只有候选侧 -> 缺侧
    merge_service.ingest_event(db, cid, ev("candidate", "MG-06", 1, value=48.5), _SeedActor())
    # MG-07 缺口暂存：v3 先到，v1/v2 未补齐 -> 缺版
    merge_service.ingest_event(db, cid, ev("reference", "MG-07", 3, value=22.7), _SeedActor())
    # MG-08 重复回传（幂等）与旧版回传（隔离不复活）
    merge_service.ingest_event(db, cid, ev("reference", "MG-08", 1, value=80.1), _SeedActor())
    merge_service.ingest_event(db, cid, ev("reference", "MG-08", 1, value=80.1), _SeedActor())
    merge_service.ingest_event(db, cid, ev("candidate", "MG-08", 1, value=81.0), _SeedActor())

    merge_service.freeze_sample_set(db, cid, SampleSetInput(
        name="N03 增量冻结 R1 · 演示", note="seed 演示：完整配对入选，冲突/缺版/缺侧逐条列明。"), _SeedActor())


def seed():
    Base.metadata.create_all(engine);rng=random.Random(73)
    with Session() as db:
        for role,name in [('admin','程瓷 · 实验室负责人'),('analyst','江光 · 测量工程师'),('viewer','许澄 · 设备观察员')]:
            if not db.scalar(select(User).where(User.email==f'{role}@ciguang.demo')):db.add(User(email=f'{role}@ciguang.demo',name=name,role=role,password_hash=hash_password('Ciguang2026!')))
        for code,name,serial in [('CG-R01','参考反射率仪 R01','CG-2018-001'),('CG-N02','新型反射率仪 N02','CG-2026-042'),('CG-N03','便携反射率仪 N03','CG-2026-083')]:
            if not db.scalar(select(Instrument).where(Instrument.code==code)):db.add(Instrument(code=code,name=name,serial=serial,unit='反射率 %',lower=0,upper=100))
        db.commit();instruments=db.scalars(select(Instrument).order_by(Instrument.id)).all()
        specs=[('N02 · 稳定光源配对',instruments[1],.35,1.002,False),('N02 · 系统偏差复核',instruments[1],1.5,1.045,False),('N03 · 宽范围异方差',instruments[2],.2,1.008,True)]
        for idx,(name,candidate,intercept,slope,hetero) in enumerate(specs):
            dataset=db.scalar(select(PairDataset).where(PairDataset.name==name))
            if not dataset:
                dataset=PairDataset(name=name,reference_id=instruments[0].id,candidate_id=candidate.id,source_file='ci-optics-pairs.csv',note='高量程误差扩大；需关注异方差，整体界限仅作描述。' if hetero else '独立陶瓷试片，每片同日各测一次；单位为反射率百分数。')
                db.add(dataset);db.flush()
                for i in range(180):
                    latent=8+i*80/179
                    x=round(latent+rng.gauss(0,.22),4)
                    noise=(.12 if latent<48 else 1.7) if hetero else .25
                    y=round(intercept+slope*latent+rng.gauss(0,noise),4)
                    db.add(PairedReading(dataset_id=dataset.id,sample_code=f'CG{idx+1}-{i+1:03d}',reference_value=x,candidate_value=y))
                db.commit()
            runname=name+' / λ=1'
            if not db.scalar(select(ComparisonRun).where(ComparisonRun.name==runname)):
                run=ComparisonRun(name=runname,dataset_id=dataset.id,lambda_ratio=1,allowance=2,range_low=0,range_high=100,bootstrap_count=500,status='queued');db.add(run);db.commit();execute_run(db,run.id)
        first=db.scalar(select(ComparisonRun).order_by(ComparisonRun.id))
        if first and first.status=='completed' and not db.scalar(select(Qualification).where(Qualification.title=='N02稳定光源资格 · 10–85%')):
            db.add(Qualification(title='N02稳定光源资格 · 10–85%',run_id=first.id,mode='raw',scope_low=10,scope_high=85,note='稳定光源条件下使用；仅覆盖本次参考反射率范围，不延伸到便携仪或其他光源。',status='approved',approved_by='admin@ciguang.demo'))
        db.commit()
        seed_merge(db, instruments)
        db.commit()
        print(json.dumps({m.__tablename__:db.scalar(select(func.count()).select_from(m)) for m in (User,Instrument,PairDataset,PairedReading,ComparisonRun,Qualification,MergeChannel,MergeEvent,MergeSpecimen,SampleSet)},ensure_ascii=False))
if __name__=='__main__':seed()
