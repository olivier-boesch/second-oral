"""
Flask-SSE — Server-Sent Events pour Flask via Redis pub/sub.

Améliorations par rapport à l'original :
  - Python 3 uniquement (suppression de la dépendance `six`)
  - Connexion Redis mise en cache par URL (évite une nouvelle connexion à
    chaque appel de publish)
  - Support des canaux nommés (?channel=nom dans l'URL de stream)
"""
import json
from collections import OrderedDict

from flask import Blueprint, request, current_app, stream_with_context
from redis import StrictRedis
from redis.exceptions import ConnectionError

__version__ = '1.1.0'

# Deux pools Redis distincts par URL :
#   - _redis_pub   : pour publish() — timeout court acceptable
#   - _redis_sub   : pour pubsub.listen() — DOIT être sans timeout
#     (gevent lève TimeoutError si socket_timeout est non-None et qu'aucun
#      message n'arrive avant l'expiration → SSE 500)
_redis_pub: dict[str, StrictRedis] = {}
_redis_sub: dict[str, StrictRedis] = {}


def _get_redis_pub(redis_url: str) -> StrictRedis:
    if redis_url not in _redis_pub:
        _redis_pub[redis_url] = StrictRedis.from_url(
            redis_url,
            socket_connect_timeout=5,
        )
    return _redis_pub[redis_url]


def _get_redis_sub(redis_url: str) -> StrictRedis:
    """Client dédié au pub/sub : socket_timeout=None indispensable avec gevent."""
    if redis_url not in _redis_sub:
        _redis_sub[redis_url] = StrictRedis.from_url(
            redis_url,
            socket_timeout=None,        # bloque indéfiniment en attendant un message
            socket_connect_timeout=5,
        )
    return _redis_sub[redis_url]


class Message:
    """Données publiées comme Server-Sent Event."""

    def __init__(self, data, type=None, id=None, retry=None):
        self.data = data
        self.type = type
        self.id = id
        self.retry = retry

    def to_dict(self) -> dict:
        d = {"data": self.data}
        if self.type:
            d["type"] = self.type
        if self.id:
            d["id"] = self.id
        if self.retry:
            d["retry"] = self.retry
        return d

    def __str__(self) -> str:
        data = self.data if isinstance(self.data, str) else json.dumps(self.data)
        lines = [f"data:{line}" for line in data.splitlines()]
        if self.type:
            lines.insert(0, f"event:{self.type}")
        if self.id:
            lines.append(f"id:{self.id}")
        if self.retry:
            lines.append(f"retry:{self.retry}")
        return "\n".join(lines) + "\n\n"

    def __repr__(self) -> str:
        kwargs = OrderedDict()
        if self.type:
            kwargs["type"] = self.type
        if self.id:
            kwargs["id"] = self.id
        if self.retry:
            kwargs["retry"] = self.retry
        kwargs_repr = "".join(
            f", {key}={value!r}" for key, value in kwargs.items()
        )
        return f"Message({self.data!r}{kwargs_repr})"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, self.__class__)
            and self.data == other.data
            and self.type == other.type
            and self.id == other.id
            and self.retry == other.retry
        )


class ServerSentEventsBlueprint(Blueprint):
    """Blueprint Flask gérant la publication et le streaming de SSE."""

    def _redis_url(self) -> str:
        url = (
            current_app.config.get("SSE_REDIS_URL")
            or current_app.config.get("REDIS_URL")
        )
        if not url:
            raise KeyError("Définir SSE_REDIS_URL ou REDIS_URL dans la config Flask.")
        return url

    def publish(self, data, type=None, id=None, retry=None, channel='sse'):
        """
        Publie un événement sur un canal Redis.

        :param channel: Canal cible. Les clients connectés sur ce canal via
                        ``/stream?channel=<canal>`` reçoivent l'événement.
                        Défaut : ``'sse'`` (canal général).
        """
        message = Message(data, type=type, id=id, retry=retry)
        return _get_redis_pub(self._redis_url()).publish(
            channel=channel, message=json.dumps(message.to_dict())
        )

    def messages(self, channel='sse', heartbeat=15):
        """
        Générateur de :class:`Message` (ou ``None`` pour un battement de cœur)
        depuis un canal Redis pub/sub.

        Sans nouveau message pendant ``heartbeat`` secondes, produit ``None`` :
        l'appelant peut alors émettre un commentaire SSE keep-alive, faute de
        quoi les proxys intermédiaires (nginx hôte, etc.) finissent par couper
        les connexions inactives (proxy_read_timeout).

        ``get_message(timeout=...)`` interroge le socket via ``select`` sans
        changer son ``socket_timeout`` ; cela reste compatible avec le client
        sans timeout utilisé ici (cf. _get_redis_sub).
        """
        pubsub = _get_redis_sub(self._redis_url()).pubsub()
        pubsub.subscribe(channel)
        try:
            while True:
                pubsub_message = pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=heartbeat,
                )
                if pubsub_message is None:
                    yield None
                elif pubsub_message['type'] == 'message':
                    yield Message(**json.loads(pubsub_message['data']))
        finally:
            try:
                pubsub.unsubscribe(channel)
            except ConnectionError:
                pass

    def stream(self):
        """
        Vue Flask qui streame des SSE.
        Paramètre GET ``channel`` pour choisir le canal (défaut : ``'sse'``).
        """
        channel = request.args.get('channel') or 'sse'

        @stream_with_context
        def generator():
            for message in self.messages(channel=channel):
                yield ': heartbeat\n\n' if message is None else str(message)

        return current_app.response_class(
            generator(),
            mimetype='text/event-stream',
            headers={
                'X-Accel-Buffering': 'no',
                'Cache-Control': 'no-cache',
            },
        )


sse = ServerSentEventsBlueprint('sse', __name__)
sse.add_url_rule(rule="", endpoint="stream", view_func=sse.stream)
