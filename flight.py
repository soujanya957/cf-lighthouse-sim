import time
import cflib.crtp

from cflib.crazyflie import Crazyflie
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from cflib.crazyflie.log import LogConfig
from cflib.crazyflie.syncLogger import SyncLogger

# -----------------------------------------
# STUDENT TASK 0:
# Make sure your radio address from cfclient is filled in here!
# -----------------------------------------

URI = "radio:[your radio address from cfclient]"

POSITION_THRESHOLD = 0.001


def wait_for_position_estimator(cf):

    print("Waiting for Lighthouse estimator to stabilize...")

    log_config = LogConfig(name="Kalman", period_in_ms=500)

    log_config.add_variable("kalman.varPX", "float")
    log_config.add_variable("kalman.varPY", "float")
    log_config.add_variable("kalman.varPZ", "float")

    var_x_history = [1000] * 10
    var_y_history = [1000] * 10
    var_z_history = [1000] * 10

    with SyncLogger(cf, log_config) as logger:

        for log_entry in logger:

            data = log_entry[1]

            var_x_history.append(data["kalman.varPX"])
            var_x_history.pop(0)
            var_y_history.append(data["kalman.varPY"])
            var_y_history.pop(0)

            var_z_history.append(data["kalman.varPZ"])
            var_z_history.pop(0)

            min_x = min(var_x_history)
            max_x = max(var_x_history)

            min_y = min(var_y_history)
            max_y = max(var_y_history)

            min_z = min(var_z_history)
            max_z = max(var_z_history)

            if (
                (max_x - min_x) < POSITION_THRESHOLD
                and (max_y - min_y) < POSITION_THRESHOLD
                and (max_z - min_z) < POSITION_THRESHOLD
            ):
                break

    print("Estimator stabilized")


def reset_estimator(cf):

    cf.param.set_value("kalman.resetEstimation", "1")
    time.sleep(0.1)
    cf.param.set_value("kalman.resetEstimation", "0")

    wait_for_position_estimator(cf)


def enable_high_level_commander(cf):

    cf.param.set_value("commander.enHighLevel", "1")
    cf.param.set_value("stabilizer.estimator", "2")


def fly_one_meter_line(cf):

    commander = cf.high_level_commander

    altitude = 0.5

    print("Takeoff")

    # -----------------------------------------
    # STUDENT TASK 1:
    # Takeoff to altitude in 3 seconds
    # -----------------------------------------

    # Write your code for Task 1 between here...



    # ... and here

    time.sleep(4.0)
    print("Move to start point")

    # -----------------------------------------
    # STUDENT TASK 2:
    # Move drone to (1.0, 0.0, altitude)
    # -----------------------------------------

    # Write your code for Task 2 between here...



    # ... and here

    time.sleep(4.0)
    print("Fly 1 meter line")

    # -----------------------------------------
    # STUDENT TASK 3:
    # Fly from (1.0, 0.0, altitude) back to (0.0, 0.0, altitude)
    # -----------------------------------------

    # Write your code for Task 3 between here...



    # ... and here

    time.sleep(5.0)
    print("Hover")

    time.sleep(2.0)
    print("Land")

    # -----------------------------------------
    # STUDENT TASK 4:
    # Land the drone
    # -----------------------------------------

    # Write your code for Task 4 between here...



    # ... and here

    time.sleep(4.0)

    commander.stop()


def main():

    cflib.crtp.init_drivers()

    with SyncCrazyflie(URI, cf=Crazyflie(rw_cache="./cache")) as scf:

        cf = scf.cf

        enable_high_level_commander(cf)

        print("Arming")

        cf.platform.send_arming_request(True)
        time.sleep(1.0)

        reset_estimator(cf)

        fly_one_meter_line(cf)


if __name__ == "__main__":
    main()
