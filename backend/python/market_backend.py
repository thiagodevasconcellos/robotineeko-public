from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

try:
    from .bridge import build_history_state_payload
    from .chart_backend import build_chart_delta_payload
    from .services.auth_service import require_websocket_auth_or_close
    from .services.realtime_sync import realtime_sync
except ImportError:
    from bridge import build_history_state_payload
    from chart_backend import build_chart_delta_payload
    from services.auth_service import require_websocket_auth_or_close
    from services.realtime_sync import realtime_sync


router = APIRouter()
MARKET_CHANNEL_KEY = 'market:default'


def build_market_event_payload(
    event_type: str = 'market.snapshot',
    source: str = 'server',
    include_chart_delta: bool = False,
):
    payload = {
        'type': event_type,
        'source': source,
        **build_history_state_payload(),
    }

    if include_chart_delta:
        payload['chart_delta'] = build_chart_delta_payload(
            since_revision=None,
        )

    return payload


async def broadcast_market_event(event_type: str, source: str = 'server', include_chart_delta: bool = False):
    return await realtime_sync.broadcast(
        MARKET_CHANNEL_KEY,
        build_market_event_payload(
            event_type=event_type,
            source=source,
            include_chart_delta=include_chart_delta,
        ),
    )


@router.websocket('/ws/market')
async def market_websocket(
    websocket: WebSocket,
    source: str = Query(default='frontend'),
):
    auth_user = await require_websocket_auth_or_close(websocket)
    if not auth_user:
        return

    await websocket.accept()
    realtime_sync.subscribe(MARKET_CHANNEL_KEY, websocket)

    try:
        await websocket.send_json(
            build_market_event_payload(
                event_type='market.snapshot',
                source=source,
                include_chart_delta=False,
            )
        )

        while True:
            message = await websocket.receive_text()
            if message == 'ping':
                await websocket.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        realtime_sync.unsubscribe(MARKET_CHANNEL_KEY, websocket)
    except Exception:
        realtime_sync.unsubscribe(MARKET_CHANNEL_KEY, websocket)
