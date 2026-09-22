import os

# 测试全程使用 sqlite，app.config 默认指向 postgres，需在导入 app 之前设置
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SEED_ON_EMPTY", "false")
