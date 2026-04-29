#!/bin/bash

source /environment.sh

# initialize launch file
dt-launchfile-init

# launch the web-controlled driving node (Flask UI + ROS wheel publisher)
rosrun my_package control_node.py

# wait for app to end
dt-launchfile-join
