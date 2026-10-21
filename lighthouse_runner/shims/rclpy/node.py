import threading
import time
import traceback
from collections import defaultdict

from lhrunner import runtime

# In-process topics: topic name -> subscriptions
_topics = defaultdict(list)
_topics_lock = threading.Lock()


class _Time:
    def __init__(self, nanoseconds):
        self.nanoseconds = nanoseconds


class _Clock:
    def now(self):
        return _Time(int(runtime.clock.now() * 1e9))


class _Logger:
    def __init__(self, name):
        self.name = name

    def _log(self, level, msg):
        print(f"[{level}] [{self.name}]: {msg}", flush=True)

    def debug(self, msg, **kwargs):
        pass

    def info(self, msg, **kwargs):
        self._log("INFO", msg)

    def warning(self, msg, **kwargs):
        self._log("WARN", msg)

    warn = warning

    def error(self, msg, **kwargs):
        self._log("ERROR", msg)

    def fatal(self, msg, **kwargs):
        self._log("FATAL", msg)


class Timer:
    """Calls callback every period seconds in its own thread, once the node is added to a spinning executor.

    Callbacks of one node never overlap, like rclpy's default callback group. Two differences from rclpy, both so a
    show can't fly twice or hang in the air: a node whose callback leaves it with done = True isn't called again, and
    an exception in a callback stops the run instead of only the executor.
    """

    def __init__(self, node, period, callback):
        self.node = node
        self.period = period
        self.callback = callback
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"timer-{self.node.get_name()}", daemon=True)
            self._thread.start()

    def _run(self):
        next_time = time.monotonic() + self.period
        while not runtime.stopping():
            runtime.shutdown_event.wait(max(0.0, next_time - time.monotonic()))
            if runtime.stopping():
                return
            with self.node._callback_lock:
                try:
                    self.callback()
                except Exception:
                    traceback.print_exc()
                    runtime.request_shutdown(f"error in {self.node.get_name()}")
                    return
            if getattr(self.node, "done", False):
                return
            next_time = max(next_time + self.period, time.monotonic())

    def cancel(self):
        pass


class Publisher:
    def __init__(self, topic):
        self.topic = topic

    def publish(self, msg):
        with _topics_lock:
            subscriptions = list(_topics[self.topic])
        for subscription in subscriptions:
            # Own thread: a subscriber's timer callback may be busy running its blocks for the whole show
            threading.Thread(target=subscription.callback, args=(msg,), daemon=True).start()


class Subscription:
    def __init__(self, topic, callback):
        self.topic = topic
        self.callback = callback


class _Dummy:
    """Services, clients and anything else a node creates but this runner doesn't support"""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Node:
    def __init__(self, node_name, *args, **kwargs):
        self._name = node_name
        self._timers = []
        self._callback_lock = threading.RLock()
        self._logger = _Logger(node_name)

    def get_name(self):
        return self._name

    def get_clock(self):
        return _Clock()

    def get_logger(self):
        return self._logger

    def create_timer(self, timer_period_sec, callback, callback_group=None, clock=None, **kwargs):
        timer = Timer(self, timer_period_sec, callback)
        self._timers.append(timer)
        return timer

    def create_publisher(self, msg_type, topic, qos_profile=10, **kwargs):
        return Publisher(topic)

    def create_subscription(self, msg_type, topic, callback, qos_profile=10, **kwargs):
        subscription = Subscription(topic, callback)
        with _topics_lock:
            _topics[topic].append(subscription)
        return subscription

    def create_service(self, *args, **kwargs):
        return _Dummy()

    def create_client(self, *args, **kwargs):
        return _Dummy()

    def declare_parameter(self, name, value=None, *args, **kwargs):
        return _Dummy()

    def destroy_node(self):
        pass
