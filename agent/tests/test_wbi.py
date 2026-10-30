"""WBI 签名单测：结构正确性 + 确定性（真实接口有效性由 E2E 冒烟验证）。"""
from hashlib import md5

from omni.tools.bilibili.wbi import get_mixin_key, sign_wbi

IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


def test_mixin_key_shape():
    key = get_mixin_key(IMG_KEY, SUB_KEY)
    assert len(key) == 32
    assert all(c in "0123456789abcdef" for c in key)
    # 同输入必同输出
    assert key == get_mixin_key(IMG_KEY, SUB_KEY)


def test_sign_wbi_deterministic(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1700000000)
    signed = sign_wbi({"cid": 123, "bvid": "BV1GJ411x7h7"}, IMG_KEY, SUB_KEY)
    assert signed["wts"] == 1700000000
    # 参数按 key 排序后参与摘要
    query = "bvid=BV1GJ411x7h7&cid=123&wts=1700000000"
    expected = md5((query + get_mixin_key(IMG_KEY, SUB_KEY)).encode()).hexdigest()
    assert signed["w_rid"] == expected
    assert len(signed["w_rid"]) == 32


def test_sign_wbi_strips_special_chars(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1700000000)
    signed = sign_wbi({"q": "hello!'()* world"}, IMG_KEY, SUB_KEY)
    # 规范：!'()* 从值里剔除，其余按 quote 编码（空格 → %20）
    query = "q=hello%20world&wts=1700000000"
    expected = md5((query + get_mixin_key(IMG_KEY, SUB_KEY)).encode()).hexdigest()
    assert signed["w_rid"] == expected
