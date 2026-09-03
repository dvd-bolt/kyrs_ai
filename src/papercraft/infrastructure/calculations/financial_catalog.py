"""Versioned, deliberately small RAS/РСБУ chart-of-accounts reference.

The catalogue is a calculation reference, not legal advice.  It makes every
account used in a reproducible exercise explicit and pins its effective date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Mapping

from .finance import AccountKind


@dataclass(frozen=True, slots=True)
class AccountDefinition:
    code: str
    name: str
    kind: AccountKind
    effective_from: date
    effective_to: date | None = None

    def applies_on(self, value: date) -> bool:
        return value >= self.effective_from and (
            self.effective_to is None or value <= self.effective_to
        )


@dataclass(frozen=True, slots=True)
class RASAccountCatalog:
    version: str
    effective_from: date
    source_note: str
    accounts: Mapping[str, AccountDefinition]

    def get(self, code: str, *, on: date) -> AccountDefinition | None:
        account = self.accounts.get(code)
        return account if account is not None and account.applies_on(on) else None


_EFFECTIVE_FROM = date(2000, 10, 31)
_ACCOUNTS = (
    ("01", "Основные средства", AccountKind.ACTIVE),
    ("02", "Амортизация основных средств", AccountKind.PASSIVE),
    ("04", "Нематериальные активы", AccountKind.ACTIVE),
    ("05", "Амортизация нематериальных активов", AccountKind.PASSIVE),
    ("08", "Вложения во внеоборотные активы", AccountKind.ACTIVE),
    ("10", "Материалы", AccountKind.ACTIVE),
    ("19", "НДС по приобретённым ценностям", AccountKind.ACTIVE),
    ("20", "Основное производство", AccountKind.ACTIVE),
    ("23", "Вспомогательные производства", AccountKind.ACTIVE),
    ("25", "Общепроизводственные расходы", AccountKind.ACTIVE),
    ("26", "Общехозяйственные расходы", AccountKind.ACTIVE),
    ("41", "Товары", AccountKind.ACTIVE),
    ("43", "Готовая продукция", AccountKind.ACTIVE),
    ("44", "Расходы на продажу", AccountKind.ACTIVE),
    ("50", "Касса", AccountKind.ACTIVE),
    ("51", "Расчётные счета", AccountKind.ACTIVE),
    ("52", "Валютные счета", AccountKind.ACTIVE),
    ("57", "Переводы в пути", AccountKind.ACTIVE),
    ("60", "Расчёты с поставщиками и подрядчиками", AccountKind.ACTIVE_PASSIVE),
    ("62", "Расчёты с покупателями и заказчиками", AccountKind.ACTIVE_PASSIVE),
    ("66", "Расчёты по краткосрочным кредитам и займам", AccountKind.PASSIVE),
    ("67", "Расчёты по долгосрочным кредитам и займам", AccountKind.PASSIVE),
    ("68", "Расчёты по налогам и сборам", AccountKind.PASSIVE),
    ("69", "Расчёты по социальному страхованию", AccountKind.PASSIVE),
    ("70", "Расчёты с персоналом по оплате труда", AccountKind.PASSIVE),
    ("71", "Расчёты с подотчётными лицами", AccountKind.ACTIVE_PASSIVE),
    ("73", "Расчёты с персоналом по прочим операциям", AccountKind.ACTIVE_PASSIVE),
    ("75", "Расчёты с учредителями", AccountKind.ACTIVE_PASSIVE),
    ("76", "Расчёты с разными дебиторами и кредиторами", AccountKind.ACTIVE_PASSIVE),
    ("80", "Уставный капитал", AccountKind.PASSIVE),
    ("82", "Резервный капитал", AccountKind.PASSIVE),
    ("84", "Нераспределённая прибыль (непокрытый убыток)", AccountKind.PASSIVE),
    ("90", "Продажи", AccountKind.ACTIVE_PASSIVE),
    ("91", "Прочие доходы и расходы", AccountKind.ACTIVE_PASSIVE),
    ("94", "Недостачи и потери от порчи ценностей", AccountKind.ACTIVE),
    ("99", "Прибыли и убытки", AccountKind.ACTIVE_PASSIVE),
)

RAS_2026 = RASAccountCatalog(
    version="ras-chart-accounts-2026.1",
    effective_from=_EFFECTIVE_FROM,
    source_note="План счетов бухгалтерского учёта: учебный воспроизводимый поднабор.",
    accounts=MappingProxyType(
        {
            code: AccountDefinition(code, name, kind, _EFFECTIVE_FROM)
            for code, name, kind in _ACCOUNTS
        }
    ),
)


__all__ = ["AccountDefinition", "RASAccountCatalog", "RAS_2026"]
