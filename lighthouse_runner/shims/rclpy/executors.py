from lhrunner import runtime


class MultiThreadedExecutor:
    """Starts the timers of its nodes and waits. When every node that has a `done` flag has set it, the run is
    finished."""

    def __init__(self, num_threads=None, context=None):
        self._nodes = []

    def add_node(self, node):
        self._nodes.append(node)
        return True

    def spin(self):
        for node in self._nodes:
            for timer in node._timers:
                timer.start()
        workers = [node for node in self._nodes if hasattr(node, "done") and node._timers]
        while not runtime.shutdown_event.wait(0.1):
            if workers and all(node.done for node in workers):
                runtime.request_shutdown("finished")

    def shutdown(self, timeout_sec=None):
        return True


SingleThreadedExecutor = MultiThreadedExecutor
