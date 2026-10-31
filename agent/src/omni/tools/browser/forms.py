"""固定配置的 UI 表单观察/提交；所有操作在 BrowserDriver 的所有者线程执行。

不调用站点写 API，不从工具参数接受脚本/选择器；表单漂移、登录或挑战异常直接停止。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from urllib.parse import urlsplit

from ...config import UIFormSelectors
from .driver import BrowserDriver, BrowserError, BrowserUnknown


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _identity(value: str) -> str:
    value = value.strip()
    if "://" in value or value.startswith("//"):
        return urlsplit(value).path.strip("/").split("/")[-1]
    return value


class BrowserForms:
    def __init__(self, driver: BrowserDriver, profiles: dict[str, UIFormSelectors], *,
                 allowed_hosts: tuple[str, ...] = ("www.bilibili.com", "message.bilibili.com")) -> None:
        self.driver, self.profiles = driver, profiles
        self.allowed_hosts = allowed_hosts

    @staticmethod
    def _one(page, selector: str):
        locator = page.locator(selector)
        if locator.count() != 1 or not locator.is_visible():
            raise BrowserError("表单控件缺失、不唯一或不可见；未操作")
        return locator

    @staticmethod
    def _raw(locator, attribute: str) -> str:
        raw = locator.inner_text(timeout=2000) if attribute == "text" else locator.get_attribute(attribute, timeout=2000)
        if raw is None or not raw.strip() or len(raw) > 2048:
            raise BrowserError("页面身份属性无法核验")
        return raw

    @classmethod
    def _value(cls, locator, attribute: str) -> str:
        return _identity(cls._raw(locator, attribute))

    def _records(self, page, profile: UIFormSelectors) -> list[dict]:
        records = page.locator(profile.records)
        result = []
        for index in range(min(records.count(), 30)):
            row = records.nth(index)
            identity = row.get_attribute(profile.record_id_attr, timeout=2000)
            authors, texts = row.locator(profile.record_author), row.locator(profile.record_text)
            if not identity or len(identity) > 128 or authors.count() != 1 or texts.count() != 1:
                continue
            text = texts.inner_text(timeout=2000).strip()
            if len(text) > 2200:
                continue
            result.append({"id": identity, "account": self._value(authors, profile.record_author_attr),
                           "content_hash": hashlib.sha256(text.encode()).hexdigest()})
        return result

    def _capture(self, kind: str) -> tuple[dict, dict]:
        profile = self.profiles.get(kind)
        if profile is None:
            raise BrowserError("该写流程尚未配置经人工核验的控件描述")
        page = self.driver._ensure_page()
        url = urlsplit(page.url)
        if url.scheme != "https" or url.hostname not in self.allowed_hosts or url.username or url.password:
            raise BrowserError("当前页面不在允许的 B站域名内")
        for selector in (profile.challenge, profile.logged_out):
            matches = page.locator(selector)
            if matches.count() > 32 or any(matches.nth(i).is_visible() for i in range(matches.count())):
                raise BrowserError("页面存在登录失效/验证码/安全挑战，需人工处理")
        account, target, editor, submit = [self._one(page, selector) for selector in
                                           (profile.account, profile.target, profile.editor, profile.submit)]
        if not editor.is_editable() or (editor.get_attribute("type") or "").lower() == "password":
            raise BrowserError("编辑控件不可用")
        # 一些站点空内容时按钮禁用；提交阶段会再次检查可用状态。
        text = editor.evaluate("e => ('value' in e ? e.value : e.textContent)") or ""
        if len(text) > 2200:
            raise BrowserError("现有输入过长，拒绝覆盖")
        key = "e => [e.tagName,e.id,e.getAttribute('name'),e.getAttribute('type'),e.getAttribute('aria-label')]"
        account_raw, target_raw = self._raw(account, profile.account_attr), self._raw(target, profile.target_attr)
        observation = {"url": page.url, "epoch": page.evaluate("performance.timeOrigin"),
                       "account": _identity(account_raw), "target": _identity(target_raw),
                       "account_raw": account_raw, "target_raw": target_raw,
                       "editor_value": text, "editor_key": editor.evaluate(key), "submit_key": submit.evaluate(key),
                       "profile_hash": _digest(profile.model_dump()), "records": self._records(page, profile)}
        observation["fingerprint"] = _digest(observation)
        return observation, {"page": page, "account": account, "target": target, "editor": editor, "submit": submit}

    def observe(self, kind: str) -> dict:
        return self.driver._run(lambda: self._capture(kind)[0])

    def submit(self, kind: str, expected: dict, content: str, *, cancel=None, authorized=lambda: True) -> dict:
        abandoned = threading.Event()
        def permitted() -> bool:
            return not abandoned.is_set() and not getattr(cancel, "cancelled", False) and authorized()
        def action() -> dict:
            wrote = False
            try:
                if not permitted():
                    return {"state": "blocked", "error": "调用已取消或授权已失效"}
                current, handles = self._capture(kind)
                if current["fingerprint"] != expected.get("fingerprint"):
                    return {"state": "blocked", "error": "批准前后页面/账号/目标/输入发生变化"}
                if not permitted():
                    return {"state": "blocked", "error": "调用已取消或授权已失效"}
                wrote = True
                handles["editor"].fill(content, timeout=3000)
                filled, handles = self._capture(kind)
                for key in ("url", "epoch", "account", "target", "account_raw", "target_raw", "editor_key", "submit_key", "profile_hash"):
                    if filled[key] != expected[key]:
                        return {"state": "unknown", "error": "填写后目标身份变化；未继续点击，需核查草稿是否自动保存"}
                if filled["editor_value"] != content or not handles["submit"].is_enabled() or not permitted():
                    return {"state": "unknown", "error": "填写未回读一致、发送不可用或已取消，未继续点击"}
                profile = self.profiles[kind]
                page = handles.pop("page")
                # 在一个 JS 任务内复核保留的控件和字段再触发 UI click，避免重新定位到另一按钮。
                payload = {key: locator.element_handle() for key, locator in handles.items()}
                payload.update({"account_attr": profile.account_attr, "target_attr": profile.target_attr,
                                "account_raw": expected["account_raw"], "target_raw": expected["target_raw"],
                                "content": content, "url": filled["url"], "epoch": filled["epoch"]})
                before = {row["id"] for row in filled["records"]}
                if not permitted():
                    return {"state": "unknown", "error": "填写后授权撤销，未继续发送"}
                clicked = page.evaluate("""p => {
                    const attr=(e,k)=>k==='text'?e.innerText:e.getAttribute(k);
                    if(location.href!==p.url || performance.timeOrigin!==p.epoch ||
                       ![p.account,p.target,p.editor,p.submit].every(e=>e && e.isConnected && e.getClientRects().length) ||
                       attr(p.account,p.account_attr)!==p.account_raw || attr(p.target,p.target_attr)!==p.target_raw ||
                       ('value' in p.editor?p.editor.value:p.editor.textContent)!==p.content || p.submit.disabled ||
                       p.submit.getAttribute('aria-disabled')==='true') return false;
                    p.submit.click(); return true;
                }""", payload)
                if not clicked:
                    return {"state": "unknown", "error": "最终页面身份核验失败，未点击"}
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    observed, _ = self._capture(kind)
                    if any(observed[key] != expected[key] for key in ("url", "epoch", "account", "target", "profile_hash")):
                        return {"state": "unknown", "error": "提交后页面身份变化，无法确认发布结果"}
                    for row in observed["records"]:
                        if row["id"] not in before and row["account"] == expected["account"] and row["content_hash"] == hashlib.sha256(content.encode()).hexdigest():
                            return {"state": "sent", "evidence": {"record_id": row["id"], "url": observed["url"],
                                                                   "account": row["account"], "content_hash": hashlib.sha256(content.encode()).hexdigest()}}
                    if not permitted():
                        break
                    page.wait_for_timeout(100)
                return {"state": "unknown", "error": "已触发一次发送，但未获得新增发布记录；禁止自动重发"}
            except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
                return {"state": "unknown" if wrote else "blocked", "error": f"页面流程停止：{type(exc).__name__}"}
        try:
            return self.driver._run(action)
        except BrowserUnknown:
            abandoned.set()
            return {"state": "unknown", "error": "浏览器等待超时，禁止自动重发"}
        except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
            abandoned.set()
            return {"state": "blocked", "error": f"浏览器拒绝本次请求：{type(exc).__name__}"}
