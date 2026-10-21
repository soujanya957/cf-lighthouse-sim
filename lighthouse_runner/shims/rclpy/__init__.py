"""Stand-in for rclpy: just enough of it for the exported project's nodes, timers and executor."""

from lhrunner import runtime

from . import executors, node  # noqa: F401 (launch_nodes.py uses rclpy.executors without importing it)


def init(args=None, context=None):
    pass


def ok(context=None):
    return not runtime.stopping()


def shutdown(context=None):
    # launch_nodes.py calls this on Ctrl-C, and again once the run has stopped
    runtime.request_shutdown("interrupted")


def spin(node, executor=None):
    ex = executors.MultiThreadedExecutor()
    ex.add_node(node)
    ex.spin()


def spin_once(node, executor=None, timeout_sec=None):
    pass
