"""
utils/keep_alive.py
===================
Keep-alive per il piano free di Render.

Render sospende i Web Service free dopo ~15 minuti senza traffico HTTP in
ingresso, fermando anche il loop del bot. Questo task chiama periodicamente
l'URL pubblico del servizio stesso (`RENDER_EXTERNAL_URL/health`): la
richiesta esce su Internet e rientra dal load balancer di Render, quindi
conta come traffico e il servizio resta attivo.

Come rete di sicurezza conviene comunque configurare un ping esterno
gratuito (es. cron-job.org o UptimeRobot) sullo stesso URL.
"""

from __future__ import annotations

import asyncio
import urllib.request

from utils.logger import get_logger

logger = get_logger(__name__)


def _ping(url: str, timeout: float = 15.0) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "QuantTradingSystem-keepalive"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


async def keep_alive_loop(base_url: str, interval_seconds: int = 600) -> None:
    """Esegue un GET su `<base_url>/health` ogni `interval_seconds`."""
    url = base_url.rstrip("/") + "/health"
    logger.info("Keep-alive attivo: ping a %s ogni %ss.", url, interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            status = await asyncio.to_thread(_ping, url)
            logger.debug("Keep-alive %s -> HTTP %s", url, status)
        except Exception as exc:  # rete o servizio momentaneamente giù
            logger.warning("Keep-alive fallito (%s): riprovo al prossimo giro.", exc)
