#!/usr/bin/env bash

#/*******************************************************************************
# Copyright (c) 2012 IBM Corp.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#/*******************************************************************************

if [[ -f /home/cloud-user/cb_os_parameters.txt ]]
then
    source $(echo $0 | sed -e "s/\(.*\/\)*.*/\1.\//g")/cb_common.sh
else
    NC=`which netcat` 
    if [[ $? -ne 0 ]]
    then
        NC=`which nc`
    fi

    function syslog_netcat {
        echo "$1"
    }
        
    function linux_distribution {
        IS_UBUNTU=$(cat /etc/*release | grep -c "Ubuntu")

        if [[ ${IS_UBUNTU} -ge 1 ]]
        then
            export LINUX_DISTRO=1
        fi

        IS_REDHAT=$(cat /etc/*release | grep -c "Red Hat\|CentOS\|Fedora")    
        if [[ ${IS_REDHAT} -ge 1 ]]
        then
            export LINUX_DISTRO=2
        fi
    
        return ${LINUX_DISTRO}
    }

    function service_stop_disable {
        #1 - service list (space-separated list)

        if [[ -z ${LINUX_DISTRO} ]]
        then
            LINUX_DISTRO=$(linux_distribution)
        fi
    
        for s in $* ; do
            
            if [[ ${LINUX_DISTRO} -eq 1 ]]
            then
                syslog_netcat "Stopping service \"${s}\" on Ubuntu..."
                sudo service $s stop             
                sudo bash -c "echo 'manual' > /etc/init/$s.override" 
            fi
    
            if [[ ${LINUX_DISTRO} -eq 2 ]]
            then
                syslog_netcat "Stopping service \"${s}\" on Fedora/RHEL/CentOS..."
                sudo service $s stop                         
                sudo chkconfig $s off >/dev/null 2>&1
            fi
        done
        /bin/true
    }
fi

syslog_netcat "Checking connectivity between this VM and the orchestration node..."
if [[ -z ${CBONIP} ]]
then
    syslog_netcat "Please define the IP address of the Orchestration Node by setting the enviroment variable CBONIP"
    exit 1
fi

OSP=6379
syslog_netcat "Checking connection to Object Store running on ${CBONIP}, port $OSP ($NC -z $CBONIP $OSP)..."
$NC -z $CBONIP $OSP 

if [[ $? -eq 0 ]]
then
    syslog_netcat "OK"
else :
    syslog_netcat "FAILURE"
    exit 1
fi

LSP=5114
syslog_netcat "Checking connection to Log Store running on ${CBONIP}, port $LSP ($NC -z $CBONIP $LSP)..."
$NC -zu $CBONIP $LSP 

if [[ $? -eq 0 ]]
then
    syslog_netcat "OK"
else :
    syslog_netcat "FAILURE"
    exit 1
fi

MSP=27017 
syslog_netcat "Checking connection to Metric Store running on ${CBONIP}, port $MSP ($NC -z $CBONIP $MSP)..."
$NC -z $CBONIP $MSP 

if [[ $? -eq 0 ]]
then
    syslog_netcat "OK"
else :
    syslog_netcat "FAILURE"
    exit 1
fi
syslog_netcat "Done"

syslog_netcat "Removing /etc/udev/rules.d/70-persistent-net.rules..."
sudo mv /etc/udev/rules.d/70-persistent-net.rules ~
sudo touch /etc/udev/rules.d/70-persistent-net.rules
syslog_netcat "Done"

LINUX_DISTRO=$(linux_distribution)

syslog_netcat "Disabling services..."
SERVICES[1]="mongodb mysql redis-server"
SERVICES[2]="mongod mysqld redis"
service_stop_disable ${SERVICES[${LINUX_DISTRO}]}
syslog_netcat "Done"

syslog_netcat "Killing all CB-related processes..."
sudo pkill -9 -f cloud-api
sudo pkill -9 -f cloud-gui        
sudo pkill -9 -f ai-
sudo pkill -9 -f vm-
sudo pkill -9 -f submit-
sudo pkill -9 -f capture-
sudo pkill -9 -f -f gtkCBUI
sudo pkill -9 -f gmetad.py
sudo pkill -9 -f gmond
sudo pkill -9 -f rsyslog
sudo pkill -9 -f ntp
sudo pkill -9 -f redis
syslog_netcat "Done"

syslog_netcat "Removing all CB-related files..."
rm -rf ~/redis*
rm -rf ~/__init__.py 
rm -rf ~/barrier.py
rm -rf ~/scp2_python_proxy.rb
rm -rf ~/rsyslog.conf
rm -rf ~/scp_python_proxy.sh
rm -rf ~/monitor-core
rm -rf ~/util
rm -rf ~/standalone.sh; 
rm -rf ~/ai_mapping_file.txt
rm -rf ~/gmetad-vms.conf
rm -rf ~/gmond-vms.conf
rm -rf ~/ntp.conf
rm -rf ~/rsyslog.pid
rm -rf ~/logs
rm -rf ~/et*
rm -rf ~/cb_*
syslog_netcat "Done"

syslog_netcat "Adding all injected public ssh keys to $(whoami)'s autorized_keys file"
mkdir -p ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys
sudo bash -c "cat /root/.ssh/authorized_keys >> /home/$(whoami)/.ssh/authorized_keys"
chmod 600 ~/.ssh/authorized_keys
syslog_netcat "Done"

sync
sync
sync
sync
syslog_netcat "SUCCESS"
exit 0