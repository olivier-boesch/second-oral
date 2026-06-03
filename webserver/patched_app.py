import gevent
import gevent.monkey

gevent.monkey.patch_all()  # noqa

from app import app

