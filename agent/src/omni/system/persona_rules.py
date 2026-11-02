"""人格保存契约的判定规则（判定逻辑，不抄 surfcore 的形态）。

背景（学习自 surfcore 的 `docs/cases/2026-10-31-persona-tone-overwrites-core.md`）：
那个项目做的是多层人格（core/tone/daily/scene），踩的坑是「保存全部」时服务端
`layerPaths()` 少返回了一层，客户端把空值也发过去，于是语气层的内容写进了核心文件、
或者核心被清空。他们的修法里有三条**判定逻辑**可以直接迁移：

1. **空值语义必须显式**：协议里要区分「清空」与「不存在」。他们的教训是
   「默认分支写主文件」——未知/空层落到了默认分支，把别的层覆盖掉了。
2. **每层一个显式分支**，不给「未知的层」兜底路径。
3. **核心内容有最小长度闸门**（他们用 50 字）与**敏感内容拒绝保存**。

我们只有一层 `persona.system_prompt`，但同一个洞照样存在：
`config_io.apply_updates` 对 multiline 键只校验「是字符串列表」，空列表 `[]` 照样通过，
于是 `_replace_multiline_block` 把整块人格正文替换成空——**用户保存后人格凭空消失**，
而前端还会提示「已保存」。更隐蔽的是 `persona.name` 与 `system_prompt` 分属两个键，
前端只预检了 name 非空，system_prompt 可以是空的。

所以这里给出三条判定：
- **正文非空**：空/纯空白一律拒绝，不区分「清空」与「不存在」——想恢复默认请手填回去；
- **最小长度**：低于阈值的正文视为误操作（他们用 50 字），避免「只留一句话」把人设削没了；
- **敏感内容拒绝**：正文里出现密钥/密码这类东西一律拒绝保存（他们也有这条）。

**判定顺序**：空 → 凭据 → 长度。凭据排在长度前面是刻意的——一段 16 字的正文里
藏着密码时报「太短」是误导用户（他会去补字而不是删凭据），凭据是更危险的那件事。

**凭据检测在 `system/text_guard.py`**（共享原语，单一真源，与记忆写入闸门共用）。
第一版这里自己维护了一份正则表，结果漏掉「密码是」与 Cookie 两类——两份表必然漂移，
漂移只会让某一处比另一处宽松。所以这里只保留**人格侧的策略与话术**，检测交给共享层。

**为什么不做「备份」**：那是形态不是判定。他们用「保存前先备份 + 保留 5 份」兜底，
我们这边 config.yaml 已经有 `commit_config_updates` 的 FileLock + 原子写，
而且 `system/backup.py` 另有整套运行时备份。人事先加一套人格专用备份目录，
收益低于它带来的目录结构复杂度——真要兜底，走 `omni.system.backup` 统一入口即可。
"""

from __future__ import annotations

from .text_guard import scan_sensitive

# 人格正文的最小长度闸门：低于这个长度视为误操作（抄 surfcore 的 core 层 50 字）。
# 20 是按我们自己的模板定的——config.example.yaml 里的默认人格是 799 字，
# 但用户完全可能只写「你是小鱼，简洁点」这么一句；20 字足够表达一个身份，
# 又不至于把「只留一句话」和「误清空」混为一谈。
PERSONA_PROMPT_MIN_CHARS = 20

# 人格正文的最大长度：防止把整本日志塞进人设（每次对话都要带上它）。
PERSONA_PROMPT_MAX_CHARS = 20000

# 人格名字的最大长度：与社区人设卡的 MAX_NAME 保持一致。
PERSONA_NAME_MAX_CHARS = 30

# 场景层（群聊/私聊）的最大长度：它每轮都跟着提示词走，比核心层更该克制。
PERSONA_SCENE_MAX_CHARS = 4000

# 场景层的最小长度闸门：低于这个长度视为误操作（与核心层同一条纪律）。
# 空场景层是**合法**的——那表示"没有这一层"，与核心层的"空=拒绝"不同。
PERSONA_SCENE_MIN_CHARS = 10


class PersonaRuleError(ValueError):
    """人格保存被规则拒绝（消息可直接展示给用户）。"""


def check_persona_name(name: str) -> None:
    """名字：非空、不超长。"""
    text = str(name or "").strip()
    if not text:
        raise PersonaRuleError("「它叫什么」不能空着")
    if len(text) > PERSONA_NAME_MAX_CHARS:
        raise PersonaRuleError(f"名字太长了（{len(text)} 字，上限 {PERSONA_NAME_MAX_CHARS} 字）")


def check_persona_prompt(lines: list[str] | str) -> None:
    """正文：非空、不含凭据、够长、不超长。

    `lines` 是工作台提交的行列表（multiline 键的原始形态），
    也可以直接给字符串（社区人设卡导入等其它入口）。

    判定顺序是 空 → 凭据 → 长度：凭据排在长度前面是刻意的——一段 16 字的正文里
    藏着密码时报「太短」是误导用户（他会去补字而不是删凭据），凭据才是更危险的那件事。
    """
    if isinstance(lines, str):
        text = lines
    else:
        text = "\n".join(str(item) for item in (lines or []))
    body = text.strip()
    if not body:
        # 关键：空正文**不区分**「清空」与「不存在」——两种都拒绝。
        # 放行了就等于「保存全部」时把人设正文悄悄清空，而界面还在说「已保存」。
        raise PersonaRuleError("人格正文不能是空的：想换回默认人设，请把默认内容贴回去再保存")
    # 凭据检测走共享原语（与记忆写入闸门同一份正则表）：
    # 人设会跟着每一次对话进提示词，等于把凭据广播给模型供应商。
    hits = scan_sensitive(body)
    if hits:
        raise PersonaRuleError(
            f"人格正文里出现了疑似{hits[0].label}：人设会跟着每次对话进入提示词，凭据不能写在这里。请删掉这一段再保存"
        )
    if len(body) < PERSONA_PROMPT_MIN_CHARS:
        raise PersonaRuleError(
            f"人格正文太短了（{len(body)} 字，至少 {PERSONA_PROMPT_MIN_CHARS} 字）："
            "这么短的内容说明身份、说话方式或规则都没写全"
        )
    if len(body) > PERSONA_PROMPT_MAX_CHARS:
        raise PersonaRuleError(f"人格正文太长了（{len(body)} 字，上限 {PERSONA_PROMPT_MAX_CHARS} 字）")


def check_persona_scene(lines: list[str] | str, *, label: str) -> None:
    """场景层：空 = 合法（没有这一层）；非空则要过凭据与长度闸门。

    **与核心层的规则不同，差别在哪、为什么**：
    - 核心层空 → **拒绝**。因为核心层是底座，把它清空等于「保存全部」时
      把人设悄悄清掉，而界面还在说「已保存」（surfcore 的真实事故）。
    - 场景层空 → **允许**。场景层是可选的追加段，"没有这一层"是它的
      正常状态之一，不存在"把场景层清空"与"场景层不存在"的二义——
      两种情况的行为完全一样（不追加）。

    凭据检测对两层是同一套规则：场景层同样每轮进提示词，凭据一样会广播出去。
    """
    if isinstance(lines, str):
        text = lines
    else:
        text = "\n".join(str(item) for item in (lines or []))
    body = text.strip()
    if not body:
        return  # 空场景层 = 没有这一层，合法
    hits = scan_sensitive(body)
    if hits:
        raise PersonaRuleError(
            f"{label}里出现了疑似{hits[0].label}：人设会跟着每次对话进入提示词，凭据不能写在这里。请删掉这一段再保存"
        )
    if len(body) < PERSONA_SCENE_MIN_CHARS:
        raise PersonaRuleError(
            f"{label}太短了（{len(body)} 字，至少 {PERSONA_SCENE_MIN_CHARS} 字）："
            "这么短的内容说明没写清这个场景下该怎么说"
        )
    if len(body) > PERSONA_SCENE_MAX_CHARS:
        raise PersonaRuleError(f"{label}太长了（{len(body)} 字，上限 {PERSONA_SCENE_MAX_CHARS} 字）")


def validate_persona_update(updates: dict) -> None:
    """校验一次「人格」段的更新；有违规就抛 PersonaRuleError。

    只在这次更新**真的动到**人格键时才校验——保存别的段（比如模型、QQ）
    不该被人格的规则挡住。
    """
    persona = updates.get("persona")
    if not isinstance(persona, dict):
        return
    if "name" in persona:
        check_persona_name(persona["name"])
    if "system_prompt" in persona:
        value = persona["system_prompt"]
        if not isinstance(value, list):
            raise PersonaRuleError("人格正文需要按行提交（字符串列表）")
        check_persona_prompt(value)
    for key, label in (("group_prompt", "群聊场景层"), ("private_prompt", "私聊场景层")):
        if key not in persona:
            continue
        value = persona[key]
        if not isinstance(value, list):
            raise PersonaRuleError(f"{label}需要按行提交（字符串列表）")
        check_persona_scene(value, label=label)
