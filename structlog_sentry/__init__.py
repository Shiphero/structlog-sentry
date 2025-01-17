from enum import Enum
import logging
import sys
from typing import List, Optional, Tuple, Union, Set, Iterable

from sentry_sdk import push_scope, capture_event
from sentry_sdk.integrations.logging import ignore_logger as logging_int_ignore_logger
from sentry_sdk.utils import event_from_exception


class Mode(Enum):
    context = "context"
    extra = "extra"


class SentryProcessor:
    """Sentry processor for structlog.

    Uses Sentry SDK to capture events in Sentry.
    """

    def __init__(
        self,
        level: int = logging.WARNING,
        active: bool = True,
        mode: Optional[Mode] = Mode.context,
        tag_keys: Union[List[str], str] = None,
        ignore_loggers: Optional[Iterable[str]] = None,
        ignore_keys: Optional[Iterable[str]] = None,
    ) -> None:
        """
        :param level: events of this or higher levels will be reported to Sentry.
        :param active: a flag to make this processor enabled/disabled.

        :param mode: determines the way to send the `event_dict` to sentry.
            Valid values are `Mode.context' (current default) or `Mode.extra`
            (legacy default, deprecated and limited).
            Otherwhise, only the message will be sent.
        :param tag_keys: a list of keys. If any if these keys appear in `event_dict`,
            the key and its corresponding value in `event_dict` will be used as Sentry
            event tags. use `"__all__"` to report all key/value pairs of event as tags.
        :param ignore_loggers: a list of logger names to ignore any events from.
        :param ignore_keys: a list of keys to ignore from the original event dictionary, if they exist.
        """
        self.level = level
        self.active = active
        self.tag_keys = tag_keys
        self._mode = mode
        self._original_event_dict = None
        self._ignored_loggers: Set[str] = set()
        if ignore_loggers is not None:
            self._ignored_loggers.update(set(ignore_loggers))
        self._ignore_keys = ignore_keys or []

    @staticmethod
    def _get_logger_name(logger, event_dict: dict) -> Optional[str]:
        """Get logger name from event_dict with a fallbacks to logger.name and record.name

        :param logger: logger instance
        :param event_dict: structlog event_dict
        """
        record = event_dict.get("_record")
        l_name = event_dict.get("logger")
        logger_name = None

        if l_name:
            logger_name = l_name
        elif record:
            logger_name = record.name

        if not logger_name and logger:
            logger_name = logger.name

        return logger_name

    def _get_event_context_and_hint(self, event_dict: dict) -> Tuple[dict, dict, Optional[str]]:
        """Create a sentry event and hint from structlog `event_dict` and sys.exc_info.

        :param event_dict: structlog event_dict
        """
        exc_info = event_dict.get("exc_info", False)
        if exc_info is True:
            # logger.exception() or logger.error(exc_info=True)
            exc_info = sys.exc_info()
        has_exc_info = exc_info and exc_info != (None, None, None)

        if has_exc_info:
            event, hint = event_from_exception(exc_info)
        else:
            event, hint = {}, None

        context = {}

        event["message"] = event_dict.get("event")
        event["level"] = event_dict.get("level")
        if "logger" in event_dict:
            event["logger"] = event_dict["logger"]

        if self._mode == Mode.context:
            context = self._filtered_event_dict.copy()

        elif self._mode == Mode.extra:
            event["extra"] = self._filtered_event_dict.copy()

        if self.tag_keys == "__all__":
            event["tags"] = self._original_event_dict.copy()
        elif isinstance(self.tag_keys, list):
            event["tags"] = {
                key: event_dict[key] for key in self.tag_keys if key in event_dict
            }

        return event, context, hint

    def _log(self, event_dict: dict) -> str:
        """Send an event to Sentry and return sentry event id.

        :param event_dict: structlog event_dict
        """
        event, context, hint = self._get_event_context_and_hint(event_dict)
        if context:
            with push_scope() as scope:
                scope.set_context("Log event data", context)
                event_id = capture_event(event, hint=hint)
        else:
            event_id = capture_event(event, hint=hint)

        return event_id

    def __call__(self, logger, method, event_dict) -> dict:
        """A middleware to process structlog `event_dict` and send it to Sentry."""
        logger_name = self._get_logger_name(logger=logger, event_dict=event_dict)
        if logger_name in self._ignored_loggers:
            event_dict["sentry"] = "ignored"
            return event_dict

        self._original_event_dict = event_dict
        self._filtered_event_dict = {k: v for k, v in event_dict.items() if k not in self._ignore_keys}
        sentry_skip = event_dict.pop("sentry_skip", False)
        do_log = getattr(logging, event_dict["level"].upper()) >= self.level

        if sentry_skip or not self.active or not do_log:
            event_dict["sentry"] = "skipped"
            return event_dict

        sid = self._log(event_dict)
        event_dict["sentry"] = "sent"
        event_dict["sentry_id"] = sid

        return event_dict


class SentryJsonProcessor(SentryProcessor):
    """Sentry processor for structlog which uses JSONRenderer.

    Uses Sentry SDK to capture events in Sentry.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # A set of all encountered structured logger names. If an application uses
        # multiple loggers with different names (eg. different qualnames), then each of
        # those loggers needs to be ignored in Sentry's logging integration so that this
        # processor will be the only thing reporting the events.
        self._ignored = set()

    def __call__(self, logger, method, event_dict) -> dict:
        self._ignore_logger(logger, event_dict)
        return super().__call__(logger, method, event_dict)

    def _ignore_logger(self, logger, event_dict: dict) -> None:
        """Tell Sentry to ignore logger, if we haven't already.

        This is temporary workaround to prevent duplication of a JSON event in Sentry.

        :param logger: logger instance
        :param event_dict: structlog event_dict
        """
        logger_name = self._get_logger_name(logger=logger, event_dict=event_dict)

        if not logger_name:
            raise Exception("Cannot ignore logger without a name.")

        if logger_name not in self._ignored:
            logging_int_ignore_logger(logger_name)
            self._ignored.add(logger_name)
