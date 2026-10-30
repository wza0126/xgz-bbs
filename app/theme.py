# -*- coding: utf-8 -*-
"""外观主题：当前生效主题的读取与切换。

全站只有一个开关（settings 表里的 'theme' 键），管理员在后台统一切换、所有人生效。
别在别处直接读写 settings 表 —— 唯一入口就是这里。

为什么不做「每人各选各的」：主题统一才有视觉一致性，也避免管理员改了半天
别人页面纹丝不动。真要做成个人偏好时，把 key 改成 user_id 就行，结构不用动。
"""
from flask import g

import config as cfg
from . import db as dbm

SETTING_KEY = "theme"


def _normalize(key: str | None) -> str:
    """只认 config 里登记过的 key；空值/脏值一律回落到默认主题。"""
    return key if key in cfg.THEME_KEYS else cfg.DEFAULT_THEME


def current() -> str:
    """当前生效主题的 key。同一请求内只查一次库（存 g）。"""
    if not hasattr(g, "_theme"):
        try:
            g._theme = _normalize(dbm.scalar(
                "SELECT value FROM settings WHERE key = ?", (SETTING_KEY,), ""))
        except Exception:  # noqa: BLE001 —— settings 表没建好也不能让页面挂掉
            g._theme = cfg.DEFAULT_THEME
    return g._theme


def save(key: str) -> str:
    """切换主题（管理员操作）。非法 key 静默回落到默认值，返回最终落库的 key。"""
    key = _normalize(key)
    dbm.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SETTING_KEY, key))
    g._theme = key
    return key


def catalog() -> list[dict]:
    """给后台「外观」页用的清单：每套主题 + active 标记。"""
    cur = current()
    return [dict(t, active=(t["key"] == cur)) for t in cfg.THEMES]
