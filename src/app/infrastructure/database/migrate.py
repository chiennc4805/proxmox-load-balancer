import asyncio

from app.infrastructure.database.operations import OperationStore
from app.settings import get_settings


async def main():
    settings = get_settings()
    await OperationStore(settings.database_dsn, settings.cluster_key).migrate()
    print('Database schema is ready')


if __name__ == '__main__':
    asyncio.run(main())
