from aiohttp import web
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily

CLIENT_CONNECTIONS = Gauge(
    'hpfeeds_broker_client_connections',
    'Number of clients connected to broker',
)

CONNECTION_MADE = Counter(
    'hpfeeds_broker_connection_made',
    'Number of connections established',
)

CONNECTION_READY = Counter(
    'hpfeeds_broker_connection_ready',
    'Number of connections established + authenticated',
    ['ident'],
)

CONNECTION_ERROR = Counter(
    'hpfeeds_broker_connection_error',
    'Number of connections that experienced a protocol error',
    ['ident', 'category'],
)

CONNECTION_LOST = Counter(
    'hpfeeds_broker_connection_lost',
    'Number of connections lost',
    ['ident'],
)

CLIENT_SEND_BUFFER_SIZE = Gauge(
    'hpfeeds_broker_connection_send_buffer_size',
    'Number of bytes queued for transmission',
    ['ident'],
)

CLIENT_RECEIVE_BUFFER_SIZE = Gauge(
    'hpfeeds_broker_connection_receive_buffer_size',
    'Number of bytes received but not yet parsed',
    ['ident'],
)

CLIENT_RECEIVE_BUFFER_FILL = Counter(
    'hpfeeds_broker_connection_receive_buffer_fill',
    'Number of bytes queued in the parsing buffer',
    ['ident'],
)

CLIENT_SEND_BUFFER_FILL = Counter(
    'hpfeeds_broker_connection_send_buffer_fill',
    'Number of bytes queued in the send buffer',
    ['ident'],
)

CLIENT_SEND_BUFFER_DRAIN = Counter(
    'hpfeeds_broker_connection_send_buffer_drain',
    'Number of bytes drained from the send buffer and sent',
    ['ident'],
)

CLIENT_SEND_BUFFER_DEADLINE_START = Counter(
    'hpfeeds_broker_connection_send_buffer_deadline_start',
    'High watermark was exceeded and this connection was put on a deadline timer',
    ['ident'],
)

CLIENT_SEND_BUFFER_DEADLINE_RECOVER = Counter(
    'hpfeeds_broker_connection_send_buffer_deadline_recover',
    'Buffer recovered to low watermark or better and deadline timer was cancelled',
    ['ident'],
)

SUBSCRIPTIONS = Gauge(
    'hpfeeds_broker_subscriptions',
    'Number of subscriptions to a channel',
    ['ident', 'chan'],
)

RECEIVE_PUBLISH_COUNT = Counter(
    'hpfeeds_broker_receive_publish_count',
    'Number of events received by broker for a channel',
    ['ident', 'chan'],
)

RECEIVE_PUBLISH_SIZE = Histogram(
    'hpfeeds_broker_receive_publish_size',
    'Sizes of messages received by broker for a channel',
    ['ident', 'chan'],
    buckets=[1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304],
)


def reset():
    ''' Reset the metrics to 0. This is intended for tests **only**. '''
    CLIENT_CONNECTIONS._value.set(0)
    SUBSCRIPTIONS._metrics = {}
    RECEIVE_PUBLISH_SIZE._metrics = {}
    RECEIVE_PUBLISH_COUNT._metrics = {}
    CLIENT_RECEIVE_BUFFER_FILL._metrics = {}
    CLIENT_SEND_BUFFER_FILL._metrics = {}
    CLIENT_SEND_BUFFER_DRAIN._metrics = {}
    CONNECTION_ERROR._metrics = {}
    CONNECTION_LOST._metrics = {}
    CONNECTION_MADE._value.set(0)
    CONNECTION_READY._metrics = {}


def collect_metrics(broker):
    CLIENT_SEND_BUFFER_SIZE._metrics = {}
    CLIENT_RECEIVE_BUFFER_SIZE._metrics = {}

    send_buffer_size = {}
    receive_buffer_size = {}
    for conn in broker.connections:
        if not conn.ak:
            continue
        send_buffer_size[conn.ak] = send_buffer_size.get(conn.ak, 0) + conn.transport.get_write_buffer_size()
        receive_buffer_size[conn.ak] = receive_buffer_size.get(conn.ak, 0) + len(conn.unpacker.buf)

    for ak in send_buffer_size.keys():
        CLIENT_SEND_BUFFER_SIZE.labels(ak).set(send_buffer_size[ak])
        CLIENT_RECEIVE_BUFFER_SIZE.labels(ak).set(receive_buffer_size[ak])


class CustomCollector:

    def __init__(self, server):
        self._server = server

    def collect(self):
        g = GaugeMetricFamily('hpfeeds_client_authenticated_connections', 'Authenticated connections', labels=['ident', 'owner'])
        metrics = {}
        for conn in self._server.connections:
            if not conn.ak:
                continue
            key = (conn.ak, conn.uid)
            metrics[key] = metrics.get(key, 0) + 1
        for (ident, owner), count in metrics.items():
            g.add_metric([ident, owner or ''], count)
        yield g


async def metrics(request):
    collect_metrics(request.app.broker)
    data = generate_latest(REGISTRY)
    return web.Response(text=data.decode('utf-8'), content_type='text/plain', charset='utf-8')


async def healthz(request):
    return web.Response(text='{}', content_type='application/json', charset='utf-8')


async def start_metrics_server(server, host, port):
    collector = CustomCollector(server)
    REGISTRY.register(collector)

    app = web.Application()
    app.broker = server

    app.router.add_get('/metrics', metrics)
    app.router.add_get('/healthz', healthz)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    site = web.TCPSite(runner, host, port)

    await site.start()

    async def close():
        try:
            await runner.cleanup()
        finally:
            REGISTRY.unregister(collector)

    return close
