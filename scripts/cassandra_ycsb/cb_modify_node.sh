#!/usr/bin/env bash

#/*******************************************************************************
# This source code is provided as is, without any express or implied warranty.
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# @author: Joe Talerico - jtaleric@redhat.com
#/*******************************************************************************

source $(echo $0 | sed -e "s/\(.*\/\)*.*/\1.\//g")/cb_ycsb_common.sh

cd ~

SHORT_HOSTNAME=$(uname -n| cut -d "." -f 1)

#
# Determine remove or add 
#
# dbs=`echo $cassandra | wc -w`

sudo mkdir -p ${CASSANDRA_DATA_DIR}
sudo mkdir -p ${CASSANDRA_DATA_DIR}/store/cassandra/data
sudo mkdir -p ${CASSANDRA_DATA_DIR}/cassandra/commitlog 
sudo mkdir -p ${CASSANDRA_DATA_DIR}/cassandra/saved_caches
sudo chown -R cassandra:cassandra ${CASSANDRA_DATA_DIR}

#
# Determine current nodes 
#
nodes=`nodetool ring | grep -o '[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}'`
if [[ $? == 1 ]]
then 
    sudo service cassandra stop

    pos=1
    tk_pos=0

    #    sudo sh -c "echo $MY_IP cassandra >> /etc/hosts"
    for db in $cassandra_ips
    do
        if [[ $(cat /etc/hosts | grep -c "cassandra${pos} ") -eq 0 ]]
        then
            sudo sh -c "echo $db cassandra$pos cassandra-$pos >> /etc/hosts"
        fi

        if [[ $MY_IP = $db ]]
        then
            k_pos=$pos
        fi
        ((pos++))
    done

    token_pos=$(($tk_pos+2))
    token=$(token-generator $pos | sed -n ${token_pos}p)

#
# Update Cassandra Config
#
    sudo sed -i "s/initial_token:$/initial_token: ${token:10}/g" /etc/cassandra/conf/cassandra.yaml
    sudo sed -i "s/- seeds:.*$/- seeds: $seed_ip/g" /etc/cassandra/conf/cassandra.yaml
    sudo sed -i "s/listen_address:.*$/listen_address: ${MY_IP}/g" /etc/cassandra/conf/cassandra.yaml
    sudo sed -i 's/rpc_address:.*$/rpc_address: 0\.0\.0\.0/g' /etc/cassandra/conf/cassandra.yaml
        
    if [[ -f ${CASSANDRA_DATA_DIR} ]]
    then
        TEMP_CASSANDRA_DATA_DIR=$(echo ${CASSANDRA_DATA_DIR} | sed 's/\//_+-_-+/g')
        sudo sed -i "s/\/var\/lib\//${TEMP_CASSANDRA_DATA_DIR}\//g" /etc/cassandra/conf/cassandra.yaml
        sudo sed -i "s/_+-_-+/\//g" /etc/cassandra/conf/cassandra.yaml
    fi
    sudo sed -i "s/'Test Cluster'/'${my_ai_name}'/g" /etc/cassandra/conf/cassandra.yaml

#
# Remove possible old runs
#
    sudo rm -rf ${CASSANDRA_DATA_DIR}/cassandra/saved_caches/*
    sudo rm -rf ${CASSANDRA_DATA_DIR}/cassandra/data/system/*
    sudo rm -rf ${CASSANDRA_DATA_DIR}/cassandra/commitlog/*

#
# Start the database
#
    syslog_netcat "Starting cassandra on ${SHORT_HOSTNAME}"
    sudo service cassandra start

#
# Modify Nodes 
#
    pos=$dbs

#
# Still need to add the Remove node case, Cassandra should be easier to remove a node.
# Since in theory, there is no SPF in Cassandra.
#
    for db in $cassandra_ips
    do
        db_chk=`cat /etc/hosts | grep $db | awk '{ print $2 }'`
        if ! [[ "$nodes" =~ "$db_chk" ]]
        then 
            syslog_netcat " Adding the following node : cassandra$pos"
            ((pos++))
        fi
    done
else 
    exit 0
fi

if [[ $? -gt 0 ]]
then
    syslog_netcat "problem modifying nodes ${SHORT_HOSTNAME} - NOK"
    exit 1
fi
exit 0