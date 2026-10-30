"""后台命令账本的迁移声明；不连接数据库、不启动进程。"""
COMMAND_JOBS_SCHEMA = """
CREATE TABLE command_jobs (
    job_id TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    owner_identity TEXT NOT NULL,
    command TEXT NOT NULL,
    shell TEXT NOT NULL,
    cwd TEXT NOT NULL,
    timeout_s REAL NOT NULL,
    status TEXT NOT NULL,
    process_identity TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    side_effect_state TEXT NOT NULL DEFAULT 'none',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    error_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX command_jobs_session ON command_jobs(session_id, created_at);
"""
