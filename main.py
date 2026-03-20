from __future__ import annotations

from typing import Any, Dict

import httpx
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register(
    "wutong-family",
    "pjh456",
    "wutong-family QQ 自然语言查询插件",
    "0.1.1",
    "https://github.com/pjh456/astrbot_plugin_wutong_family",
)
class WutongFamilyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.http = httpx.AsyncClient(timeout=self.config.get("timeout", 30))
        self.session_cache: Dict[str, int] = {}
        self.pending_cache: Dict[str, Dict[str, str]] = {}

    def _get_base_url(self) -> str:
        base_url = self.config.get("base_url", "http://127.0.0.1:8000")
        return str(base_url).rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        token = self.config.get("api_token", "")
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    def _get_sender_key(self, event: AstrMessageEvent) -> str:
        # Try multiple APIs to stay compatible across AstrBot versions
        for attr in ("get_sender_id", "sender_id", "user_id"):
            value = getattr(event, attr, None)
            if callable(value):
                try:
                    return str(value())
                except Exception:
                    pass
            elif value is not None:
                return str(value)

        # Try to include group/channel context if available
        for attr in ("get_group_id", "group_id"):
            value = getattr(event, attr, None)
            if callable(value):
                try:
                    return f"group:{value()}"
                except Exception:
                    pass
            elif value is not None:
                return f"group:{value}"

        # Fallback to sender name
        for attr in ("get_sender_name", "get_sender_nick", "sender_name"):
            value = getattr(event, attr, None)
            if callable(value):
                try:
                    return str(value())
                except Exception:
                    pass
            elif value is not None:
                return str(value)

        return "unknown"

    async def _get_session_id(self, user_key: str) -> int:
        if user_key in self.session_cache:
            return self.session_cache[user_key]

        base_url = self._get_base_url()
        headers = self._get_headers()

        resp = await self.http.post(
            f"{base_url}/api/chat/sessions/",
            json={"title": f"qq:{user_key}"},
            headers=headers,
        )
        resp.raise_for_status()
        session_id = resp.json().get("id")
        if not session_id:
            raise ValueError("Failed to create chat session")
        self.session_cache[user_key] = int(session_id)
        return int(session_id)

    def _format_preview(self, data: Any, max_rows: int) -> str:
        if not data:
            return ""
        if isinstance(data, list):
            preview = data[:max_rows]
            return f"前{len(preview)}条:\n{preview}"
        return str(data)

    def _format_result(self, payload: Dict[str, Any]) -> str:
        max_rows = int(self.config.get("preview_rows", 3))

        if "assistant_message" in payload:
            msg = payload.get("assistant_message", {}).get("content", "")
            qr = payload.get("query_result")
            if qr and qr.get("pending_confirmation"):
                plan = qr.get("execution_plan") or msg
                return (
                    f"{plan}\n\n"
                    "如需执行，请回复：/执行\n"
                    "如需修改/拒绝，请回复：/拒绝 你的修改意见"
                ).strip()
            if qr and qr.get("success") and qr.get("data"):
                preview = self._format_preview(qr.get("data"), max_rows)
                return f"{msg}\n{preview}".strip()
            return msg or "查询完成。"

        if payload.get("success"):
            preview = self._format_preview(payload.get("data"), max_rows)
            if preview:
                return f"查询成功，返回 {payload.get('count', 0)} 条。\n{preview}".strip()
            return payload.get("response") or "查询成功。"

        return payload.get("response") or payload.get("error") or "查询失败"

    @filter.command("查")
    async def query(self, event: AstrMessageEvent, message: str = ""):
        # Prefer raw message to avoid framework truncation
        text = getattr(event, "message_str", "") or ""
        text = text.replace("\u3000", " ")  # normalize full-width spaces
        query = ""
        if text.startswith("/查"):
            query = text[2:].strip()
        elif text.startswith("查"):
            query = text[1:].strip()
        if not query:
            query = (message or "").strip()
        if not query:
            yield event.plain_result("用法：/查 统计各区域用户数量")
            return

        base_url = self._get_base_url()
        headers = self._get_headers()
        mode = str(self.config.get("mode", "session")).lower()

        try:
            if mode == "data":
                resp = await self.http.post(
                    f"{base_url}/api/data/natural_language/",
                    json={"query": query, "save_history": True},
                    headers=headers,
                )
                resp.raise_for_status()
                payload = resp.json()
            else:
                user_key = self._get_sender_key(event)
                session_id = await self._get_session_id(user_key)
                resp = await self.http.post(
                    f"{base_url}/api/chat/sessions/{session_id}/send_message/",
                    json={"message": query, "stream": False},
                    headers=headers,
                )
                resp.raise_for_status()
                payload = resp.json()

            # cache pending execution if present
            qr = payload.get("query_result") if isinstance(payload, dict) else None
            if qr and qr.get("pending_confirmation") and user_key:
                self.pending_cache[user_key] = {
                    "action_type": qr.get("action_type", "sql"),
                    "code": qr.get("code") or qr.get("sql") or "",
                    "original_query": query,
                }

            yield event.plain_result(self._format_result(payload))
        except Exception as exc:
            logger.exception("wutong-family query failed")
            yield event.plain_result(f"请求失败：{exc}")

    @filter.command("执行")
    async def execute(self, event: AstrMessageEvent, message: str = ""):
        user_key = self._get_sender_key(event)
        pending = self.pending_cache.get(user_key)
        if not pending:
            yield event.plain_result("没有待执行的操作，请先用 /查 提交请求。")
            return

        base_url = self._get_base_url()
        headers = self._get_headers()
        session_id = await self._get_session_id(user_key)

        try:
            resp = await self.http.post(
                f"{base_url}/api/chat/sessions/{session_id}/execute_sql/",
                json={
                    "action_type": pending.get("action_type", "sql"),
                    "code": pending.get("code", ""),
                    "original_query": pending.get("original_query", ""),
                },
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
            # clear pending on success
            if payload.get("success"):
                self.pending_cache.pop(user_key, None)
            yield event.plain_result(self._format_result(payload))
        except Exception as exc:
            logger.exception("wutong-family execute failed")
            yield event.plain_result(f"执行失败：{exc}")

    @filter.command("拒绝")
    async def reject(self, event: AstrMessageEvent, message: str = ""):
        user_key = self._get_sender_key(event)
        pending = self.pending_cache.get(user_key)
        if not pending:
            yield event.plain_result("没有待执行的操作。")
            return

        instruction = (message or "").strip()
        base_url = self._get_base_url()
        headers = self._get_headers()
        session_id = await self._get_session_id(user_key)
        try:
            resp = await self.http.post(
                f"{base_url}/api/chat/sessions/{session_id}/execute_sql/",
                json={
                    "action_type": pending.get("action_type", "sql"),
                    "code": pending.get("code", ""),
                    "original_query": pending.get("original_query", ""),
                    "action": "reject",
                    "instruction": instruction,
                },
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
            self.pending_cache.pop(user_key, None)
            yield event.plain_result(self._format_result(payload))
        except Exception as exc:
            logger.exception("wutong-family reject failed")
            yield event.plain_result(f"拒绝失败：{exc}")
    async def terminate(self):
        await self.http.aclose()
