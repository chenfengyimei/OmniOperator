"""仅由 pytest/离线评测显式加入 PYTHONPATH；不进入应用 wheel。"""

import os

if os.environ.get("OMNI_TEST_OFFLINE") == "1":
    from offline_guard import install

    install()
