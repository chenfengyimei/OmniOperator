"""Derived FTS definitions. Applied only by versioned migrations."""

FTS_STATEMENTS = (
    "CREATE VIRTUAL TABLE memory_fts USING fts5(agent_id UNINDEXED, id UNINDEXED, content, content='memory_entries', content_rowid='rowid')",
    "CREATE TRIGGER memory_ai AFTER INSERT ON memory_entries BEGIN INSERT INTO memory_fts(rowid,agent_id,id,content) VALUES(new.rowid,new.agent_id,new.id,new.content); END",
    "CREATE TRIGGER memory_ad AFTER DELETE ON memory_entries BEGIN INSERT INTO memory_fts(memory_fts,rowid,agent_id,id,content) VALUES('delete',old.rowid,old.agent_id,old.id,old.content); END",
    "CREATE TRIGGER memory_au AFTER UPDATE ON memory_entries BEGIN INSERT INTO memory_fts(memory_fts,rowid,agent_id,id,content) VALUES('delete',old.rowid,old.agent_id,old.id,old.content); INSERT INTO memory_fts(rowid,agent_id,id,content) VALUES(new.rowid,new.agent_id,new.id,new.content); END",
)
