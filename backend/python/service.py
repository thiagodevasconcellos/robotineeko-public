import uvicorn

try:
    from .config import build_service_config
except ImportError:
    from config import build_service_config


def main():
    config = build_service_config()

    uvicorn.run(
        'python.bridge:app',
        host=config['host'],
        port=config['port'],
        reload=config['reload'],
        log_level=config['log_level'],
    )


if __name__ == '__main__':
    main()
