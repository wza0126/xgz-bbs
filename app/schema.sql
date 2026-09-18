-- ============================================================
--  行知教研吧 · 建表脚本
--  SQLite 3 ；时间一律用 UTC 整数时间戳；布尔用 0/1
-- ============================================================

PRAGMA foreign_keys = ON;

-- ---------- 用户 ----------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT    NOT NULL,
    subject       TEXT    NOT NULL DEFAULT '',
    role          TEXT    NOT NULL DEFAULT 'teacher',   -- admin | teacher
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    last_login_at INTEGER
);

-- ---------- 课题组 ----------
CREATE TABLE IF NOT EXISTS boards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT 'project',     -- project | general
    is_public   INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    owner_id    INTEGER NOT NULL REFERENCES users(id),
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_boards_owner ON boards(owner_id);

CREATE TABLE IF NOT EXISTS board_members (
    board_id  INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    role      TEXT    NOT NULL DEFAULT 'member',        -- leader | member
    joined_at INTEGER NOT NULL,
    PRIMARY KEY (board_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_user ON board_members(user_id);

-- ---------- 话题类型（标签）—— 管理员可在后台自助增删 ----------
CREATE TABLE IF NOT EXISTS topic_kinds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,            -- 入库值：topics.kind 存的就是它，创建后不可改
    label       TEXT    NOT NULL,                   -- 界面显示的中文名
    color       TEXT    NOT NULL DEFAULT 'slate',   -- 调色板键，对应 app.css 的 .t-c-<键>
    sort_order  INTEGER NOT NULL DEFAULT 0,         -- 标签栏顺序
    leader_only INTEGER NOT NULL DEFAULT 0,         -- 1 = 仅组长可发布
    pinnable    INTEGER NOT NULL DEFAULT 0,         -- 1 = 发布时可置顶
    is_builtin  INTEGER NOT NULL DEFAULT 0,         -- 1 = 代码里有专门流程，不许删
    is_active   INTEGER NOT NULL DEFAULT 1,         -- 0 = 停用：不出现在标签栏与发布页
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kinds_order ON topic_kinds(sort_order, id);

-- ---------- 话题（讨论 / 公告 / 任务 / …）----------
CREATE TABLE IF NOT EXISTS topics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id       INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    author_id      INTEGER NOT NULL REFERENCES users(id),
    kind           TEXT    NOT NULL DEFAULT 'discussion',
    title          TEXT    NOT NULL,
    body           TEXT    NOT NULL DEFAULT '',
    is_pinned      INTEGER NOT NULL DEFAULT 0,
    is_featured    INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'open',     -- open | closed
    view_count     INTEGER NOT NULL DEFAULT 0,
    reply_count    INTEGER NOT NULL DEFAULT 0,
    last_reply_at  INTEGER,
    last_reply_uid INTEGER,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_board ON topics(board_id, is_pinned DESC, COALESCE(last_reply_at, created_at) DESC);
CREATE INDEX IF NOT EXISTS idx_topics_author ON topics(author_id);

-- ---------- 回复（parent_id 支持楼中楼）----------
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    author_id  INTEGER NOT NULL REFERENCES users(id),
    parent_id  INTEGER REFERENCES posts(id) ON DELETE CASCADE,
    floor_no   INTEGER NOT NULL DEFAULT 0,
    body       TEXT    NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_topic ON posts(topic_id, floor_no);
CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_id);

-- ---------- 任务（话题的 1:1 扩展）----------
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL UNIQUE REFERENCES topics(id) ON DELETE CASCADE,
    board_id   INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL,
    detail     TEXT    NOT NULL DEFAULT '',
    points     INTEGER NOT NULL DEFAULT 0,              -- 满分，0 表示不计分
    due_date   TEXT,                                    -- YYYY-MM-DD
    created_by INTEGER NOT NULL REFERENCES users(id),
    status     TEXT    NOT NULL DEFAULT 'open',         -- open | closed
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_board ON tasks(board_id);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);

-- ---------- 任务 × 人：状态、提交、打分 ----------
CREATE TABLE IF NOT EXISTS task_assignees (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status             TEXT    NOT NULL DEFAULT 'todo', -- todo|doing|done|confirmed|rejected
    submission_post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    submitted_at       INTEGER,
    reviewed_by        INTEGER REFERENCES users(id),
    reviewed_at        INTEGER,
    score              INTEGER,                         -- 组长打分
    review_note        TEXT    NOT NULL DEFAULT '',
    UNIQUE (task_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_assignee_user ON task_assignees(user_id, status);
CREATE INDEX IF NOT EXISTS idx_assignee_task ON task_assignees(task_id);

-- ---------- 附件（多态关联）----------
CREATE TABLE IF NOT EXISTS attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id),
    board_id        INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    attachable_type TEXT    NOT NULL,                   -- topic | post | task_sub
    attachable_id   INTEGER NOT NULL,
    orig_name       TEXT    NOT NULL,
    stored_path     TEXT    NOT NULL,                   -- 相对 uploads/ 的路径
    ext             TEXT    NOT NULL DEFAULT '',
    mime            TEXT    NOT NULL DEFAULT '',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT    NOT NULL DEFAULT '',
    download_count  INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_att_target ON attachments(attachable_type, attachable_id);
CREATE INDEX IF NOT EXISTS idx_att_board ON attachments(board_id);
CREATE INDEX IF NOT EXISTS idx_att_owner ON attachments(owner_id);

-- ---------- 通知 ----------
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    kind       TEXT    NOT NULL,                        -- reply|mention|assign|review|topic
    board_id   INTEGER,
    topic_id   INTEGER,
    task_id    INTEGER,
    summary    TEXT    NOT NULL DEFAULT '',
    link       TEXT    NOT NULL DEFAULT '',
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read, created_at DESC);

-- ---------- 点赞 ----------
CREATE TABLE IF NOT EXISTS reactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT    NOT NULL,
    target_id   INTEGER NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL DEFAULT 'like',
    created_at  INTEGER NOT NULL,
    UNIQUE (target_type, target_id, user_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_reaction_target ON reactions(target_type, target_id);

-- ---------- 杂项设置 ----------
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
