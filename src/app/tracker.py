import asyncio
import logging
from urllib.parse import quote

from app.infrastructure.database.operations import OperationStore
from app.infrastructure.proxmox.client import ProxmoxClient
from app.services.resources import read_data
from app.settings import get_settings


logger = logging.getLogger(__name__)


class TaskTracker:
    def __init__(self, proxmox, operations, concurrency=4):
        self.proxmox, self.operations = proxmox, operations
        self.semaphore = asyncio.Semaphore(concurrency)

    async def run_once(self):
        pending = await self.operations.pending()
        await asyncio.gather(*(self._check(operation) for operation in pending))
        return len(pending)

    async def _check(self, operation):
        async with self.semaphore:
            try:
                task_node = quote(operation['task_node'], safe='')
                upid = quote(operation['upid'], safe='')
                task = await read_data(self.proxmox, f'/nodes/{task_node}/tasks/{upid}/status')
                if task.get('status') != 'stopped':
                    return
                success = task.get('exitstatus') == 'OK'
                if success:
                    target = quote(operation['selected_target'], safe='')
                    # Reconcile terminal success with actual VM visibility.
                    response = await self.proxmox.request('GET', f'/nodes/{target}/qemu/{operation["newid"]}/config')
                    if response.status_code != 200:
                        return
                await self.operations.finish(str(operation['id']), success)
                logger.info('clone_terminal operation=%s newid=%s success=%s', operation['id'], operation['newid'], success)
            except Exception as error:
                # A transient polling failure must not release reservations.
                logger.warning('task_poll_failed operation=%s error_type=%s', operation['id'], type(error).__name__)


async def main():
    settings = get_settings()
    if settings.tracker_interval_seconds <= 0 or settings.tracker_concurrency < 1:
        raise ValueError('Tracker interval and concurrency must be positive')
    operations = OperationStore(settings.database_dsn, settings.cluster_key)
    await operations.migrate()
    tracker = TaskTracker(ProxmoxClient(settings), operations, settings.tracker_concurrency)
    while True:
        try:
            await tracker.run_once()
        except Exception as error:
            logger.warning('tracker_cycle_failed error_type=%s', type(error).__name__)
        await asyncio.sleep(settings.tracker_interval_seconds)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    asyncio.run(main())
