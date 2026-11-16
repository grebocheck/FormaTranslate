"""Keep queue, session and checkpoint writers within one application run."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from forma.core.models import FormaError
from forma.persistence.locking import FileLockBusy, file_lock
from forma.settings.config import data_directory


@contextmanager
def application_lock() -> Iterator[None]:
    with ExitStack() as lifetime:
        try:
            lifetime.enter_context(file_lock(data_directory() / "application.lock"))
        except FileLockBusy:
            raise FormaError(
                "Інший екземпляр Forma вже використовує ці дані. Завершіть його перед повторним запуском."
            ) from None
        except OSError:
            raise FormaError(
                "Не вдалося заблокувати каталог даних Forma. Перевірте права доступу та повторіть спробу."
            ) from None
        yield
