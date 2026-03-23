"""
reaper/auth/rotator.py
======================
Estrategia de rotación inteligente de cuentas para distribuir la carga de peticiones.

El ``AccountRotator`` selecciona la cuenta más adecuada para una petición evaluando
múltiples factores de salud. El objetivo es distribuir la carga de forma equitativa
y priorizar las cuentas con mejor historial.

Algoritmo de puntuación (``_score``):
    score = tasa_de_éxito * WEIGHT_SUCCESS_RATE
          + antigüedad_de_uso * WEIGHT_LEAST_RECENTLY_USED
          - penalización_por_umbral_de_refresco * WEIGHT_COOKIE_AGE

    Donde:
    - ``tasa_de_éxito``:  porcentaje de éxitos históricos (0.0–1.0).
    - ``antigüedad_de_uso``: normalizada entre cuentas del pool; mayor puntaje
      para cuentas usadas hace más tiempo (o nunca usadas).
    - ``penalización_por_umbral_de_refresco``: aumenta cuando la cuenta se
      acerca al umbral de refresco de cookies, incentivando usar otras antes.

Estados no seleccionables (filtrados antes de puntuar):
    - ``AccountStatus.SUSPENDED``
    - ``AccountStatus.COOKIE_EXPIRED``

Estado con prioridad reducida (penalizado en score):
    - ``AccountStatus.RATE_LIMITED``   → score * RATE_LIMITED_PENALTY
    - ``AccountStatus.NEEDS_REFRESH``  → score * NEEDS_REFRESH_PENALTY

Ejemplo::

    from reaper.auth.rotator import AccountRotator
    from reaper.auth.models import AccountProfile

    rotator = AccountRotator(cookie_refresh_threshold=50)
    best = rotator.select(accounts_pool, platform="facebook")
    if best:
        print(f"Usando cuenta: {best.username}")
    else:
        print("No hay cuentas disponibles para facebook")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import NamedTuple

from reaper.auth.models import AccountProfile, AccountStatus, NON_SELECTABLE_STATUSES

logger = logging.getLogger("reaper.auth.rotator")

# ─────────────────────────────────────────────────────────────────────────────
# Pesos del algoritmo de puntuación
# ─────────────────────────────────────────────────────────────────────────────

# Peso de la tasa de éxito histórica en el score final.
WEIGHT_SUCCESS_RATE: float = 0.6

# Peso de la antigüedad de uso (favorece cuentas poco usadas recientemente).
WEIGHT_LEAST_RECENTLY_USED: float = 0.3

# Peso negativo del envejecimiento de cookies (desincentiva cuentas próximas al umbral).
WEIGHT_COOKIE_AGE: float = 0.1

# Multiplicador de penalización para cuentas con rate-limit activo.
RATE_LIMITED_PENALTY: float = 0.2

# Multiplicador de penalización para cuentas que necesitan refresco de cookies.
NEEDS_REFRESH_PENALTY: float = 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Tipos internos
# ─────────────────────────────────────────────────────────────────────────────


class ScoredAccount(NamedTuple):
    """Par (score, cuenta) usado en el ranking interno."""

    score: float
    account: AccountProfile


# ─────────────────────────────────────────────────────────────────────────────
# Rotador
# ─────────────────────────────────────────────────────────────────────────────


class AccountRotator:
    """Selecciona la cuenta óptima para la siguiente petición.

    El rotador es stateless: no almacena estado entre llamadas.
    Recibe el pool completo de cuentas en cada llamada a ``select()``
    y calcula el ranking en el momento.

    Esta decisión de diseño simplifica la integración: ``AccountManager``
    llama a ``select()`` pasando las cuentas actualizadas sin que el rotador
    necesite ser notificado de cambios.

    Args:
        cookie_refresh_threshold: Número de peticiones exitosas tras el cual
            una cuenta se considera "próxima a necesitar refresco". Se usa
            para penalizar su score y dar preferencia a otras cuentas.
            Debe coincidir con el valor configurado en ``AccountManager``.
    """

    def __init__(self, cookie_refresh_threshold: int = 50) -> None:
        self.cookie_refresh_threshold = cookie_refresh_threshold

    def select(
        self,
        accounts: list[AccountProfile],
        platform: str,
    ) -> AccountProfile | None:
        """Selecciona la cuenta con mayor score para la plataforma indicada.

        Proceso:
        1. Filtra cuentas de la plataforma correcta.
        2. Filtra cuentas no seleccionables (``NON_SELECTABLE_STATUSES`` o sin cookies).
        3. Puntúa las candidatas.
        4. Retorna la de mayor score.

        Args:
            accounts: Pool completo de cuentas (todas las plataformas).
            platform: ``"facebook"`` o ``"instagram"``.

        Returns:
            La ``AccountProfile`` con mejor score, o ``None`` si no hay
            candidatas disponibles.
        """
        # ── 1. Filtrar por plataforma y seleccionabilidad ─────────────────────
        candidates = [
            acc for acc in accounts
            if acc.platform == platform and acc.is_selectable
        ]

        if not candidates:
            logger.warning(
                "No hay cuentas disponibles para plataforma=%s "
                "(total_en_pool=%d)",
                platform,
                len(accounts),
            )
            return None

        # ── 2. Puntuar y ordenar ──────────────────────────────────────────────
        scored = self._rank(candidates)

        best = scored[0].account
        logger.debug(
            "Cuenta seleccionada | username=%s | score=%.3f | "
            "success_rate=%.1f%% | peticiones=%d",
            best.username,
            scored[0].score,
            best.activity.success_rate * 100,
            best.activity.total_requests,
        )

        if logger.isEnabledFor(logging.DEBUG) and len(scored) > 1:
            logger.debug(
                "Ranking completo (%d candidatas): %s",
                len(scored),
                [f"{s.account.username}={s.score:.3f}" for s in scored[:5]],
            )

        return best

    def rank_all(
        self,
        accounts: list[AccountProfile],
        platform: str,
    ) -> list[ScoredAccount]:
        """Retorna el ranking completo de candidatas para diagnóstico.

        A diferencia de ``select()``, no filtra por estado no seleccionable:
        retorna todas las cuentas de la plataforma con su score calculado.
        Útil para mostrar el estado del pool al operador.

        Args:
            accounts: Pool completo de cuentas.
            platform: ``"facebook"`` o ``"instagram"``.

        Returns:
            Lista de ``ScoredAccount`` ordenada de mayor a menor score.
        """
        platform_accounts = [acc for acc in accounts if acc.platform == platform]
        return self._rank(platform_accounts)

    # ── Lógica interna de scoring ─────────────────────────────────────────────

    def _rank(self, candidates: list[AccountProfile]) -> list[ScoredAccount]:
        """Calcula el score de cada candidata y retorna la lista ordenada."""
        if not candidates:
            return []

        # Calcular antigüedades de uso para normalizar entre candidatas.
        # Cuentas nunca usadas obtienen la mayor antigüedad posible.
        lru_scores = self._compute_lru_scores(candidates)

        scored: list[ScoredAccount] = []
        for account in candidates:
            score = self._score(account, lru_scores[account.account_id])
            scored.append(ScoredAccount(score=score, account=account))

        # Ordenar de mayor a menor score.
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    def _score(self, account: AccountProfile, lru_score: float) -> float:
        """Calcula el score de salud de una cuenta entre 0.0 y 1.0 (aprox).

        Formula:
            score = (success_rate * W_SUCCESS)
                  + (lru_score * W_LRU)
                  - (cookie_age_ratio * W_COOKIE_AGE)

        Luego aplica penalizaciones multiplicativas por estado.

        Args:
            account:   Perfil de la cuenta a puntuar.
            lru_score: Puntuación de antigüedad de uso normalizada (0.0–1.0).

        Returns:
            Score final. Valores más altos → cuenta más preferida.
        """
        activity = account.activity

        # ── Componente 1: tasa de éxito histórica ────────────────────────────
        success_component = activity.success_rate * WEIGHT_SUCCESS_RATE

        # ── Componente 2: antigüedad de uso (favorece las menos usadas) ──────
        lru_component = lru_score * WEIGHT_LEAST_RECENTLY_USED

        # ── Componente 3: antigüedad de cookies (penaliza proximidad al umbral)
        if self.cookie_refresh_threshold > 0:
            cookie_age_ratio = min(
                activity.requests_since_cookie_refresh / self.cookie_refresh_threshold,
                1.0,
            )
        else:
            cookie_age_ratio = 0.0

        cookie_component = cookie_age_ratio * WEIGHT_COOKIE_AGE

        raw_score = success_component + lru_component - cookie_component

        # ── Penalizaciones por estado ─────────────────────────────────────────
        if account.status == AccountStatus.RATE_LIMITED:
            raw_score *= RATE_LIMITED_PENALTY
        elif account.status == AccountStatus.NEEDS_REFRESH:
            raw_score *= NEEDS_REFRESH_PENALTY

        return max(0.0, raw_score)

    @staticmethod
    def _compute_lru_scores(
        candidates: list[AccountProfile],
    ) -> dict[str, float]:
        """Asigna una puntuación de antigüedad de uso normalizada a cada cuenta.

        Las cuentas nunca usadas obtienen score 1.0 (máximo).
        La cuenta usada más recientemente obtiene score 0.0 (mínimo).
        El resto se normaliza linealmente entre ambos extremos.

        Returns:
            Dict ``{account_id: lru_score}`` con valores entre 0.0 y 1.0.
        """
        now = datetime.now(tz=timezone.utc)

        # Calcular antigüedad en segundos para cada cuenta.
        ages: dict[str, float] = {}
        for acc in candidates:
            if acc.activity.last_used_at is None:
                # Nunca usada → antigüedad máxima (año fake muy grande).
                ages[acc.account_id] = float("inf")
            else:
                try:
                    last = datetime.fromisoformat(acc.activity.last_used_at)
                    ages[acc.account_id] = (now - last).total_seconds()
                except (ValueError, TypeError):
                    ages[acc.account_id] = 0.0

        # Reemplazar infinitos con el máximo finito + 1 para normalización.
        finite_ages = [v for v in ages.values() if v != float("inf")]
        max_age = max(finite_ages, default=0.0)

        for acc_id, age in ages.items():
            if age == float("inf"):
                ages[acc_id] = max_age + 1.0

        # Normalizar: máxima antigüedad → 1.0, cero antigüedad → 0.0.
        max_age_with_inf = max(ages.values(), default=1.0)
        if max_age_with_inf == 0.0:
            # Todas fueron usadas en el mismo instante: score igual para todas.
            return {acc_id: 0.5 for acc_id in ages}

        return {
            acc_id: age / max_age_with_inf
            for acc_id, age in ages.items()
        }
