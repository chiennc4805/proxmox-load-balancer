from backend.app.interfaces import Algorithm

class Scheduler():

    def __init__(self, config: dict, algorithm_cls: Algorithm):
        ...

    def _filter(self):
        ...

    def _score(self):
        self.algorithm_cls