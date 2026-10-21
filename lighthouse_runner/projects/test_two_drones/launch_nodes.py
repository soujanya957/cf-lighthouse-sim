import yaml
from ament_index_python.packages import get_package_share_directory
from crazyflie_py import Crazyswarm
import rclpy
import threading
import time
from rclpy.node import Node


class crazyflie_node(Node):
    def __init__(self, crazyswarm):
        super().__init__("crazyswarm_node")
        Hz = 20
        self.crazyswarm = crazyswarm
        self.timeHelper = crazyswarm.timeHelper
        self.timer = self.create_timer(
            1 / Hz,
            self.timer_callback,
        )

    def timer_callback(self):
        self.timeHelper.sleepForRate(50)
# Inject Imports Here:

def launch(nodes):
    # Allocate extra threads because worker callbacks can block for long durations.
    thread_count = max(8, len(nodes) * 2)
    executor = rclpy.executors.MultiThreadedExecutor(num_threads=thread_count)

    for node in nodes:
        executor.add_node(node)
    
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        while rclpy.ok():
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    rclpy.shutdown()
    thread.join()
    

def main():
    # node_config_file = get_package_share_directory('crazyflie'), 'config', 'crazyflies.yaml'
    
    # Initialize swarm
    swarm = Crazyswarm()
    with open("cfs_ordering.yaml") as f:
        ordering = yaml.safe_load(f) #TODO!!!
        order = ordering['cfs']
    
    crazyflies = [swarm.allcfs.crazyfliesById[int(k)] for k in order]
    counter = 0
    nodes = []
    cfs = []

    #   -----------Insert Nodes Here----------- 
    import Group1_node
    cfs = []
    cfs.append(crazyflies[0])
    nodes.append(Group1_node.worker_node(cfs, len(nodes), 2))


    import Group2_node
    cfs = []
    cfs.append(crazyflies[1])
    nodes.append(Group2_node.worker_node(cfs, len(nodes), 3))



    # nodes.append(crazyflie_node(swarm))
    nodes.append(swarm.allcfs)
    # Launch all nodes
    return launch(nodes)

if __name__ == '__main__':
    main()
