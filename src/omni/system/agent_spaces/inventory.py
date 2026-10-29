"""路径用途索引：纯声明，不扫描磁盘或读取记忆、凭据内容。"""
from __future__ import annotations

from ...config import RuntimePaths


def private_paths(paths: RuntimePaths):
    """按实际位置保护私有数据，防止旧 extra_roots 恰好覆盖新保存目录。"""
    names=('memory','resident','history','state','file_history','cookies','browser_profile',
           'cache','artifacts','logs','command_logs','mcp_logs','backups','config','env',
           'catalog','run','service_logs')
    return [value for name in names if (value:=getattr(paths,name)) is not None] + [paths.data/'plugins']


def storage_inventory(paths: RuntimePaths) -> list[dict[str, str]]:
    """所有位置都来自本次启动的冻结路径，不能用新配置猜测运行位置。"""
    rows = [
        ('workspace', '工作目录', '公开文件', paths.workspace, '项目源码、命令工作目录；不自动纳入数据备份', '按文件策略读写'),
        ('files', '文件', '公开文件', paths.files, '用户文件与整理后的交付文件；纳入备份', '按文件策略读写'),
        ('documents', '资料', '公开文件', paths.documents, '参考文档与附件；纳入备份，不自动送给模型', '人工上传，模型只读'),
        ('memory', '长期记忆与档案', '持久数据', paths.memory, '事实、用户档案、会话消息、摘要、FTS 与向量索引，同一个 SQLite 库', '记忆 API 管理'),
        ('resident', '常驻记忆', '持久数据', paths.resident, '每轮使用的常驻笔记；纳入备份', '常驻记忆 API 管理'),
        ('history', '记忆与技能修订', '持久数据', paths.history, '常驻笔记和技能的版本快照；纳入备份', '版本 API 管理'),
        ('state', '运行状态库', '持久数据', paths.state, '任务、额度、运行账本、产物元数据、迁移记录；纳入备份', '各模块 API 管理'),
        ('skills', '技能库', '持久数据', paths.skills, 'SKILL.md 与参考文件；纳入备份', '技能 API 管理'),
        ('file_history', '文件修订', '持久数据', paths.file_history, '文件编辑回滚副本；纳入备份', '文件 API 管理'),
        ('plugins', '插件数据', '扩展', paths.data / 'plugins', '隔离安装、启用记录与社区配置；含凭据，当前常规备份不包含', '插件 API 管理'),
        ('artifacts', '产物与截图', '产物', paths.artifacts, '按会话登记和限额保留的截图、导出；纳入备份', '产物 API 管理'),
        ('cache', '可再生缓存', '临时数据', paths.cache, '转写等可再生成数据；不纳入备份', '模块管理'),
        ('logs', '诊断日志', '日志', paths.logs, '运行事件与诊断；按日志策略保留', '日志 API 管理'),
        ('command_logs', '后台命令输出', '日志', paths.command_logs, '有界任务输出；纳入备份', '任务 API 管理'),
        ('mcp_logs', 'MCP 日志', '日志', paths.mcp_logs, '有界轮转的服务日志；不纳入备份', 'MCP API 管理'),
        ('backups', '备份', '备份', paths.backups, '显式生成的已校验副本；不递归备份自身', '人工生成与恢复'),
        ('cookies', '站点登录凭据', '私密数据', paths.cookies, 'B 站 Cookie；常规备份不包含', '专用设置管理'),
        ('browser_profile', '浏览器登录态', '私密数据', paths.browser_profile, '持久浏览器配置与登录态；常规备份不包含', '浏览器模块管理'),
        ('config', '应用配置', '固定控制区', paths.config, '共享模型、权限和服务声明；备份默认脱敏', '设置 API 管理'),
        ('env', '模型密钥', '固定控制区', paths.env, '环境密钥文件；常规备份不包含', '专用设置管理'),
        ('catalog', '目录注册表', '固定控制区', paths.catalog, '全局目录与 Agent 空间声明；更改后重启生效', '目录设置管理'),
        ('run', '服务进程身份', '固定控制区', paths.run, 'PID 与进程身份；切换存储根保持固定', '服务管理器'),
        ('service_logs', '服务启动日志', '固定控制区', paths.service_logs, '入口进程日志；切换存储根保持固定', '服务管理器'),
    ]
    return [dict(id=key, label=label, category=category, path=str(path), contents=contents, access=access)
            for key, label, category, path, contents, access in rows if path is not None]
