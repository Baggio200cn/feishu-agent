"""
飞书日历管理器 — 查看/创建/更新/删除日历事件
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CalendarManager:
    """管理飞书工作日历（calendar.v4 API）"""

    def __init__(self, client):
        self._client = client
        self._primary_calendar_id = None

    def get_primary_calendar_id(self) -> Optional[str]:
        """获取主日历 ID（缓存）"""
        if self._primary_calendar_id:
            return self._primary_calendar_id
        try:
            from lark_oapi.api.calendar.v4 import ListCalendarRequest

            req = ListCalendarRequest.builder().build()
            resp = self._client.calendar.v4.calendar.list(req)
            if not resp.success():
                logger.warning(f"获取日历列表失败: {resp.msg}")
                return None

            for cal in (resp.data.calendar_list or []):
                if cal.type == "primary":
                    self._primary_calendar_id = cal.calendar_id
                    return self._primary_calendar_id
        except Exception as e:
            logger.warning(f"获取主日历异常: {e}")
        return None

    def list_events(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """列出未来 N 天的日历事件"""
        calendar_id = self.get_primary_calendar_id()
        if not calendar_id:
            return []

        try:
            from lark_oapi.api.calendar.v4 import ListCalendarEventRequest

            now = datetime.utcnow()
            # 飞书 Calendar API 要求 Unix 时间戳（秒级字符串）
            start = str(int(now.timestamp()))
            end = str(int((now + timedelta(days=days_ahead)).timestamp()))

            req = (
                ListCalendarEventRequest.builder()
                .calendar_id(calendar_id)
                .start_time(start)
                .end_time(end)
                .build()
            )
            resp = self._client.calendar.v4.calendar_event.list(req)
            if not resp.success():
                logger.warning(f"获取日历事件失败: {resp.msg}")
                return []

            results = []
            for event in (resp.data.items or []):
                results.append({
                    "event_id": event.event_id,
                    "summary": event.summary,
                    "description": event.description,
                    "start_time": event.start_time.timestamp if event.start_time else "",
                    "end_time": event.end_time.timestamp if event.end_time else "",
                    "location": event.location.name if event.location else "",
                    "organizer": event.organizer_calendar_id,
                })
            return results

        except Exception as e:
            logger.warning(f"获取日历事件异常: {e}")
            return []

    def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
    ) -> Optional[str]:
        """
        创建日历事件。
        - start_time / end_time: Unix 时间戳字符串，秒级，如 "1743487200"
        返回 event_id，失败返回 None。
        """
        calendar_id = self.get_primary_calendar_id()
        if not calendar_id:
            return None

        try:
            from lark_oapi.api.calendar.v4 import (
                CreateCalendarEventRequest,
                CreateCalendarEventRequestBody,
                TimeInfo,
            )

            body_builder = (
                CreateCalendarEventRequestBody.builder()
                .summary(summary)
                .description(description)
                .start_time(TimeInfo.builder().timestamp(start_time).build())
                .end_time(TimeInfo.builder().timestamp(end_time).build())
            )
            req = (
                CreateCalendarEventRequest.builder()
                .calendar_id(calendar_id)
                .request_body(body_builder.build())
                .build()
            )
            resp = self._client.calendar.v4.calendar_event.create(req)
            if resp.success():
                event_id = resp.data.event.event_id
                logger.info(f"日历事件已创建: {summary} (id={event_id})")
                return event_id
            logger.warning(f"创建日历事件失败: {resp.msg}")
            return None

        except Exception as e:
            logger.warning(f"创建日历事件异常: {e}")
            return None

    def delete_event(self, event_id: str) -> bool:
        """删除日历事件"""
        calendar_id = self.get_primary_calendar_id()
        if not calendar_id:
            return False

        try:
            from lark_oapi.api.calendar.v4 import DeleteCalendarEventRequest

            req = (
                DeleteCalendarEventRequest.builder()
                .calendar_id(calendar_id)
                .event_id(event_id)
                .build()
            )
            resp = self._client.calendar.v4.calendar_event.delete(req)
            if resp.success():
                logger.info(f"日历事件已删除: {event_id}")
                return True
            logger.warning(f"删除日历事件失败: {resp.msg}")
            return False
        except Exception as e:
            logger.warning(f"删除日历事件异常: {e}")
            return False
